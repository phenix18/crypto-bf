"""Technical indicators over OHLCV candles.

Pure functions on plain lists — no pandas, no network, no state — so the
strategy engine stays testable without a market feed. Every function takes
oldest-first candles and returns a series aligned to that input, padding the
warm-up period with ``None`` rather than silently shortening the series: a
caller that lines an indicator up against candles by index must not have the
two drift apart.

Conventions:
- A candle is a ``Candle``: open time in ms, OHLC, volume.
- Series return ``list[float | None]``, same length as the input.
- Anything that cannot be computed (not enough history) is ``None``, never 0.0,
  so "no reading" is never mistaken for "a reading of zero".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

Number = Optional[float]


@dataclass(frozen=True)
class Candle:
    """One OHLCV bar. ``open_time`` is epoch milliseconds, as exchanges report."""

    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_binance(cls, row: Sequence) -> "Candle":
        """Build from a Binance /api/v3/klines row.

        Binance returns arrays shaped
        ``[openTime, open, high, low, close, volume, closeTime, ...]`` with the
        prices as strings.
        """
        return cls(
            open_time=int(row[0]),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )


def closes(candles: Sequence[Candle]) -> list[float]:
    return [c.close for c in candles]


def _pad(values: list[float], length: int) -> list[Number]:
    """Left-pad a computed series with None so it aligns with the candles."""
    missing = length - len(values)
    if missing < 0:
        raise ValueError("series is longer than the candle input")
    return [None] * missing + list(values)


# ---------------------------------------------------------------- averages --


def sma(values: Sequence[float], period: int) -> list[Number]:
    """Simple moving average."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return [None] * len(values)

    out: list[float] = []
    window = sum(values[:period])
    out.append(window / period)
    for index in range(period, len(values)):
        window += values[index] - values[index - period]
        out.append(window / period)
    return _pad(out, len(values))


def ema(values: Sequence[float], period: int) -> list[Number]:
    """Exponential moving average, seeded with the SMA of the first period."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return [None] * len(values)

    multiplier = 2.0 / (period + 1.0)
    current = sum(values[:period]) / period
    out = [current]
    for value in values[period:]:
        current = (value - current) * multiplier + current
        out.append(current)
    return _pad(out, len(values))


# ------------------------------------------------------------------- trend --


@dataclass(frozen=True)
class MacdSeries:
    macd: list[Number]
    signal: list[Number]
    histogram: list[Number]


def macd(
    values: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> MacdSeries:
    """MACD line, signal line, and histogram.

    The signal EMA is computed over the MACD line's defined region only, then
    realigned, so the warm-up does not leak zeros into the early bars.
    """
    if fast >= slow:
        raise ValueError("fast period must be shorter than slow period")

    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    macd_line: list[Number] = [
        None if f is None or s is None else f - s for f, s in zip(fast_ema, slow_ema)
    ]

    defined = [v for v in macd_line if v is not None]
    if len(defined) < signal_period:
        empty: list[Number] = [None] * len(values)
        return MacdSeries(macd=macd_line, signal=empty, histogram=list(empty))

    signal_defined = ema(defined, signal_period)
    signal_line: list[Number] = _pad(
        [v for v in signal_defined if v is not None],
        len(values),
    )
    histogram: list[Number] = [
        None if m is None or s is None else m - s for m, s in zip(macd_line, signal_line)
    ]
    return MacdSeries(macd=macd_line, signal=signal_line, histogram=histogram)


# ----------------------------------------------------------------- momentum --


def rsi(values: Sequence[float], period: int = 14) -> list[Number]:
    """Wilder's RSI.

    Wilder smoothing, not a plain average of the last `period` changes — the
    two diverge quickly and every charting platform uses Wilder's.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) <= period:
        return [None] * len(values)

    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def to_rsi(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        rs = gain / loss
        return 100.0 - (100.0 / (1.0 + rs))

    out = [to_rsi(avg_gain, avg_loss)]
    for index in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period
        out.append(to_rsi(avg_gain, avg_loss))
    return _pad(out, len(values))


@dataclass(frozen=True)
class StochasticSeries:
    k: list[Number]
    d: list[Number]


def stochastic(
    candles: Sequence[Candle],
    period: int = 14,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> StochasticSeries:
    """Stochastic oscillator (%K smoothed, %D the average of %K)."""
    if len(candles) < period:
        empty: list[Number] = [None] * len(candles)
        return StochasticSeries(k=empty, d=list(empty))

    raw: list[float] = []
    for index in range(period - 1, len(candles)):
        window = candles[index - period + 1 : index + 1]
        highest = max(c.high for c in window)
        lowest = min(c.low for c in window)
        span = highest - lowest
        # A perfectly flat window has no range; 50 is the neutral reading.
        raw.append(50.0 if span == 0 else (candles[index].close - lowest) / span * 100.0)

    k_smoothed = [v for v in sma(raw, smooth_k) if v is not None]
    d_smoothed = [v for v in sma(k_smoothed, smooth_d) if v is not None]
    return StochasticSeries(
        k=_pad(k_smoothed, len(candles)),
        d=_pad(d_smoothed, len(candles)),
    )


# ---------------------------------------------------------------- volatility --


def true_range(candles: Sequence[Candle]) -> list[Number]:
    """True range: the first bar has no previous close, so it is None."""
    if not candles:
        return []
    out: list[Number] = [None]
    for previous, current in zip(candles, candles[1:]):
        out.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> list[Number]:
    """Average true range, Wilder-smoothed. The unit is price, not percent."""
    ranges = [v for v in true_range(candles) if v is not None]
    if len(ranges) < period:
        return [None] * len(candles)

    current = sum(ranges[:period]) / period
    out = [current]
    for value in ranges[period:]:
        current = (current * (period - 1) + value) / period
        out.append(current)
    return _pad(out, len(candles))


@dataclass(frozen=True)
class BollingerSeries:
    upper: list[Number]
    middle: list[Number]
    lower: list[Number]


def bollinger(
    values: Sequence[float],
    period: int = 20,
    deviations: float = 2.0,
) -> BollingerSeries:
    """Bollinger bands around an SMA."""
    middle = sma(values, period)
    upper: list[Number] = []
    lower: list[Number] = []
    for index, mid in enumerate(middle):
        if mid is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[index - period + 1 : index + 1]
        variance = sum((v - mid) ** 2 for v in window) / period
        spread = deviations * (variance ** 0.5)
        upper.append(mid + spread)
        lower.append(mid - spread)
    return BollingerSeries(upper=upper, middle=middle, lower=lower)


def bollinger_bandwidth(bands: BollingerSeries) -> list[Number]:
    """Band width relative to the middle band — a squeeze detector."""
    out: list[Number] = []
    for upper, middle, lower in zip(bands.upper, bands.middle, bands.lower):
        if upper is None or middle is None or lower is None or middle == 0:
            out.append(None)
        else:
            out.append((upper - lower) / middle)
    return out


# ------------------------------------------------------------------- volume --


def volume_ratio(candles: Sequence[Candle], period: int = 20) -> list[Number]:
    """Current volume over its moving average. Above 1.0 means unusual activity."""
    averages = sma([c.volume for c in candles], period)
    out: list[Number] = []
    for candle, average in zip(candles, averages):
        out.append(None if average in (None, 0) else candle.volume / average)
    return out


# ---------------------------------------------------------------- structure --


@dataclass(frozen=True)
class Level:
    """A support or resistance level and how many pivots formed it."""

    price: float
    touches: int


def _pivots(candles: Sequence[Candle], span: int, high: bool) -> list[float]:
    """Bars that are a strict local extreme over +/- ``span`` neighbours.

    Strict on purpose. Accepting ties makes every bar of a flat stretch a pivot,
    and a long quiet range then outvotes the real swing highs around it — the
    level that matters gets buried under the noise it should stand out from.
    """
    found: list[float] = []
    for index in range(span, len(candles) - span):
        price = candles[index].high if high else candles[index].low
        neighbours = [
            (c.high if high else c.low)
            for offset, c in enumerate(candles[index - span : index + span + 1])
            if offset != span
        ]
        if high and price > max(neighbours):
            found.append(price)
        elif not high and price < min(neighbours):
            found.append(price)
    return found


def support_resistance(
    candles: Sequence[Candle],
    span: int = 3,
    tolerance: float = 0.002,
) -> tuple[list[Level], list[Level]]:
    """Cluster pivot highs and lows into resistance and support levels.

    ``tolerance`` is the fraction of price within which two pivots count as the
    same level (0.002 = 0.2%). Levels come back strongest first.
    """
    def cluster(prices: list[float]) -> list[Level]:
        levels: list[list[float]] = []
        for price in sorted(prices):
            if levels and abs(price - levels[-1][0]) / max(levels[-1][0], 1e-9) <= tolerance:
                levels[-1].append(price)
            else:
                levels.append([price])
        grouped = [Level(price=sum(g) / len(g), touches=len(g)) for g in levels]
        return sorted(grouped, key=lambda lv: (-lv.touches, lv.price))

    return cluster(_pivots(candles, span, high=True)), cluster(
        _pivots(candles, span, high=False)
    )


def trend_slope(values: Sequence[float], period: int = 20) -> Number:
    """Least-squares slope of the last `period` values, normalized by the mean.

    Returned as a fraction per bar, so it compares across instruments: 0.001
    means the fit rises a tenth of a percent of price per bar.
    """
    if len(values) < period or period < 2:
        return None
    window = list(values[-period:])
    n = len(window)
    mean_x = (n - 1) / 2.0
    mean_y = sum(window) / n
    numerator = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(window))
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    if denominator == 0 or mean_y == 0:
        return None
    return (numerator / denominator) / mean_y


def last_defined(series: Iterable[Number]) -> Number:
    """The most recent non-None reading of a series, or None if there is none."""
    latest: Number = None
    for value in series:
        if value is not None:
            latest = value
    return latest
