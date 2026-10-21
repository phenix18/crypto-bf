"""Where candles come from.

Sources sit behind one small protocol so the engine never knows whether it is
reading Binance, a recorded session, or a generated fixture. That boundary is
what makes the strategy testable end to end without a market feed, and it is
also what lets a live run be rehearsed on recorded data before any money is at
risk.

Nothing here decides anything. Fetch, validate, hand back candles — the moment
a source starts interpreting prices, replaying a session stops reproducing the
original run.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol, Sequence

from indicators import Candle

# Binance publishes these; anything else is a typo we would rather catch here
# than have rejected after a round trip.
VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}

MAINNET_BASE = "https://api.binance.com"
TESTNET_BASE = "https://testnet.binance.vision"

# Milliseconds per interval, for sources that have to place candles in time
# themselves rather than being told when they happened.
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000,
    "1w": 604_800_000, "1M": 2_592_000_000,
}


class MarketDataError(RuntimeError):
    """A source could not produce usable candles."""


class CandleSource(Protocol):
    """Anything that can hand back oldest-first OHLCV candles."""

    name: str

    def fetch(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        ...


# ------------------------------------------------------------------ replay --


@dataclass
class ReplaySource:
    """Candles from memory: recorded sessions, fixtures, or generated series.

    The whole pipeline runs against this with no network at all, which is how
    the strategy, risk and execution layers get tested here. It is also the
    honest way to rehearse a configuration: same code path, known data.
    """

    series: dict[tuple[str, str], list[Candle]] = field(default_factory=dict)
    name: str = "replay"
    # Advance one candle per fetch, so a loop sees the market move.
    cursor: dict[tuple[str, str], int] = field(default_factory=dict)
    streaming: bool = False

    def add(self, symbol: str, interval: str, candles: Sequence[Candle]) -> None:
        self.series[(symbol.upper(), interval)] = list(candles)

    def fetch(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        key = (symbol.upper(), interval)
        candles = self.series.get(key)
        if not candles:
            raise MarketDataError(f"no replay data for {symbol} {interval}")
        limit = max(1, int(limit))

        if not self.streaming:
            return list(candles[-limit:])

        # Streaming mode walks forward one bar at a time, so a backtest and a
        # live loop exercise the same "latest candle is the live one" logic.
        position = self.cursor.get(key, min(limit, len(candles)))
        if position > len(candles):
            # Past the end. Raising is the whole point: clamping instead would
            # hand the same final window back forever, and a backtest loop
            # would keep "finding" setups on data it has already traded.
            raise MarketDataError(f"replay for {symbol} {interval} is exhausted")
        window = candles[max(0, position - limit) : position]
        self.cursor[key] = position + 1
        return list(window)

    def reset(self) -> None:
        self.cursor.clear()


# --------------------------------------------------------------- synthetic --


@dataclass
class SyntheticSource:
    """Plausible candles from a seed. For demonstrations, not for decisions.

    There is one honest reason for this to exist: the interface, the pipeline
    and the paper broker can all be shown working where there is no market
    access at all. It is a source like any other, so nothing downstream needs
    a special case — and because ``name`` travels with every response, a screen
    fed by this can say so rather than passing invented prices off as the
    market.

    The series is a seeded random walk, so the same symbol always produces the
    same history; the window advances with the clock, so a screen watching it
    sees the market move.
    """

    seed: int = 7
    bars: int = 2_000
    drift: float = 0.00004
    volatility: float = 0.0035
    name: str = "synthetic"
    _cache: dict[tuple[str, str], list[Candle]] = field(default_factory=dict)
    clock: Callable[[], float] = time.time

    def _series(self, symbol: str, interval: str) -> list[Candle]:
        key = (symbol.upper(), interval)
        if key in self._cache:
            return self._cache[key]

        step = INTERVAL_MS.get(interval, 300_000)
        # A per-symbol seed, so BTC and ETH do not print identical charts.
        rng = random.Random(f"{self.seed}:{key[0]}:{interval}")
        price = 50.0 + (int(hashlib.sha256(key[0].encode()).hexdigest()[:8], 16) % 60_000) / 10.0
        candles: list[Candle] = []
        for index in range(self.bars):
            drift = self.drift * math.sin(index / 180.0) * 6.0
            close = max(0.01, price * (1 + drift + rng.gauss(0.0, self.volatility)))
            high = max(price, close) * (1 + abs(rng.gauss(0.0, self.volatility / 2)))
            low = min(price, close) * (1 - abs(rng.gauss(0.0, self.volatility / 2)))
            candles.append(
                Candle(
                    open_time=index * step,
                    open=price,
                    high=high,
                    low=low,
                    close=close,
                    volume=round(500 + rng.random() * 1500, 4),
                )
            )
            price = close
        self._cache[key] = candles
        return candles

    def fetch(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        if interval not in VALID_INTERVALS:
            raise MarketDataError(f"{interval!r} is not an interval this source knows")
        limit = max(1, min(int(limit), self.bars))
        candles = self._series(symbol, interval)

        step = INTERVAL_MS.get(interval, 300_000)
        elapsed_bars = int(self.clock() * 1000 // step)
        span = max(1, self.bars - limit)
        end = limit + elapsed_bars % span
        window = candles[end - limit : end]

        # Restamp so the newest candle is the current bar: a chart drawn from
        # this should show now, not 1970.
        latest_open = (elapsed_bars - 1) * step
        shift = latest_open - window[-1].open_time
        return [
            Candle(
                open_time=c.open_time + shift,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
            )
            for c in window
        ]


# ----------------------------------------------------------------- binance --


@dataclass
class BinanceSource:
    """Public market data from Binance.

    Klines need no API key — this reads the public endpoint only, and never
    signs anything. Order placement lives in the execution layer, where the
    credentials are, so a misconfiguration here cannot send an order.
    """

    testnet: bool = True
    timeout: float = 10.0
    max_retries: int = 2
    backoff_seconds: float = 0.4
    name: str = "binance"
    _opener: Optional[object] = None

    @property
    def base_url(self) -> str:
        return TESTNET_BASE if self.testnet else MAINNET_BASE

    def fetch(self, symbol: str, interval: str, limit: int) -> list[Candle]:
        if interval not in VALID_INTERVALS:
            raise MarketDataError(
                f"{interval!r} is not a Binance interval; expected one of "
                f"{sorted(VALID_INTERVALS)}"
            )
        # Binance caps a single klines call at 1000.
        limit = max(1, min(int(limit), 1000))
        query = urllib.parse.urlencode(
            {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        )
        url = f"{self.base_url}/api/v3/klines?{query}"
        payload = self._get_json(url)

        if not isinstance(payload, list):
            raise MarketDataError(f"unexpected klines payload for {symbol}: {payload!r}")
        try:
            candles = [Candle.from_binance(row) for row in payload]
        except (IndexError, TypeError, ValueError) as exc:
            raise MarketDataError(f"malformed kline row for {symbol}: {exc}") from exc
        return validate(candles, symbol, interval)

    def _get_json(self, url: str):
        last: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                request = urllib.request.Request(
                    url, headers={"Accept": "application/json", "User-Agent": "ai-trader"}
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:200]
                # 4xx is our mistake — a bad symbol, a bad interval, a ban.
                # Retrying will not fix it and burns the rate limit.
                if 400 <= exc.code < 500:
                    raise MarketDataError(f"Binance rejected the request ({exc.code}): {body}")
                last = MarketDataError(f"Binance error {exc.code}: {body}")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = MarketDataError(f"could not reach Binance: {exc}")

            if attempt < self.max_retries:
                time.sleep(self.backoff_seconds * (2 ** attempt))

        raise last or MarketDataError("Binance request failed")


# ------------------------------------------------------------ coinmarketcap --


@dataclass
class CoinMarketCapQuotes:
    """Spot quotes and market context. Not a candle source.

    CoinMarketCap gives breadth — market cap, dominance, 24h move across
    thousands of assets — which is useful for choosing *what* to look at. It is
    not granular enough to trade from, so it never feeds the strategy engine.
    """

    api_key: str
    base_url: str = "https://pro-api.coinmarketcap.com"
    timeout: float = 10.0
    name: str = "coinmarketcap"

    def quotes(self, symbols: Sequence[str], convert: str = "USD") -> dict[str, dict]:
        if not self.api_key:
            raise MarketDataError("CoinMarketCap needs an API key (CMC_API_KEY)")
        if not symbols:
            return {}
        query = urllib.parse.urlencode(
            {"symbol": ",".join(s.upper() for s in symbols), "convert": convert}
        )
        url = f"{self.base_url}/v1/cryptocurrency/quotes/latest?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "X-CMC_PRO_API_KEY": self.api_key,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            raise MarketDataError(f"CoinMarketCap error {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise MarketDataError(f"could not reach CoinMarketCap: {exc}") from exc

        data = payload.get("data")
        if not isinstance(data, dict):
            raise MarketDataError(f"unexpected CoinMarketCap payload: {payload!r}")
        return {symbol: parse_cmc_entry(entry, convert) for symbol, entry in data.items()}


def parse_cmc_entry(entry, convert: str = "USD") -> dict:
    """Flatten one CoinMarketCap asset into the fields worth keeping."""
    if isinstance(entry, list):  # some plans return a list per symbol
        entry = entry[0] if entry else {}
    quote = (entry.get("quote") or {}).get(convert) or {}
    return {
        "name": entry.get("name"),
        "symbol": entry.get("symbol"),
        "price": quote.get("price"),
        "percent_change_1h": quote.get("percent_change_1h"),
        "percent_change_24h": quote.get("percent_change_24h"),
        "percent_change_7d": quote.get("percent_change_7d"),
        "volume_24h": quote.get("volume_24h"),
        "market_cap": quote.get("market_cap"),
    }


# ---------------------------------------------------------------- validation --


def validate(candles: Sequence[Candle], symbol: str, interval: str) -> list[Candle]:
    """Reject candles the strategy must never see.

    A NaN price or an inverted high/low does not raise anywhere downstream — it
    quietly poisons every indicator and produces a confident, meaningless
    signal. Catching it at the boundary is the only place it stays cheap.
    """
    if not candles:
        raise MarketDataError(f"no candles returned for {symbol} {interval}")

    for index, candle in enumerate(candles):
        values = (candle.open, candle.high, candle.low, candle.close)
        if any(math.isnan(v) or math.isinf(v) for v in values):
            raise MarketDataError(f"{symbol} {interval} candle {index} has a non-finite price")
        if any(v <= 0 for v in values):
            raise MarketDataError(f"{symbol} {interval} candle {index} has a non-positive price")
        if candle.high < candle.low:
            raise MarketDataError(
                f"{symbol} {interval} candle {index} has high {candle.high} below low {candle.low}"
            )
        if not (candle.low <= candle.open <= candle.high):
            raise MarketDataError(f"{symbol} {interval} candle {index} opens outside its range")
        if not (candle.low <= candle.close <= candle.high):
            raise MarketDataError(f"{symbol} {interval} candle {index} closes outside its range")
        if candle.volume < 0:
            raise MarketDataError(f"{symbol} {interval} candle {index} has negative volume")

    for previous, current in zip(candles, candles[1:]):
        if current.open_time <= previous.open_time:
            raise MarketDataError(
                f"{symbol} {interval} candles are not in ascending time order"
            )

    return list(candles)


def build_source(
    *,
    provider: Optional[str] = None,
    testnet: Optional[bool] = None,
) -> CandleSource:
    """Pick a source from the environment.

    Defaults to Binance testnet. Going to mainnet takes a deliberate
    `BINANCE_TESTNET=false`, so no amount of forgetting to set something ends
    up pointing at the live book by accident.
    """
    provider = (provider or os.getenv("MARKET_DATA_PROVIDER", "binance")).strip().lower()
    if provider == "replay":
        return ReplaySource()
    if provider == "synthetic":
        return SyntheticSource()
    if provider != "binance":
        raise MarketDataError(f"unknown market data provider {provider!r}")

    if testnet is None:
        testnet = os.getenv("BINANCE_TESTNET", "true").strip().lower() not in {
            "false", "0", "no", "off",
        }
    return BinanceSource(testnet=testnet)
