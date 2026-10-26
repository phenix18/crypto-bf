"""Indicator correctness.

Running without raising is not the same as being right, so the RSI case pins
the first two readings of Wilder's worked example to values derived by hand
from the definition rather than to a reproduction of someone's table: several
widely copied versions of that table do not match the closes printed above
them, and an expected value taken from the implementation under test proves
nothing.
"""

import math
import os
import sys
import unittest

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from indicators import (  # noqa: E402
    Candle,
    atr,
    bollinger,
    bollinger_bandwidth,
    ema,
    last_defined,
    macd,
    rsi,
    sma,
    stochastic,
    support_resistance,
    trend_slope,
    true_range,
    volume_ratio,
)

# Wilder's worked example.
WILDER_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
    46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
    43.42, 42.66, 43.13,
]
# Derived by hand from Wilder's definition over the closes above.
#
#   first 14 changes: gains 3.34, losses 1.40
#   avg gain 3.34/14 = 0.2385714, avg loss 1.40/14 = 0.1000000
#   RS 2.3857143 -> RSI 100 - 100/3.3857143 = 70.4641
#
#   next change -0.28, Wilder-smoothed:
#   avg gain (0.2385714*13)/14 = 0.2215306
#   avg loss (0.1000000*13 + 0.28)/14 = 0.1128571
#   RS 1.9629  -> RSI 66.2496
WILDER_FIRST_RSI = 70.4641
WILDER_SECOND_RSI = 66.2496


def candle(index: int, high: float, low: float, close: float, volume: float = 100.0) -> Candle:
    return Candle(
        open_time=index * 60_000,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


class AlignmentTests(unittest.TestCase):
    """Every series must line up with its input index for index."""

    def test_series_keep_the_input_length(self) -> None:
        values = [float(v) for v in range(50)]
        candles = [candle(i, v + 1, v - 1, v) for i, v in enumerate(values)]
        self.assertEqual(len(sma(values, 10)), len(values))
        self.assertEqual(len(ema(values, 10)), len(values))
        self.assertEqual(len(rsi(values, 14)), len(values))
        self.assertEqual(len(atr(candles, 14)), len(candles))
        self.assertEqual(len(true_range(candles)), len(candles))
        self.assertEqual(len(volume_ratio(candles, 20)), len(candles))
        self.assertEqual(len(bollinger(values, 20).upper), len(values))
        self.assertEqual(len(macd(values).histogram), len(values))
        self.assertEqual(len(stochastic(candles, 14).k), len(candles))

    def test_warmup_is_none_not_zero(self) -> None:
        values = [float(v) for v in range(30)]
        series = sma(values, 10)
        self.assertTrue(all(v is None for v in series[:9]))
        self.assertIsNotNone(series[9])

    def test_too_little_history_is_all_none(self) -> None:
        self.assertEqual(sma([1.0, 2.0], 10), [None, None])
        self.assertEqual(rsi([1.0, 2.0], 14), [None, None])


class MovingAverageTests(unittest.TestCase):
    def test_sma_matches_hand_computation(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(sma(values, 3), [None, None, 2.0, 3.0, 4.0])

    def test_sma_of_a_flat_series_is_the_value(self) -> None:
        self.assertEqual(sma([7.0] * 5, 3)[-1], 7.0)

    def test_ema_is_seeded_with_the_sma(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        series = ema(values, 3)
        self.assertAlmostEqual(series[2], 2.0)  # SMA of 1,2,3
        # Then 4 and 5 pull it up with multiplier 2/(3+1) = 0.5.
        self.assertAlmostEqual(series[3], 3.0)
        self.assertAlmostEqual(series[4], 4.0)


class RsiTests(unittest.TestCase):
    def test_matches_wilders_worked_example(self) -> None:
        series = rsi(WILDER_CLOSES, 14)
        defined = [v for v in series if v is not None]
        # One reading per close past the 14-change warm-up.
        self.assertEqual(len(defined), len(WILDER_CLOSES) - 14)
        self.assertAlmostEqual(defined[0], WILDER_FIRST_RSI, delta=0.001)
        self.assertAlmostEqual(defined[1], WILDER_SECOND_RSI, delta=0.001)

    def test_stays_inside_the_zero_hundred_band(self) -> None:
        for value in rsi(WILDER_CLOSES, 14):
            if value is not None:
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 100.0)

    def test_monotonic_rise_pins_to_100(self) -> None:
        self.assertAlmostEqual(last_defined(rsi([float(v) for v in range(1, 40)], 14)), 100.0)

    def test_monotonic_fall_pins_to_zero(self) -> None:
        self.assertAlmostEqual(last_defined(rsi([float(v) for v in range(40, 1, -1)], 14)), 0.0)

    def test_flat_series_is_neutral(self) -> None:
        # No gains and no losses: neither overbought nor oversold.
        self.assertAlmostEqual(last_defined(rsi([10.0] * 40, 14)), 50.0)


class MacdTests(unittest.TestCase):
    def test_histogram_is_macd_minus_signal(self) -> None:
        values = [float(v) for v in range(1, 80)]
        series = macd(values)
        for m, s, h in zip(series.macd, series.signal, series.histogram):
            if m is None or s is None:
                self.assertIsNone(h)
            else:
                self.assertAlmostEqual(h, m - s)

    def test_rising_series_gives_a_positive_macd(self) -> None:
        values = [float(v) for v in range(1, 80)]
        self.assertGreater(last_defined(macd(values).macd), 0)

    def test_falling_series_gives_a_negative_macd(self) -> None:
        values = [float(v) for v in range(80, 1, -1)]
        self.assertLess(last_defined(macd(values).macd), 0)

    def test_fast_must_be_shorter_than_slow(self) -> None:
        with self.assertRaises(ValueError):
            macd([1.0] * 50, fast=26, slow=12)


class VolatilityTests(unittest.TestCase):
    def test_true_range_uses_the_previous_close(self) -> None:
        candles = [candle(0, 10, 9, 9.5), candle(1, 12, 11, 11.5)]
        ranges = true_range(candles)
        self.assertIsNone(ranges[0])
        # high-low = 1, |high - prev close| = 2.5, |low - prev close| = 1.5
        self.assertAlmostEqual(ranges[1], 2.5)

    def test_atr_of_constant_range_is_that_range(self) -> None:
        candles = [candle(i, 101.0, 99.0, 100.0) for i in range(40)]
        self.assertAlmostEqual(last_defined(atr(candles, 14)), 2.0, delta=0.01)

    def test_bollinger_bands_straddle_the_middle(self) -> None:
        values = [100.0 + (v % 7) for v in range(60)]
        bands = bollinger(values, 20)
        upper, middle, lower = bands.upper[-1], bands.middle[-1], bands.lower[-1]
        self.assertGreater(upper, middle)
        self.assertGreater(middle, lower)

    def test_flat_series_has_zero_bandwidth(self) -> None:
        bands = bollinger([50.0] * 40, 20)
        self.assertAlmostEqual(last_defined(bollinger_bandwidth(bands)), 0.0)


class StochasticTests(unittest.TestCase):
    def test_close_at_the_window_high_reads_100(self) -> None:
        candles = [candle(i, 100.0, 90.0, 95.0) for i in range(20)]
        candles += [candle(20 + i, 100.0, 90.0, 100.0) for i in range(5)]
        self.assertAlmostEqual(last_defined(stochastic(candles, 14).k), 100.0, delta=0.01)

    def test_flat_window_is_neutral(self) -> None:
        candles = [candle(i, 100.0, 100.0, 100.0) for i in range(30)]
        self.assertAlmostEqual(last_defined(stochastic(candles, 14).k), 50.0)


class VolumeTests(unittest.TestCase):
    def test_ratio_is_one_on_constant_volume(self) -> None:
        candles = [candle(i, 101, 99, 100, volume=500.0) for i in range(40)]
        self.assertAlmostEqual(last_defined(volume_ratio(candles, 20)), 1.0)

    def test_a_volume_spike_shows_above_one(self) -> None:
        candles = [candle(i, 101, 99, 100, volume=500.0) for i in range(40)]
        candles.append(candle(40, 101, 99, 100, volume=2500.0))
        self.assertGreater(last_defined(volume_ratio(candles, 20)), 2.0)


class StructureTests(unittest.TestCase):
    def test_repeated_highs_cluster_into_one_resistance(self) -> None:
        candles = []
        for i in range(30):
            # A sawtooth that tops out at 110 three times against a 104 ceiling.
            high = 110.0 if i % 10 == 5 else 104.0
            candles.append(candle(i, high, 100.0, 102.0))
        resistance, _ = support_resistance(candles, span=2)
        self.assertTrue(resistance)
        self.assertAlmostEqual(resistance[0].price, 110.0, delta=0.5)
        self.assertGreaterEqual(resistance[0].touches, 2)

    def test_a_flat_stretch_produces_no_pivots(self) -> None:
        # Every bar ties its neighbours. Counting those as pivots would bury
        # the real swing highs under a cluster of noise.
        flat = [candle(i, 100.0, 99.0, 99.5) for i in range(30)]
        resistance, support = support_resistance(flat, span=2)
        self.assertEqual(resistance, [])
        self.assertEqual(support, [])

    def test_a_single_spike_beats_a_long_plateau(self) -> None:
        candles = [candle(i, 104.0, 100.0, 102.0) for i in range(25)]
        candles[12] = candle(12, 115.0, 100.0, 102.0)
        resistance, _ = support_resistance(candles, span=2)
        self.assertTrue(resistance)
        self.assertAlmostEqual(resistance[0].price, 115.0, delta=0.5)

    def test_slope_is_positive_on_an_uptrend(self) -> None:
        self.assertGreater(trend_slope([float(v) for v in range(1, 40)], 20), 0)

    def test_slope_is_negative_on_a_downtrend(self) -> None:
        self.assertLess(trend_slope([float(v) for v in range(40, 1, -1)], 20), 0)

    def test_slope_of_a_flat_series_is_zero(self) -> None:
        self.assertAlmostEqual(trend_slope([25.0] * 40, 20), 0.0)

    def test_slope_needs_enough_history(self) -> None:
        self.assertIsNone(trend_slope([1.0, 2.0], 20))


class CandleTests(unittest.TestCase):
    def test_parses_a_binance_kline_row(self) -> None:
        row = [
            1699999200000, "43250.10", "43310.00", "43190.55", "43287.40",
            "128.4521", 1699999499999, "5557000.0", 4210, "64.1", "2778000.0", "0",
        ]
        parsed = Candle.from_binance(row)
        self.assertEqual(parsed.open_time, 1699999200000)
        self.assertAlmostEqual(parsed.high, 43310.00)
        self.assertAlmostEqual(parsed.close, 43287.40)
        self.assertAlmostEqual(parsed.volume, 128.4521)

    def test_last_defined_skips_the_warmup(self) -> None:
        self.assertEqual(last_defined([None, None, 3.0, None]), 3.0)
        self.assertIsNone(last_defined([None, None]))


if __name__ == "__main__":
    unittest.main()
