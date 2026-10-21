"""Turn candles into a scored trade proposal.

Each check votes independently — direction, strength, weight — and the engine
sums those votes into a confluence score from 0 to 100. The score measures how
far the indicators agree with each other, nothing more. It is **not** a
probability of the trade winning, and must never be presented as one; the
calibration layer in `calibration.py` is what turns realised outcomes into an
actual hit rate.

Targets come from ATR rather than fixed percentages, so a stop sits outside the
instrument's normal noise instead of at an arbitrary distance, and they are
pushed past the nearest structure level when one is close by.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Optional, Sequence

from indicators import (
    Candle,
    atr,
    bollinger,
    bollinger_bandwidth,
    closes,
    ema,
    last_defined,
    macd,
    rsi,
    stochastic,
    support_resistance,
    trend_slope,
    volume_ratio,
)


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"

    @property
    def opposite(self) -> "Direction":
        if self is Direction.LONG:
            return Direction.SHORT
        if self is Direction.SHORT:
            return Direction.LONG
        return Direction.FLAT


class Regime(str, Enum):
    """Which family of checks is allowed to pick the direction.

    Mean reversion and trend following disagree by construction: in a falling
    market RSI sits oversold the whole way down, and reading that as "buy" is
    how a bot knife-catches a downtrend to zero. So the regime is decided first,
    and only the matching family votes on direction — in a trend the oscillators
    are demoted to timing the pullback, never to calling the turn.
    """

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"

    @property
    def direction(self) -> Direction:
        if self is Regime.TRENDING_UP:
            return Direction.LONG
        if self is Regime.TRENDING_DOWN:
            return Direction.SHORT
        return Direction.FLAT


@dataclass(frozen=True)
class Vote:
    """One check's opinion.

    ``strength`` is 0..1 — how strongly this check leans — and ``weight`` is how
    much the check counts relative to the others. A check with nothing to say
    votes FLAT, which contributes nothing in either direction but still counts
    against the total, so silence lowers confluence instead of being free.
    """

    name: str
    direction: Direction
    strength: float
    weight: float
    detail: str

    @property
    def signed(self) -> float:
        if self.direction is Direction.LONG:
            return self.strength * self.weight
        if self.direction is Direction.SHORT:
            return -self.strength * self.weight
        return 0.0


@dataclass(frozen=True)
class Readings:
    """Latest indicator values, for the UI and the audit trail."""

    price: float
    rsi: Optional[float]
    macd: Optional[float]
    macd_signal: Optional[float]
    macd_histogram: Optional[float]
    ema_fast: Optional[float]
    ema_slow: Optional[float]
    ema_trend: Optional[float]
    bb_upper: Optional[float]
    bb_middle: Optional[float]
    bb_lower: Optional[float]
    bb_bandwidth: Optional[float]
    atr: Optional[float]
    atr_percent: Optional[float]
    stoch_k: Optional[float]
    stoch_d: Optional[float]
    volume_ratio: Optional[float]
    trend_slope: Optional[float]
    nearest_support: Optional[float]
    nearest_resistance: Optional[float]
    regime: str


@dataclass(frozen=True)
class Proposal:
    """What the engine would do, and why."""

    symbol: str
    interval: str
    direction: Direction
    regime: Regime
    confluence: float
    entry: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    risk_reward: Optional[float]
    votes: list[Vote]
    readings: Readings
    blockers: list[str] = field(default_factory=list)
    # Share of the checks' weight that had a reading at all. Confluence is
    # agreement among those; this says how many of them there were.
    participation: float = 0.0

    @property
    def actionable(self) -> bool:
        """A proposal is actionable only with a direction, targets and no blocker."""
        return (
            self.direction is not Direction.FLAT
            and not self.blockers
            and self.stop_loss is not None
            and self.take_profit is not None
        )

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "direction": self.direction.value,
            "regime": self.regime.value,
            "confluence": round(self.confluence, 2),
            "participation": round(self.participation * 100, 2),
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": None if self.risk_reward is None else round(self.risk_reward, 3),
            "actionable": self.actionable,
            "blockers": list(self.blockers),
            "votes": [
                {
                    "name": v.name,
                    "direction": v.direction.value,
                    "strength": round(v.strength, 3),
                    "weight": v.weight,
                    "detail": v.detail,
                }
                for v in self.votes
            ],
            "readings": {
                k: (round(v, 6) if isinstance(v, float) else v)
                for k, v in asdict(self.readings).items()
            },
        }


@dataclass(frozen=True)
class StrategyConfig:
    """Knobs. The defaults are tuned for 5m-15m crypto scalping."""

    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    ema_fast: int = 9
    ema_slow: int = 21
    ema_trend: int = 50
    bb_period: int = 20
    atr_period: int = 14
    stoch_period: int = 14
    volume_period: int = 20

    # Targets
    atr_stop_multiple: float = 1.5
    atr_target_multiple: float = 2.5
    min_risk_reward: float = 1.3

    # Tradability guards
    min_atr_percent: float = 0.05   # below this the move cannot pay the fees
    max_atr_percent: float = 5.0    # above this the stop is a coin flip
    min_candles: int = 60
    # How much of the checks' weight must have a reading before the agreement
    # among them means anything. Two checks agreeing is not a confluence.
    min_participation: float = 0.45

    # How close to structure counts as "at the level", as a fraction of price.
    structure_proximity: float = 0.004

    # Regime detection. A slope past this (fraction of price per bar) with the
    # EMAs stacked the same way means "trending"; anything else is a range.
    trending_slope: float = 0.0004


# --------------------------------------------------------------- the checks --


def _vote_rsi(value: Optional[float], regime: Regime, config: StrategyConfig) -> Vote:
    """RSI reads as a reversal in a range and as pullback timing in a trend."""
    if value is None:
        return Vote("rsi", Direction.FLAT, 0.0, 1.6, "not enough history")

    trend = regime.direction
    if trend is not Direction.FLAT:
        # In a trend, only a pullback *towards* the trend is tradable. An
        # extreme against the trend is the trend itself, not a turn.
        if trend is Direction.LONG:
            if value <= 45.0:
                strength = min(1.0, (50.0 - value) / 25.0)
                return Vote("rsi", Direction.LONG, strength, 1.6,
                            f"pullback to {value:.1f} inside an uptrend")
            if value >= config.rsi_overbought:
                return Vote("rsi", Direction.FLAT, 0.0, 1.6,
                            f"stretched at {value:.1f}, waiting for a pullback")
            return Vote("rsi", Direction.LONG, 0.2, 1.6, f"holding up at {value:.1f}")
        if value >= 55.0:
            strength = min(1.0, (value - 50.0) / 25.0)
            return Vote("rsi", Direction.SHORT, strength, 1.6,
                        f"bounce to {value:.1f} inside a downtrend")
        if value <= config.rsi_oversold:
            return Vote("rsi", Direction.FLAT, 0.0, 1.6,
                        f"oversold at {value:.1f} but the trend is down, not buying it")
        return Vote("rsi", Direction.SHORT, 0.2, 1.6, f"weak at {value:.1f}")

    # Ranging: the classic mean-reversion reading.
    if value <= config.rsi_oversold:
        strength = min(1.0, (config.rsi_oversold - value) / 20.0 + 0.5)
        return Vote("rsi", Direction.LONG, strength, 1.6, f"oversold at {value:.1f} in a range")
    if value >= config.rsi_overbought:
        strength = min(1.0, (value - config.rsi_overbought) / 20.0 + 0.5)
        return Vote("rsi", Direction.SHORT, strength, 1.6, f"overbought at {value:.1f} in a range")
    if 45.0 <= value <= 55.0:
        return Vote("rsi", Direction.FLAT, 0.0, 1.6, f"neutral at {value:.1f}")
    if value < 45.0:
        return Vote("rsi", Direction.LONG, 0.25, 1.6, f"soft at {value:.1f}")
    return Vote("rsi", Direction.SHORT, 0.25, 1.6, f"firm at {value:.1f}")


def _vote_macd(histogram: Optional[float], previous: Optional[float]) -> Vote:
    if histogram is None:
        return Vote("macd", Direction.FLAT, 0.0, 1.3, "not enough history")
    crossed = previous is not None and (previous <= 0 < histogram or previous >= 0 > histogram)
    # A fresh crossover is the strong case; an established sign is a weak one.
    strength = 0.9 if crossed else 0.45
    if histogram > 0:
        label = "bullish crossover" if crossed else "histogram positive"
        return Vote("macd", Direction.LONG, strength, 1.3, label)
    if histogram < 0:
        label = "bearish crossover" if crossed else "histogram negative"
        return Vote("macd", Direction.SHORT, strength, 1.3, label)
    return Vote("macd", Direction.FLAT, 0.0, 1.3, "flat")


def _vote_ema(
    price: float,
    fast: Optional[float],
    slow: Optional[float],
    trend: Optional[float],
) -> Vote:
    if fast is None or slow is None:
        return Vote("ema", Direction.FLAT, 0.0, 1.4, "not enough history")
    stacked_up = fast > slow and (trend is None or slow > trend)
    stacked_down = fast < slow and (trend is None or slow < trend)
    if stacked_up and price > fast:
        return Vote("ema", Direction.LONG, 0.9, 1.4, "price above a rising stack")
    if stacked_down and price < fast:
        return Vote("ema", Direction.SHORT, 0.9, 1.4, "price below a falling stack")
    if fast > slow:
        return Vote("ema", Direction.LONG, 0.4, 1.4, "fast above slow")
    if fast < slow:
        return Vote("ema", Direction.SHORT, 0.4, 1.4, "fast below slow")
    return Vote("ema", Direction.FLAT, 0.0, 1.4, "entangled")


def _vote_bollinger(
    price: float,
    upper: Optional[float],
    lower: Optional[float],
    middle: Optional[float],
    regime: Regime,
) -> Vote:
    """Band touches mean-revert in a range; in a trend they mark continuation."""
    if upper is None or lower is None or middle is None:
        return Vote("bollinger", Direction.FLAT, 0.0, 1.1, "not enough history")
    span = upper - lower
    if span <= 0:
        return Vote("bollinger", Direction.FLAT, 0.0, 1.1, "bands collapsed")
    position = (price - lower) / span

    trend = regime.direction
    if trend is not Direction.FLAT:
        # Riding the band is strength, not exhaustion. Fading it is how you get
        # run over, so the counter-trend touch withholds instead of opposing.
        if trend is Direction.LONG:
            if position >= 0.8:
                return Vote("bollinger", Direction.LONG, 0.5, 1.1, "riding the upper band")
            if position <= 0.4:
                return Vote("bollinger", Direction.LONG, 0.8, 1.1,
                            "pulled back to the lower half of the channel")
            return Vote("bollinger", Direction.LONG, 0.3, 1.1, "mid channel, trend up")
        if position <= 0.2:
            return Vote("bollinger", Direction.SHORT, 0.5, 1.1, "riding the lower band")
        if position >= 0.6:
            return Vote("bollinger", Direction.SHORT, 0.8, 1.1,
                        "bounced into the upper half of the channel")
        return Vote("bollinger", Direction.SHORT, 0.3, 1.1, "mid channel, trend down")

    if price <= lower:
        return Vote("bollinger", Direction.LONG, 0.85, 1.1, "at or below the lower band")
    if price >= upper:
        return Vote("bollinger", Direction.SHORT, 0.85, 1.1, "at or above the upper band")
    if position < 0.3:
        return Vote("bollinger", Direction.LONG, 0.4, 1.1, "lower third of the channel")
    if position > 0.7:
        return Vote("bollinger", Direction.SHORT, 0.4, 1.1, "upper third of the channel")
    return Vote("bollinger", Direction.FLAT, 0.0, 1.1, "mid channel")


def _vote_stochastic(k: Optional[float], d: Optional[float], regime: Regime) -> Vote:
    if k is None or d is None:
        return Vote("stochastic", Direction.FLAT, 0.0, 1.0, "not enough history")

    trend = regime.direction
    if trend is not Direction.FLAT:
        if trend is Direction.LONG:
            if k < 40:
                return Vote("stochastic", Direction.LONG, 0.8, 1.0,
                            f"dipped to {k:.1f} inside an uptrend")
            if k > 80:
                return Vote("stochastic", Direction.FLAT, 0.0, 1.0,
                            f"stretched at {k:.1f}, no entry here")
            return Vote("stochastic", Direction.LONG, 0.25, 1.0, f"mid range at {k:.1f}")
        if k > 60:
            return Vote("stochastic", Direction.SHORT, 0.8, 1.0,
                        f"bounced to {k:.1f} inside a downtrend")
        if k < 20:
            return Vote("stochastic", Direction.FLAT, 0.0, 1.0,
                        f"oversold at {k:.1f} but the trend is down")
        return Vote("stochastic", Direction.SHORT, 0.25, 1.0, f"mid range at {k:.1f}")

    if k < 20 and k > d:
        return Vote("stochastic", Direction.LONG, 0.85, 1.0, f"turning up from {k:.1f}")
    if k > 80 and k < d:
        return Vote("stochastic", Direction.SHORT, 0.85, 1.0, f"turning down from {k:.1f}")
    if k < 20:
        return Vote("stochastic", Direction.LONG, 0.4, 1.0, f"oversold at {k:.1f}")
    if k > 80:
        return Vote("stochastic", Direction.SHORT, 0.4, 1.0, f"overbought at {k:.1f}")
    return Vote("stochastic", Direction.FLAT, 0.0, 1.0, f"mid range at {k:.1f}")


def _vote_trend(slope: Optional[float]) -> Vote:
    if slope is None:
        return Vote("trend", Direction.FLAT, 0.0, 1.2, "not enough history")
    # 0.1% of price per bar is already a decisive slope on a scalping timeframe.
    strength = min(1.0, abs(slope) / 0.001)
    if slope > 0:
        return Vote("trend", Direction.LONG, strength, 1.2, f"rising {slope * 100:.3f}%/bar")
    if slope < 0:
        return Vote("trend", Direction.SHORT, strength, 1.2, f"falling {slope * 100:.3f}%/bar")
    return Vote("trend", Direction.FLAT, 0.0, 1.2, "flat")


def _vote_volume(ratio: Optional[float], bias: Direction) -> Vote:
    """Volume never picks a side — it confirms or withholds confirmation."""
    if ratio is None:
        return Vote("volume", Direction.FLAT, 0.0, 0.9, "not enough history")
    if bias is Direction.FLAT:
        return Vote("volume", Direction.FLAT, 0.0, 0.9, f"{ratio:.2f}x average, no bias to confirm")
    if ratio >= 1.5:
        return Vote("volume", bias, 0.9, 0.9, f"{ratio:.2f}x average confirms the move")
    if ratio >= 1.0:
        return Vote("volume", bias, 0.45, 0.9, f"{ratio:.2f}x average")
    return Vote("volume", Direction.FLAT, 0.0, 0.9, f"thin at {ratio:.2f}x average")


def _vote_structure(
    price: float,
    support: Optional[float],
    resistance: Optional[float],
    config: StrategyConfig,
) -> Vote:
    near = config.structure_proximity * price
    at_support = support is not None and abs(price - support) <= near
    at_resistance = resistance is not None and abs(price - resistance) <= near
    if at_support and not at_resistance:
        return Vote("structure", Direction.LONG, 0.8, 1.2, f"at support {support:.6g}")
    if at_resistance and not at_support:
        return Vote("structure", Direction.SHORT, 0.8, 1.2, f"at resistance {resistance:.6g}")
    if at_support and at_resistance:
        return Vote("structure", Direction.FLAT, 0.0, 1.2, "squeezed between levels")
    return Vote("structure", Direction.FLAT, 0.0, 1.2, "no level nearby")


# Which family each check belongs to, and how much that family counts in each
# regime. The gating in the votes above decides *which way* a check may lean;
# this decides *how loudly*. Trend followers lead a trend, mean reverters lead
# a range, and the losing family is damped rather than silenced so a genuine
# disagreement still costs confluence.
VOTE_FAMILY = {
    "rsi": "reversion",
    "stochastic": "reversion",
    "bollinger": "reversion",
    "structure": "reversion",
    "ema": "trend",
    "macd": "trend",
    "trend": "trend",
    "volume": "confirmation",
}

FAMILY_WEIGHT = {
    Regime.TRENDING_UP: {"trend": 1.0, "reversion": 0.6, "confirmation": 1.0},
    Regime.TRENDING_DOWN: {"trend": 1.0, "reversion": 0.6, "confirmation": 1.0},
    Regime.RANGING: {"trend": 0.45, "reversion": 1.0, "confirmation": 1.0},
}


def apply_regime_weights(votes: list[Vote], regime: Regime) -> list[Vote]:
    """Scale each vote's weight by how much its family counts in this regime."""
    scale = FAMILY_WEIGHT[regime]
    return [
        replace(vote, weight=vote.weight * scale[VOTE_FAMILY[vote.name]])
        for vote in votes
    ]


def detect_regime(
    slope: Optional[float],
    ema_fast: Optional[float],
    ema_slow: Optional[float],
    ema_trend: Optional[float],
    config: StrategyConfig,
) -> Regime:
    """Trend only when the slope and the EMA stack agree.

    Requiring both keeps a single sharp bar from being read as a trend, and
    keeps a drifting market with tangled EMAs classified as the range it is.
    """
    if slope is None or ema_fast is None or ema_slow is None:
        return Regime.RANGING

    stacked_up = ema_fast > ema_slow and (ema_trend is None or ema_slow > ema_trend)
    stacked_down = ema_fast < ema_slow and (ema_trend is None or ema_slow < ema_trend)

    if slope >= config.trending_slope and stacked_up:
        return Regime.TRENDING_UP
    if slope <= -config.trending_slope and stacked_down:
        return Regime.TRENDING_DOWN
    return Regime.RANGING


# ---------------------------------------------------------------- the engine --


def _nearest(levels, price: float, below: bool) -> Optional[float]:
    candidates = [lv.price for lv in levels if (lv.price < price if below else lv.price > price)]
    if not candidates:
        return None
    return max(candidates) if below else min(candidates)


def analyse(
    symbol: str,
    interval: str,
    candles: Sequence[Candle],
    config: Optional[StrategyConfig] = None,
) -> Proposal:
    """Score the current bar and propose a trade.

    The last candle is treated as the live one: its close is the entry price.
    """
    config = config or StrategyConfig()

    if len(candles) < config.min_candles:
        empty = Readings(
            price=candles[-1].close if candles else 0.0,
            rsi=None, macd=None, macd_signal=None, macd_histogram=None,
            ema_fast=None, ema_slow=None, ema_trend=None,
            bb_upper=None, bb_middle=None, bb_lower=None, bb_bandwidth=None,
            atr=None, atr_percent=None, stoch_k=None, stoch_d=None,
            volume_ratio=None, trend_slope=None,
            nearest_support=None, nearest_resistance=None,
            regime=Regime.RANGING.value,
        )
        return Proposal(
            symbol=symbol,
            interval=interval,
            direction=Direction.FLAT,
            regime=Regime.RANGING,
            confluence=0.0,
            entry=empty.price,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
            votes=[],
            readings=empty,
            blockers=[f"need at least {config.min_candles} candles, got {len(candles)}"],
        )

    price_series = closes(candles)
    price = price_series[-1]

    macd_series = macd(price_series)
    bands = bollinger(price_series, config.bb_period)
    stoch = stochastic(candles, config.stoch_period)
    atr_series = atr(candles, config.atr_period)
    atr_value = last_defined(atr_series)
    atr_percent = None if atr_value is None or price == 0 else atr_value / price * 100.0

    histogram = last_defined(macd_series.histogram)
    defined_histogram = [v for v in macd_series.histogram if v is not None]
    previous_histogram = defined_histogram[-2] if len(defined_histogram) >= 2 else None

    resistance_levels, support_levels = support_resistance(candles)
    nearest_support = _nearest(support_levels, price, below=True)
    nearest_resistance = _nearest(resistance_levels, price, below=False)

    regime = detect_regime(
        trend_slope(price_series),
        last_defined(ema(price_series, config.ema_fast)),
        last_defined(ema(price_series, config.ema_slow)),
        last_defined(ema(price_series, config.ema_trend)),
        config,
    )

    readings = Readings(
        price=price,
        rsi=last_defined(rsi(price_series, config.rsi_period)),
        macd=last_defined(macd_series.macd),
        macd_signal=last_defined(macd_series.signal),
        macd_histogram=histogram,
        ema_fast=last_defined(ema(price_series, config.ema_fast)),
        ema_slow=last_defined(ema(price_series, config.ema_slow)),
        ema_trend=last_defined(ema(price_series, config.ema_trend)),
        bb_upper=last_defined(bands.upper),
        bb_middle=last_defined(bands.middle),
        bb_lower=last_defined(bands.lower),
        bb_bandwidth=last_defined(bollinger_bandwidth(bands)),
        atr=atr_value,
        atr_percent=atr_percent,
        stoch_k=last_defined(stoch.k),
        stoch_d=last_defined(stoch.d),
        volume_ratio=last_defined(volume_ratio(candles, config.volume_period)),
        trend_slope=trend_slope(price_series),
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        regime=regime.value,
    )

    # Directional checks first; volume then confirms whichever way they lean.
    votes = [
        _vote_rsi(readings.rsi, regime, config),
        _vote_macd(histogram, previous_histogram),
        _vote_ema(price, readings.ema_fast, readings.ema_slow, readings.ema_trend),
        _vote_bollinger(
            price, readings.bb_upper, readings.bb_lower, readings.bb_middle, regime
        ),
        _vote_stochastic(readings.stoch_k, readings.stoch_d, regime),
        _vote_trend(readings.trend_slope),
        _vote_structure(price, nearest_support, nearest_resistance, config),
    ]
    provisional = sum(v.signed for v in votes)
    bias = Direction.LONG if provisional > 0 else Direction.SHORT if provisional < 0 else Direction.FLAT
    votes.append(_vote_volume(readings.volume_ratio, bias))
    votes = apply_regime_weights(votes, regime)

    total_weight = sum(v.weight for v in votes)
    voted_weight = sum(v.weight for v in votes if v.direction is not Direction.FLAT)
    net = sum(v.signed for v in votes)
    # Confluence is agreement among the checks that actually have a reading.
    #
    # It used to divide by the whole board, which sounded stricter and was in
    # fact unreachable: in a trend the mean-reversion family is structurally
    # quiet, so a textbook setup topped out near 50 and a "only trade above 90"
    # rule meant "never trade". Silence is still not free — it shows up as
    # participation, which is policed as a blocker just below — but it no
    # longer makes the headline number uninterpretable.
    confluence = abs(net) / voted_weight * 100.0 if voted_weight else 0.0
    participation = voted_weight / total_weight if total_weight else 0.0
    direction = Direction.LONG if net > 0 else Direction.SHORT if net < 0 else Direction.FLAT

    blockers: list[str] = []
    if participation < config.min_participation:
        blockers.append(
            f"only {participation * 100:.0f}% of the checks have a reading, "
            f"need {config.min_participation * 100:.0f}%"
        )
    if regime.direction is not Direction.FLAT and direction is regime.direction.opposite:
        # The checks net out against the prevailing trend. That is a fade, and
        # fading a trend is exactly the trade this engine refuses to take.
        blockers.append(f"counter-trend: {direction.value} against a {regime.value} market")

    if atr_percent is None:
        blockers.append("no ATR reading, cannot size a stop")
    elif atr_percent < config.min_atr_percent:
        blockers.append(
            f"volatility too low ({atr_percent:.3f}% ATR < {config.min_atr_percent}%)"
        )
    elif atr_percent > config.max_atr_percent:
        blockers.append(
            f"volatility too high ({atr_percent:.2f}% ATR > {config.max_atr_percent}%)"
        )

    stop_loss, take_profit, risk_reward = _targets(
        price, direction, atr_value, nearest_support, nearest_resistance, config
    )
    if direction is not Direction.FLAT and risk_reward is not None:
        if risk_reward < config.min_risk_reward:
            blockers.append(
                f"risk/reward {risk_reward:.2f} below the {config.min_risk_reward} floor"
            )

    return Proposal(
        symbol=symbol,
        interval=interval,
        direction=direction,
        regime=regime,
        confluence=confluence,
        entry=price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=risk_reward,
        votes=votes,
        readings=readings,
        blockers=blockers,
        participation=participation,
    )


def _targets(
    price: float,
    direction: Direction,
    atr_value: Optional[float],
    support: Optional[float],
    resistance: Optional[float],
    config: StrategyConfig,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Stop and target from ATR, nudged past the structure that would hit first.

    A stop parked exactly on support is the stop that gets taken out on the wick
    that holds the level, so it goes a little beyond.
    """
    if direction is Direction.FLAT or atr_value is None or atr_value <= 0:
        return None, None, None

    stop_distance = atr_value * config.atr_stop_multiple
    target_distance = atr_value * config.atr_target_multiple

    if direction is Direction.LONG:
        stop = price - stop_distance
        if support is not None and support < price:
            stop = min(stop, support - atr_value * 0.25)
        target = price + target_distance
        if resistance is not None and resistance > price:
            # Don't aim through a wall: take the near side of it.
            target = min(target, resistance - atr_value * 0.1)
    else:
        stop = price + stop_distance
        if resistance is not None and resistance > price:
            stop = max(stop, resistance + atr_value * 0.25)
        target = price - target_distance
        if support is not None and support < price:
            target = max(target, support + atr_value * 0.1)

    risk = abs(price - stop)
    reward = abs(target - price)
    if risk <= 0:
        return stop, target, None
    return stop, target, reward / risk
