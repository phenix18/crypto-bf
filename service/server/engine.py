"""The single trading engine the API and the loop share.

One engine, one portfolio, one audit trail, built once per process. Two of
those would be worse than none: two engines means two position books, each
sizing trades as though the other's exposure did not exist.

Everything that touches state takes a lock. FastAPI runs synchronous handlers
in a thread pool, so two requests arriving together are genuinely concurrent —
and the thing they would race on here is order placement.

Defaults are read once, at build time, and they are the timid ones: paper
broker, Binance testnet for data, auto-trading off. Turning any of it up is a
deliberate change to the environment, never a side effect of calling an
endpoint.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import calibration
import indicators
from auto_trader import AutoTrader, Portfolio
from broker import Broker, build_broker
from llm_client import build_llm_client
from market_data import CandleSource, build_source
from strategy_engine import StrategyConfig, analyse


class AutoTradeDisabled(RuntimeError):
    """An order was asked for while auto-trading is switched off."""


def _flag(name: str, *, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in {"false", "0", "no", "off"}:
        return False
    if raw in {"true", "1", "yes", "on"}:
        return True
    return default


def _float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class EngineConfig:
    watchlist: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    interval: str = "5m"
    # Whether orders may be placed at all. Off means the whole pipeline still
    # runs and reports — which is the useful part while a configuration is
    # being judged — but nothing is sent anywhere.
    auto_trade: bool = False
    equity: float = 10_000.0
    candle_limit: int = 300

    @classmethod
    def from_env(cls) -> "EngineConfig":
        raw = (os.getenv("WATCHLIST") or "").strip()
        watchlist = tuple(s.strip().upper() for s in raw.split(",") if s.strip())
        return cls(
            watchlist=watchlist or cls.watchlist,
            interval=(os.getenv("AUTO_TRADE_INTERVAL") or cls.interval).strip(),
            auto_trade=_flag("AUTO_TRADE_ENABLED", default=False),
            equity=_float("PAPER_EQUITY", cls.equity),
            candle_limit=int(_float("CANDLE_LIMIT", cls.candle_limit)),
        )


@dataclass
class Engine:
    trader: AutoTrader
    config: EngineConfig
    lock: threading.RLock = field(default_factory=threading.RLock)

    # -- reads -------------------------------------------------------------

    @property
    def source_name(self) -> str:
        return getattr(self.trader.source, "name", "unknown")

    def mode(self) -> dict:
        """What this process can actually do, in the plainest terms available."""
        broker = self.trader.broker
        return {
            "data_source": self.source_name,
            "broker": getattr(broker, "name", "unknown"),
            "paper": getattr(broker, "name", "") == "paper",
            "testnet": bool(getattr(broker, "testnet", True)),
            "live": bool(getattr(broker, "is_live", False)),
            "auto_trade": self.config.auto_trade,
            "model": getattr(self.trader.llm_client, "name", "none"),
            "model_available": bool(getattr(self.trader.llm_client, "available", False)),
        }

    def status(self) -> dict:
        with self.lock:
            return {
                "mode": self.mode(),
                "watchlist": list(self.config.watchlist),
                "interval": self.config.interval,
                "portfolio": self.trader.portfolio.to_dict(),
                "closed_trades": [t.to_dict() for t in self.trader.portfolio.closed[-20:]],
                "history_size": len(self.trader.history),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

    def chart(self, symbol: str, interval: Optional[str] = None, limit: int = 0) -> dict:
        """Candles, every indicator series, the levels, and the current proposal.

        One call, because the alternative is a chart whose overlays were
        computed from a different window than its candles — the kind of drift
        that makes a screen quietly lie about where the stop is.
        """
        interval = interval or self.config.interval
        limit = limit or self.config.candle_limit
        candles = self.trader.source.fetch(symbol, interval, limit)
        config = self.trader.strategy_config
        proposal = analyse(symbol, interval, candles, config)

        closes = indicators.closes(candles)
        macd = indicators.macd(closes)
        bands = indicators.bollinger(closes, config.bb_period)
        stoch = indicators.stochastic(candles, config.stoch_period)
        support, resistance = indicators.support_resistance(candles)

        return {
            "symbol": symbol.upper(),
            "interval": interval,
            "source": self.source_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "candles": [
                {
                    "t": c.open_time,
                    "o": c.open,
                    "h": c.high,
                    "l": c.low,
                    "c": c.close,
                    "v": c.volume,
                }
                for c in candles
            ],
            "indicators": {
                "rsi": indicators.rsi(closes, config.rsi_period),
                "ema_fast": indicators.ema(closes, config.ema_fast),
                "ema_slow": indicators.ema(closes, config.ema_slow),
                "ema_trend": indicators.ema(closes, config.ema_trend),
                "macd": macd.macd,
                "macd_signal": macd.signal,
                "macd_histogram": macd.histogram,
                "bb_upper": bands.upper,
                "bb_middle": bands.middle,
                "bb_lower": bands.lower,
                "atr": indicators.atr(candles, config.atr_period),
                "stoch_k": stoch.k,
                "stoch_d": stoch.d,
                "volume_ratio": indicators.volume_ratio(candles, config.volume_period),
            },
            "levels": {
                "support": [{"price": level.price, "touches": level.touches} for level in support],
                "resistance": [
                    {"price": level.price, "touches": level.touches} for level in resistance
                ],
            },
            "proposal": proposal.to_dict(),
        }

    def calibration(self) -> dict:
        with self.lock:
            stats = calibration.summarise_all(self.trader.history)
            config = self.trader.calibration_config
        return {
            "buckets": [bucket.to_dict() for bucket in stats],
            "sample_size": len(stats),
            "config": {
                "min_sample": config.min_sample,
                "min_hit_rate": round(config.min_hit_rate * 100, 2),
                "min_expectancy_r": config.min_expectancy_r,
                "confluence_gate": config.confluence_gate,
            },
        }

    def audit(self, limit: int = 100) -> dict:
        with self.lock:
            entries = self.trader.audit[-max(1, min(limit, 500)) :]
        return {"entries": list(reversed(entries)), "total": len(self.trader.audit)}

    # -- decisions ---------------------------------------------------------

    def analyse(self, symbol: str, interval: Optional[str] = None) -> dict:
        """Run every gate and report. Places nothing, whatever the verdict."""
        interval = interval or self.config.interval
        with self.lock:
            return self.trader.evaluate(symbol, interval).to_dict()

    def trade(self, symbol: str, interval: Optional[str] = None) -> dict:
        interval = interval or self.config.interval
        if not self.config.auto_trade:
            raise AutoTradeDisabled(
                "auto-trading is off; set AUTO_TRADE_ENABLED=true to let the "
                "engine place orders"
            )
        with self.lock:
            return self.trader.trade(symbol, interval).to_dict()

    def sweep(self) -> list[dict]:
        """One pass over the watchlist. Reports either way; trades only if armed."""
        with self.lock:
            if not self.config.auto_trade:
                return [
                    self.trader.evaluate(symbol, self.config.interval).to_dict()
                    for symbol in self.config.watchlist
                ]
            decisions = self.trader.run_once(list(self.config.watchlist), self.config.interval)
            return [d.to_dict() for d in decisions]

    # -- controls ----------------------------------------------------------

    def set_kill_switch(self, engaged: bool) -> dict:
        """Engage or clear the halt. Clearing is a human decision, by design."""
        with self.lock:
            self.trader.portfolio.kill_switch = bool(engaged)
            self.trader._note(
                {
                    "kind": "kill_switch",
                    "engaged": bool(engaged),
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return self.trader.portfolio.to_dict()

    def close(self, symbol: str, price: float) -> Optional[dict]:
        with self.lock:
            trade = self.trader.close_now(symbol, price)
            return None if trade is None else trade.to_dict()


def build_engine(
    config: Optional[EngineConfig] = None,
    *,
    source: Optional[CandleSource] = None,
    broker: Optional[Broker] = None,
) -> Engine:
    config = config or EngineConfig.from_env()
    source = source or build_source()
    broker = broker or build_broker()
    trader = AutoTrader(
        source=source,
        broker=broker,
        portfolio=Portfolio(equity=config.equity),
        strategy_config=StrategyConfig(),
        llm_client=build_llm_client(),
        candle_limit=config.candle_limit,
    )
    return Engine(trader=trader, config=config)


_LOCK = threading.Lock()
_ENGINE: Optional[Engine] = None


def get_engine() -> Engine:
    """The process's engine, built on first use."""
    global _ENGINE
    with _LOCK:
        if _ENGINE is None:
            _ENGINE = build_engine()
        return _ENGINE


def reset_engine(engine: Optional[Engine] = None) -> Optional[Engine]:
    """Replace the process's engine. For tests, and for nothing else."""
    global _ENGINE
    with _LOCK:
        _ENGINE = engine
        return _ENGINE
