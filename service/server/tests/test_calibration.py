"""Calibration: what the history actually says, versus what it looks like it says.

Most of these pin down the two ways a calibration layer flatters itself — a
tiny sample read as proof, and a high hit rate read as an edge.
"""

import os
import sys
import unittest

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from calibration import (  # noqa: E402
    Bucket,
    CalibrationConfig,
    Outcome,
    TradeRecord,
    classify,
    confluence_band,
    gate,
    summarise,
    summarise_all,
    wilson_lower_bound,
)


def record(outcome=Outcome.WIN, r=1.0, confluence=85.0, regime="trending_up", direction="long"):
    return TradeRecord(
        symbol="BTCUSDT",
        interval="5m",
        regime=regime,
        direction=direction,
        confluence=confluence,
        outcome=outcome,
        r_multiple=r,
    )


def history(wins: int, losses: int, win_r: float = 2.0, loss_r: float = -1.0, **kwargs):
    return (
        [record(Outcome.WIN, win_r, **kwargs) for _ in range(wins)]
        + [record(Outcome.LOSS, loss_r, **kwargs) for _ in range(losses)]
    )


class WilsonTests(unittest.TestCase):
    def test_a_perfect_tiny_sample_is_not_certainty(self) -> None:
        # Three for three is three coin flips, not a 100% strategy.
        self.assertLess(wilson_lower_bound(3, 3), 0.5)

    def test_confidence_grows_with_the_sample(self) -> None:
        small = wilson_lower_bound(8, 10)
        large = wilson_lower_bound(80, 100)
        huge = wilson_lower_bound(800, 1000)
        self.assertLess(small, large)
        self.assertLess(large, huge)

    def test_it_converges_towards_the_raw_rate(self) -> None:
        self.assertAlmostEqual(wilson_lower_bound(8_000, 10_000), 0.80, delta=0.02)

    def test_it_never_exceeds_the_raw_rate(self) -> None:
        for wins, total in ((1, 2), (5, 10), (50, 100), (999, 1000)):
            self.assertLessEqual(wilson_lower_bound(wins, total), wins / total + 1e-9)

    def test_an_empty_sample_is_zero_not_an_error(self) -> None:
        self.assertEqual(wilson_lower_bound(0, 0), 0.0)

    def test_total_failure_floors_at_zero(self) -> None:
        self.assertEqual(wilson_lower_bound(0, 20), 0.0)


class BandTests(unittest.TestCase):
    def test_bands_cover_the_range(self) -> None:
        self.assertEqual(confluence_band(0.0), "0-50")
        self.assertEqual(confluence_band(49.9), "0-50")
        self.assertEqual(confluence_band(50.0), "50-65")
        self.assertEqual(confluence_band(64.9), "50-65")
        self.assertEqual(confluence_band(65.0), "65-80")
        self.assertEqual(confluence_band(80.0), "80-100")
        self.assertEqual(confluence_band(100.0), "80-100")

    def test_a_bucket_key_is_stable(self) -> None:
        bucket = Bucket(regime="ranging", direction="short", confluence_band="65-80")
        self.assertEqual(bucket.key(), "ranging|short|65-80")


class SummaryTests(unittest.TestCase):
    def test_it_counts_each_outcome(self) -> None:
        records = history(7, 3) + [record(Outcome.SCRATCH, 0.0)]
        stats = summarise(records, records[0].bucket)
        self.assertEqual(stats.wins, 7)
        self.assertEqual(stats.losses, 3)
        self.assertEqual(stats.scratches, 1)
        self.assertEqual(stats.total, 11)

    def test_a_scratch_does_not_move_the_hit_rate(self) -> None:
        clean = summarise(history(7, 3), history(7, 3)[0].bucket)
        with_scratch = history(7, 3) + [record(Outcome.SCRATCH, 0.0)]
        scratched = summarise(with_scratch, with_scratch[0].bucket)
        self.assertAlmostEqual(clean.raw_hit_rate, scratched.raw_hit_rate)

    def test_expectancy_is_the_average_r(self) -> None:
        records = history(1, 1, win_r=3.0, loss_r=-1.0)
        stats = summarise(records, records[0].bucket)
        self.assertAlmostEqual(stats.expectancy_r, 1.0)

    def test_buckets_do_not_bleed_into_each_other(self) -> None:
        longs = history(10, 0, direction="long")
        shorts = history(0, 10, direction="short")
        stats = summarise(longs + shorts, longs[0].bucket)
        self.assertEqual(stats.wins, 10)
        self.assertEqual(stats.losses, 0)

    def test_summarise_all_lists_busiest_first(self) -> None:
        records = history(20, 0, direction="long") + history(2, 0, direction="short")
        everything = summarise_all(records)
        self.assertEqual(len(everything), 2)
        self.assertEqual(everything[0].total, 20)
        self.assertEqual(everything[1].total, 2)

    def test_an_absent_bucket_summarises_to_nothing(self) -> None:
        stats = summarise([], Bucket("ranging", "long", "65-80"))
        self.assertEqual(stats.total, 0)
        self.assertEqual(stats.raw_hit_rate, 0.0)
        self.assertFalse(stats.sample_is_usable)


class GateTests(unittest.TestCase):
    def test_a_cold_start_gates_on_confluence(self) -> None:
        verdict = gate(confluence=95.0, regime="trending_up", direction="long", history=[])
        self.assertEqual(verdict.basis, "confluence")
        self.assertTrue(verdict.allowed)
        self.assertIsNone(verdict.probability)

    def test_a_cold_start_refuses_weak_confluence(self) -> None:
        verdict = gate(confluence=70.0, regime="trending_up", direction="long", history=[])
        self.assertEqual(verdict.basis, "confluence")
        self.assertFalse(verdict.allowed)

    def test_a_small_but_perfect_record_does_not_unlock_the_gate(self) -> None:
        # Five straight wins is not evidence; the gate stays on confluence.
        verdict = gate(
            confluence=70.0,
            regime="trending_up",
            direction="long",
            history=history(5, 0, confluence=70.0),
        )
        self.assertEqual(verdict.basis, "confluence")
        self.assertFalse(verdict.allowed)

    def test_calibration_takes_over_once_the_sample_is_real(self) -> None:
        verdict = gate(
            confluence=70.0,
            regime="trending_up",
            direction="long",
            history=history(30, 10, confluence=70.0),
        )
        self.assertEqual(verdict.basis, "calibrated")
        self.assertTrue(verdict.allowed)
        self.assertIsNotNone(verdict.probability)

    def test_calibration_can_refuse_what_confluence_would_have_allowed(self) -> None:
        # High confluence, but this bucket has been measured and it loses.
        verdict = gate(
            confluence=95.0,
            regime="trending_up",
            direction="long",
            history=history(12, 30, confluence=95.0),
        )
        self.assertEqual(verdict.basis, "calibrated")
        self.assertFalse(verdict.allowed)

    def test_a_high_hit_rate_with_negative_expectancy_is_refused(self) -> None:
        """The trap this layer exists to catch.

        Eight wins of 0.15R against two losses of 1R is an 80% strategy with
        an expectancy of 0.8*0.15 - 0.2*1.0 = -0.08R: it loses money on every
        ten trades while looking like it wins four out of five.
        """
        records = history(32, 8, win_r=0.15, loss_r=-1.0, confluence=85.0)
        verdict = gate(
            confluence=85.0, regime="trending_up", direction="long", history=records
        )
        self.assertEqual(verdict.basis, "calibrated")
        self.assertFalse(verdict.allowed)
        self.assertTrue(any("not an edge" in r for r in verdict.reasons))

    def test_the_refused_record_really_is_a_losing_one(self) -> None:
        # Guard against the test above passing for the wrong reason: the
        # premise has to actually be negative expectancy.
        records = history(32, 8, win_r=0.15, loss_r=-1.0, confluence=85.0)
        stats = summarise(records, records[0].bucket)
        self.assertGreater(stats.raw_hit_rate, 0.75)
        self.assertLess(stats.expectancy_r, 0.0)

    def test_the_same_record_with_real_winners_is_allowed(self) -> None:
        records = history(32, 8, win_r=2.0, loss_r=-1.0, confluence=85.0)
        verdict = gate(
            confluence=85.0, regime="trending_up", direction="long", history=records
        )
        self.assertTrue(verdict.allowed)

    def test_history_from_another_regime_does_not_count(self) -> None:
        # A long record in a trend says nothing about a long in a range.
        verdict = gate(
            confluence=70.0,
            regime="ranging",
            direction="long",
            history=history(50, 5, regime="trending_up", confluence=70.0),
        )
        self.assertEqual(verdict.basis, "confluence")
        self.assertFalse(verdict.allowed)

    def test_the_confluence_gate_is_configurable(self) -> None:
        relaxed = CalibrationConfig(confluence_gate=60.0)
        verdict = gate(
            confluence=70.0,
            regime="trending_up",
            direction="long",
            history=[],
            config=relaxed,
        )
        self.assertTrue(verdict.allowed)

    def test_the_verdict_explains_itself(self) -> None:
        verdict = gate(
            confluence=85.0,
            regime="trending_up",
            direction="long",
            history=history(30, 10, confluence=85.0),
        )
        self.assertTrue(verdict.reasons)
        joined = " ".join(verdict.reasons)
        self.assertIn("decided trades", joined)
        self.assertIn("expectancy", joined)


class ClassifyTests(unittest.TestCase):
    def test_a_long_stopped_out_scores_minus_one_r(self) -> None:
        outcome, r = classify(entry=100.0, exit_price=95.0, stop_loss=95.0, direction="long")
        self.assertEqual(outcome, Outcome.LOSS)
        self.assertAlmostEqual(r, -1.0)

    def test_a_short_stopped_out_scores_minus_one_r(self) -> None:
        outcome, r = classify(entry=100.0, exit_price=105.0, stop_loss=105.0, direction="short")
        self.assertEqual(outcome, Outcome.LOSS)
        self.assertAlmostEqual(r, -1.0)

    def test_a_long_at_target_scores_the_reward_ratio(self) -> None:
        outcome, r = classify(entry=100.0, exit_price=110.0, stop_loss=95.0, direction="long")
        self.assertEqual(outcome, Outcome.WIN)
        self.assertAlmostEqual(r, 2.0)

    def test_a_short_at_target_scores_the_reward_ratio(self) -> None:
        outcome, r = classify(entry=100.0, exit_price=90.0, stop_loss=105.0, direction="short")
        self.assertEqual(outcome, Outcome.WIN)
        self.assertAlmostEqual(r, 2.0)

    def test_a_flat_exit_is_a_scratch(self) -> None:
        outcome, r = classify(entry=100.0, exit_price=100.0, stop_loss=95.0, direction="long")
        self.assertEqual(outcome, Outcome.SCRATCH)
        self.assertEqual(r, 0.0)

    def test_a_zero_risk_trade_cannot_be_scored(self) -> None:
        outcome, r = classify(entry=100.0, exit_price=120.0, stop_loss=100.0, direction="long")
        self.assertEqual(outcome, Outcome.SCRATCH)

    def test_an_unknown_direction_is_a_scratch(self) -> None:
        outcome, _ = classify(entry=100.0, exit_price=120.0, stop_loss=95.0, direction="flat")
        self.assertEqual(outcome, Outcome.SCRATCH)

    def test_r_is_comparable_across_instruments(self) -> None:
        # Same R from a $40 000 instrument and a $0.50 one.
        _, cheap = classify(entry=0.50, exit_price=0.60, stop_loss=0.45, direction="long")
        _, dear = classify(entry=40_000.0, exit_price=48_000.0, stop_loss=36_000.0, direction="long")
        self.assertAlmostEqual(cheap, dear)


class SerialisationTests(unittest.TestCase):
    def test_payloads_are_json_safe(self) -> None:
        import json

        records = history(30, 10)
        json.dumps(summarise(records, records[0].bucket).to_dict())
        verdict = gate(
            confluence=85.0, regime="trending_up", direction="long", history=records
        )
        payload = verdict.to_dict()
        json.dumps(payload)
        self.assertEqual(payload["basis"], "calibrated")
        self.assertIsInstance(payload["probability"], float)


if __name__ == "__main__":
    unittest.main()
