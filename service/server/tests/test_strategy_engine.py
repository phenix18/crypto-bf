"""Confluence scoring and target placement.

The engine is deterministic, so these drive it with synthetic shapes whose
correct answer is obvious — a clean trend, a dead range, a volatility blowout —
rather than with a recorded market where the "right" call is a matter of taste.
"""

import math
import os
import sys
import unittest

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from indicators import Candle  # noqa: E402
from strategy_engine import (  # noqa: E402
    Direction,
    Regime,
    StrategyConfig,
    analyse,
    detect_regime,
)


def build(prices, volumes=None, wick=0.004):
    """Candles around a close series, with a proportional high/low wick."""
    volumes = volumes or [1000.0] * len(prices)
    out = []
    for index, (close, volume) in enumerate(zip(prices, volumes)):
        out.append(
            Candle(
                open_time=index * 300_000,
                open=close,
                high=close * (1 + wick),
                low=close * (1 - wick),
                close=close,
                volume=volume,
            )
        )
    return out


def uptrend(n=120, start=100.0, step=0.004):
    """A rising market with a small oscillation so the indicators have texture."""
    return [start * (1 + step) ** i + math.sin(i / 3.0) * start * 0.001 for i in range(n)]


def downtrend(n=120, start=100.0, step=0.004):
    return [start * (1 - step) ** i + math.sin(i / 3.0) * start * 0.001 for i in range(n)]


def chop(n=120, start=100.0, amplitude=0.006):
    return [start * (1 + math.sin(i / 2.0) * amplitude) for i in range(n)]


class DirectionTests(unittest.TestCase):
    def test_a_clean_uptrend_proposes_long(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend()))
        self.assertEqual(result.direction, Direction.LONG)
        self.assertGreater(result.confluence, 25.0)

    def test_a_clean_downtrend_proposes_short(self) -> None:
        result = analyse("BTCUSDT", "5m", build(downtrend()))
        self.assertEqual(result.direction, Direction.SHORT)
        self.assertGreater(result.confluence, 25.0)

    def test_chop_scores_lower_than_a_trend(self) -> None:
        trending = analyse("BTCUSDT", "5m", build(uptrend()))
        ranging = analyse("BTCUSDT", "5m", build(chop()))
        self.assertLess(ranging.confluence, trending.confluence)

    def test_confluence_stays_within_zero_and_one_hundred(self) -> None:
        for prices in (uptrend(), downtrend(), chop()):
            result = analyse("BTCUSDT", "5m", build(prices))
            self.assertGreaterEqual(result.confluence, 0.0)
            self.assertLessEqual(result.confluence, 100.0)


class GuardTests(unittest.TestCase):
    def test_short_history_is_blocked_not_guessed(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend(n=10)))
        self.assertEqual(result.direction, Direction.FLAT)
        self.assertFalse(result.actionable)
        self.assertTrue(any("candles" in b for b in result.blockers))

    def test_no_candles_at_all_is_handled(self) -> None:
        result = analyse("BTCUSDT", "5m", [])
        self.assertEqual(result.direction, Direction.FLAT)
        self.assertFalse(result.actionable)

    def test_dead_volatility_is_blocked(self) -> None:
        # A market that barely moves cannot pay for the spread.
        flat = build([100.0] * 120, wick=0.00001)
        result = analyse("BTCUSDT", "5m", flat)
        self.assertFalse(result.actionable)
        self.assertTrue(any("volatility too low" in b for b in result.blockers))

    def test_blown_out_volatility_is_blocked(self) -> None:
        wild = build(uptrend(step=0.02), wick=0.12)
        result = analyse("BTCUSDT", "5m", wild)
        self.assertFalse(result.actionable)
        self.assertTrue(any("volatility too high" in b for b in result.blockers))

    def test_a_poor_risk_reward_is_blocked(self) -> None:
        # Demanding 10:1 cannot be met by a 1.5/2.5 ATR stop and target.
        config = StrategyConfig(min_risk_reward=10.0)
        result = analyse("BTCUSDT", "5m", build(uptrend()), config)
        self.assertFalse(result.actionable)
        self.assertTrue(any("risk/reward" in b for b in result.blockers))


class TargetTests(unittest.TestCase):
    def test_a_long_puts_the_stop_below_and_the_target_above(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend()))
        self.assertEqual(result.direction, Direction.LONG)
        self.assertLess(result.stop_loss, result.entry)
        self.assertGreater(result.take_profit, result.entry)

    def test_a_short_puts_the_stop_above_and_the_target_below(self) -> None:
        result = analyse("BTCUSDT", "5m", build(downtrend()))
        self.assertEqual(result.direction, Direction.SHORT)
        self.assertGreater(result.stop_loss, result.entry)
        self.assertLess(result.take_profit, result.entry)

    def test_risk_reward_matches_the_distances(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend()))
        risk = abs(result.entry - result.stop_loss)
        reward = abs(result.take_profit - result.entry)
        self.assertAlmostEqual(result.risk_reward, reward / risk, places=6)

    def test_the_stop_scales_with_volatility(self) -> None:
        calm = analyse("BTCUSDT", "5m", build(uptrend(), wick=0.002))
        rough = analyse("BTCUSDT", "5m", build(uptrend(), wick=0.02))
        calm_risk = abs(calm.entry - calm.stop_loss) / calm.entry
        rough_risk = abs(rough.entry - rough.stop_loss) / rough.entry
        self.assertGreater(rough_risk, calm_risk)

    def test_a_wider_atr_multiple_widens_the_stop(self) -> None:
        tight = analyse("BTCUSDT", "5m", build(uptrend()), StrategyConfig(atr_stop_multiple=1.0))
        wide = analyse("BTCUSDT", "5m", build(uptrend()), StrategyConfig(atr_stop_multiple=3.0))
        self.assertGreater(
            abs(wide.entry - wide.stop_loss),
            abs(tight.entry - tight.stop_loss),
        )

    def test_flat_has_no_targets(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend(n=10)))
        self.assertIsNone(result.stop_loss)
        self.assertIsNone(result.take_profit)
        self.assertIsNone(result.risk_reward)


class VoteTests(unittest.TestCase):
    def test_every_check_reports(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend()))
        names = {v.name for v in result.votes}
        self.assertEqual(
            names,
            {"rsi", "macd", "ema", "bollinger", "stochastic", "trend", "structure", "volume"},
        )

    def test_volume_confirms_the_leaning_side_never_opposes_it(self) -> None:
        for prices in (uptrend(), downtrend()):
            result = analyse("BTCUSDT", "5m", build(prices))
            volume = next(v for v in result.votes if v.name == "volume")
            if volume.direction is not Direction.FLAT:
                self.assertEqual(volume.direction, result.direction)

    def test_thin_volume_withholds_confirmation(self) -> None:
        prices = uptrend()
        # The last bar trades on a fraction of the usual volume.
        volumes = [1000.0] * (len(prices) - 1) + [100.0]
        result = analyse("BTCUSDT", "5m", build(prices, volumes))
        volume = next(v for v in result.votes if v.name == "volume")
        self.assertEqual(volume.direction, Direction.FLAT)
        self.assertIn("thin", volume.detail)

    def test_a_volume_spike_confirms(self) -> None:
        prices = uptrend()
        volumes = [1000.0] * (len(prices) - 1) + [3000.0]
        result = analyse("BTCUSDT", "5m", build(prices, volumes))
        volume = next(v for v in result.votes if v.name == "volume")
        self.assertEqual(volume.direction, Direction.LONG)
        self.assertGreater(volume.strength, 0.5)

    def test_votes_are_signed_by_direction(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend()))
        for vote in result.votes:
            if vote.direction is Direction.LONG:
                self.assertGreater(vote.signed, 0)
            elif vote.direction is Direction.SHORT:
                self.assertLess(vote.signed, 0)
            else:
                self.assertEqual(vote.signed, 0.0)


class RegimeTests(unittest.TestCase):
    """The regime filter is what stops the engine knife-catching a downtrend.

    Without it the oscillators sit pinned at oversold all the way down and
    outvote the trend checks, and the engine proposes LONG into a collapse.
    """

    def test_an_uptrend_is_classified_as_trending_up(self) -> None:
        self.assertEqual(analyse("BTCUSDT", "5m", build(uptrend())).regime, Regime.TRENDING_UP)

    def test_a_downtrend_is_classified_as_trending_down(self) -> None:
        self.assertEqual(analyse("BTCUSDT", "5m", build(downtrend())).regime, Regime.TRENDING_DOWN)

    def test_chop_is_classified_as_ranging(self) -> None:
        self.assertEqual(analyse("BTCUSDT", "5m", build(chop())).regime, Regime.RANGING)

    def test_oversold_in_a_downtrend_is_never_a_buy(self) -> None:
        result = analyse("BTCUSDT", "5m", build(downtrend()))
        rsi_vote = next(v for v in result.votes if v.name == "rsi")
        self.assertNotEqual(rsi_vote.direction, Direction.LONG)
        self.assertEqual(result.direction, Direction.SHORT)

    def test_overbought_in_an_uptrend_is_never_a_sell(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend()))
        rsi_vote = next(v for v in result.votes if v.name == "rsi")
        self.assertNotEqual(rsi_vote.direction, Direction.SHORT)
        self.assertEqual(result.direction, Direction.LONG)

    def test_oversold_in_a_range_is_a_buy(self) -> None:
        # Same oscillator reading, opposite conclusion, because the regime differs.
        prices = chop()
        # End the series at the bottom of the range.
        trough = min(range(len(prices)), key=lambda i: prices[i] if i > 80 else float("inf"))
        result = analyse("BTCUSDT", "5m", build(prices[: trough + 1]))
        self.assertEqual(result.regime, Regime.RANGING)
        self.assertEqual(result.direction, Direction.LONG)

    def test_a_counter_trend_net_is_blocked(self) -> None:
        # Force the issue: a trend regime whose checks net the other way must
        # not produce a tradable proposal.
        for prices in (uptrend(), downtrend()):
            result = analyse("BTCUSDT", "5m", build(prices))
            if result.regime.direction is not Direction.FLAT:
                if result.direction is result.regime.direction.opposite:
                    self.assertFalse(result.actionable)
                    self.assertTrue(any("counter-trend" in b for b in result.blockers))

    def test_detector_needs_both_slope_and_stack(self) -> None:
        config = StrategyConfig()
        # Slope says up, EMAs are tangled the other way: not a trend.
        self.assertEqual(
            detect_regime(0.01, ema_fast=90.0, ema_slow=100.0, ema_trend=110.0, config=config),
            Regime.RANGING,
        )
        # Stack says up but the slope is flat: not a trend either.
        self.assertEqual(
            detect_regime(0.0, ema_fast=110.0, ema_slow=100.0, ema_trend=90.0, config=config),
            Regime.RANGING,
        )
        # Both agree.
        self.assertEqual(
            detect_regime(0.01, ema_fast=110.0, ema_slow=100.0, ema_trend=90.0, config=config),
            Regime.TRENDING_UP,
        )

    def test_detector_falls_back_to_ranging_without_data(self) -> None:
        config = StrategyConfig()
        self.assertEqual(detect_regime(None, None, None, None, config), Regime.RANGING)

    def test_regime_is_exposed_to_the_ui(self) -> None:
        payload = analyse("BTCUSDT", "5m", build(uptrend())).to_dict()
        self.assertEqual(payload["regime"], "trending_up")
        self.assertEqual(payload["readings"]["regime"], "trending_up")


class SerialisationTests(unittest.TestCase):
    def test_to_dict_is_json_safe_and_complete(self) -> None:
        import json

        result = analyse("BTCUSDT", "5m", build(uptrend()))
        payload = result.to_dict()
        json.dumps(payload)  # must not raise
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertEqual(payload["direction"], result.direction.value)
        self.assertIn("readings", payload)
        self.assertIn("rsi", payload["readings"])
        self.assertEqual(len(payload["votes"]), len(result.votes))

    def test_direction_has_an_opposite(self) -> None:
        self.assertEqual(Direction.LONG.opposite, Direction.SHORT)
        self.assertEqual(Direction.SHORT.opposite, Direction.LONG)
        self.assertEqual(Direction.FLAT.opposite, Direction.FLAT)


if __name__ == "__main__":
    unittest.main()


class FamilyWeightTests(unittest.TestCase):
    """Weighting is symmetric: whichever family does not lead gets damped."""

    def test_every_check_has_a_family(self) -> None:
        from strategy_engine import VOTE_FAMILY

        result = analyse("BTCUSDT", "5m", build(uptrend()))
        for vote in result.votes:
            self.assertIn(vote.name, VOTE_FAMILY)

    def test_trend_family_leads_in_a_trend(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend()))
        self.assertEqual(result.regime, Regime.TRENDING_UP)
        trend_weight = sum(v.weight for v in result.votes if v.name in {"ema", "macd", "trend"})
        reversion_weight = sum(
            v.weight for v in result.votes if v.name in {"rsi", "stochastic", "bollinger", "structure"}
        )
        self.assertGreater(trend_weight / 3, reversion_weight / 4)

    def test_reversion_family_leads_in_a_range(self) -> None:
        result = analyse("BTCUSDT", "5m", build(chop()))
        self.assertEqual(result.regime, Regime.RANGING)
        trend_weight = sum(v.weight for v in result.votes if v.name in {"ema", "macd", "trend"})
        reversion_weight = sum(
            v.weight for v in result.votes if v.name in {"rsi", "stochastic", "bollinger", "structure"}
        )
        self.assertGreater(reversion_weight / 4, trend_weight / 3)

    def test_damping_never_silences_a_family(self) -> None:
        # A damped check still carries weight, so real disagreement still costs
        # confluence instead of being swept under the rug.
        for prices in (uptrend(), chop()):
            result = analyse("BTCUSDT", "5m", build(prices))
            for vote in result.votes:
                self.assertGreater(vote.weight, 0.0)


class ConfluenceScaleTests(unittest.TestCase):
    """What the headline number means, and that it is reachable.

    The scale was wrong once in a way nothing caught: dividing agreement by
    every check on the board, including the ones that structurally have nothing
    to say in the current regime, capped a textbook setup near 50. Nothing was
    broken, no test failed, and a "trade above 90" rule quietly meant "never
    trade" — the worst kind of bug, because the bot looks like it is working.
    """

    def test_a_clean_trend_clears_a_demanding_gate(self) -> None:
        # This is the regression test for that bug. If the scale is ever
        # redefined again, a textbook trend must still be able to clear the
        # gate the trader actually ships with.
        from calibration import CalibrationConfig

        result = analyse("BTCUSDT", "5m", build(uptrend()))
        self.assertTrue(result.actionable)
        self.assertGreaterEqual(result.confluence, CalibrationConfig().confluence_gate)

    def test_participation_is_reported_alongside_the_score(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend()))
        self.assertGreater(result.participation, 0.0)
        self.assertLessEqual(result.participation, 1.0)
        self.assertEqual(
            result.to_dict()["participation"], round(result.participation * 100, 2)
        )

    def test_confluence_is_agreement_among_the_checks_that_spoke(self) -> None:
        result = analyse("BTCUSDT", "5m", build(uptrend()))
        voted = [v for v in result.votes if v.direction is not Direction.FLAT]
        voted_weight = sum(v.weight for v in voted)
        net = sum(v.signed for v in voted)
        self.assertAlmostEqual(result.confluence, abs(net) / voted_weight * 100.0, places=6)

    def test_a_quiet_board_is_blocked_however_much_it_agrees(self) -> None:
        # Two checks agreeing is not a confluence. Chop leaves most of the
        # trend family silent, so participation, not the score, is what refuses
        # it.
        result = analyse("BTCUSDT", "5m", build(chop()))
        self.assertLess(result.participation, 0.45)
        self.assertTrue(any("have a reading" in b for b in result.blockers))

    def test_the_participation_floor_is_configurable(self) -> None:
        permissive = analyse(
            "BTCUSDT", "5m", build(chop()), StrategyConfig(min_participation=0.0)
        )
        self.assertFalse(any("have a reading" in b for b in permissive.blockers))

    def test_confluence_still_cannot_exceed_one_hundred(self) -> None:
        for prices in (uptrend(), downtrend(), chop()):
            result = analyse("BTCUSDT", "5m", build(prices))
            self.assertLessEqual(result.confluence, 100.0)


class TargetPlacementTests(unittest.TestCase):
    """Where the stop and the target land, and what structure may do to them.

    All of these come from one bug found by running the engine on generated
    data rather than on fixtures: a support level left over from a different
    price regime widened a short's stop to five times the entry price, and the
    resulting 0.007 reward-to-risk was reported as an ordinary reading. ATR
    sets the scale here; structure may only adjust within it.
    """

    ATR = 2.0
    PRICE = 100.0

    def targets(self, direction, support=None, resistance=None, config=None):
        from strategy_engine import _targets

        return _targets(
            self.PRICE, direction, self.ATR, support, resistance, config or StrategyConfig()
        )

    def test_without_structure_the_ratio_is_the_atr_ratio(self) -> None:
        config = StrategyConfig()
        _, _, ratio = self.targets(Direction.LONG)
        self.assertAlmostEqual(ratio, config.atr_target_multiple / config.atr_stop_multiple)

    def test_a_distant_level_cannot_drag_the_stop(self) -> None:
        # The actual failure: a level hundreds of ATR away, from a different
        # price regime, used to become the stop.
        stop, _, ratio = self.targets(Direction.SHORT, resistance=self.PRICE + self.ATR * 400)
        self.assertAlmostEqual(stop, self.PRICE + self.ATR * StrategyConfig().atr_stop_multiple)
        self.assertGreater(ratio, 1.0)

    def test_a_nearby_level_still_widens_the_stop(self) -> None:
        # The rule it was there to serve has to survive the fix: a stop parked
        # on support is the stop the wick takes out.
        support = self.PRICE - self.ATR * 1.6
        stop, _, _ = self.targets(Direction.LONG, support=support)
        self.assertAlmostEqual(stop, support - self.ATR * 0.25)
        self.assertLess(stop, self.PRICE - self.ATR * StrategyConfig().atr_stop_multiple)

    def test_a_level_just_past_the_reach_is_ignored(self) -> None:
        config = StrategyConfig()
        edge = config.atr_stop_multiple * config.structure_reach
        _, _, _ = self.targets(Direction.LONG, support=self.PRICE - self.ATR * (edge - 0.1))
        far_stop, _, _ = self.targets(Direction.LONG, support=self.PRICE - self.ATR * (edge + 0.5))
        self.assertAlmostEqual(far_stop, self.PRICE - self.ATR * config.atr_stop_multiple)

    def test_noise_half_an_atr_away_is_not_a_wall(self) -> None:
        config = StrategyConfig()
        _, target, ratio = self.targets(Direction.LONG, resistance=self.PRICE + self.ATR * 0.2)
        self.assertAlmostEqual(target, self.PRICE + self.ATR * config.atr_target_multiple)
        self.assertAlmostEqual(ratio, config.atr_target_multiple / config.atr_stop_multiple)

    def test_a_real_wall_caps_the_target(self) -> None:
        wall = self.PRICE + self.ATR * 1.5
        _, target, ratio = self.targets(Direction.LONG, resistance=wall)
        self.assertAlmostEqual(target, wall - self.ATR * 0.1)
        self.assertLess(ratio, StrategyConfig().min_risk_reward)

    def test_a_capped_target_never_lands_below_the_entry(self) -> None:
        # A "take profit" below a long's entry is a loss with a friendly name.
        _, target, ratio = self.targets(Direction.LONG, resistance=self.PRICE + self.ATR * 0.55)
        self.assertGreater(target, self.PRICE)
        self.assertGreater(ratio, 0.0)
        self.assertLess(ratio, StrategyConfig().min_risk_reward)

    def test_a_capped_short_target_never_lands_above_the_entry(self) -> None:
        _, target, ratio = self.targets(Direction.SHORT, support=self.PRICE - self.ATR * 0.55)
        self.assertLess(target, self.PRICE)
        self.assertGreater(ratio, 0.0)

    def test_the_short_mirror_holds(self) -> None:
        config = StrategyConfig()
        stop, target, ratio = self.targets(Direction.SHORT)
        self.assertAlmostEqual(stop, self.PRICE + self.ATR * config.atr_stop_multiple)
        self.assertAlmostEqual(target, self.PRICE - self.ATR * config.atr_target_multiple)
        self.assertAlmostEqual(ratio, config.atr_target_multiple / config.atr_stop_multiple)

    def test_every_actionable_proposal_points_the_right_way(self) -> None:
        """The invariant, swept over generated data rather than one fixture.

        This is the test that would have caught the original bug: it fails the
        moment a proposal is tradable with its target on the wrong side of its
        entry, whatever produced that.
        """
        import os
        import sys

        sys.path.insert(0, os.path.dirname(SERVER_DIR))
        from market_data import SyntheticSource

        source = SyntheticSource(clock=lambda: 1_700_000_000.0)
        checked = 0
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"):
            series = source._series(symbol, "5m")
            for end in range(300, len(series), 97):
                result = analyse(symbol, "5m", series[end - 300 : end])
                if not result.actionable:
                    continue
                checked += 1
                if result.direction is Direction.LONG:
                    self.assertGreater(result.take_profit, result.entry)
                    self.assertLess(result.stop_loss, result.entry)
                else:
                    self.assertLess(result.take_profit, result.entry)
                    self.assertGreater(result.stop_loss, result.entry)
                self.assertGreaterEqual(result.risk_reward, StrategyConfig().min_risk_reward)
                # And the stop stays on the ATR's scale rather than a level's.
                reach = StrategyConfig().atr_stop_multiple * StrategyConfig().structure_reach
                self.assertLessEqual(
                    abs(result.entry - result.stop_loss),
                    result.readings.atr * (reach + 0.3),
                )
        self.assertGreater(checked, 20, 'the sweep needs actionable proposals to be meaningful')
