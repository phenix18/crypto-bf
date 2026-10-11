"""The limits that stand between a bad run and a ruinous one.

This is the module that fails closed, so most of these tests are about refusal:
what the sizer does when something is missing, stale, or already too far gone.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from risk_manager import (  # noqa: E402
    AccountState,
    Halt,
    RiskConfig,
    check_halt,
    daily_loss_percent,
    drawdown_percent,
    evaluate,
    should_trip_kill_switch,
)

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def account(**overrides) -> AccountState:
    """A healthy account. Changing equity moves the peak with it unless the
    test says otherwise — an account at its peak is the neutral starting point,
    and leaving the peak behind would silently arm the drawdown halt."""
    base = dict(
        equity=10_000.0,
        realised_pnl_today=0.0,
        open_positions=0,
        consecutive_losses=0,
        as_of=NOW,
    )
    base.update(overrides)
    base.setdefault("peak_equity", base["equity"])
    return AccountState(**base)


class SizingTests(unittest.TestCase):
    def test_quantity_makes_the_stop_cost_exactly_the_risk_budget(self) -> None:
        # 0.5% of 10 000 = 50 risked over a 100-wide stop = 0.5 units.
        decision = evaluate(entry=1000.0, stop_loss=900.0, account=account(), now=NOW)
        self.assertTrue(decision.approved)
        self.assertAlmostEqual(decision.risk_amount, 50.0)
        self.assertAlmostEqual(decision.quantity, 0.5)
        self.assertAlmostEqual(decision.quantity * 100.0, 50.0)

    def test_a_tighter_stop_buys_more_units_for_the_same_risk(self) -> None:
        # Both stops are wide enough that the notional cap does not bind, so
        # the risked amount is identical and only the quantity changes.
        wide = evaluate(entry=1000.0, stop_loss=800.0, account=account(), now=NOW)
        tight = evaluate(entry=1000.0, stop_loss=900.0, account=account(), now=NOW)
        self.assertGreater(tight.quantity, wide.quantity)
        self.assertAlmostEqual(tight.risk_amount, wide.risk_amount, places=6)

    def test_the_notional_cap_dominates_at_scalping_stop_distances(self) -> None:
        """Worth stating plainly, because it surprises people.

        With risk 0.5% and a 10% notional cap, the cap binds for any stop
        closer than 5% — which is every scalping stop there is. The bot then
        risks *less* than the allowance, and the decision says so.
        """
        decision = evaluate(entry=1000.0, stop_loss=995.0, account=account(), now=NOW)
        self.assertTrue(decision.approved)
        self.assertTrue(any("capped" in r for r in decision.reasons))
        self.assertLess(decision.risk_amount, 50.0)
        self.assertAlmostEqual(decision.notional, 1_000.0)

    def test_risk_scales_with_equity(self) -> None:
        small = evaluate(entry=1000.0, stop_loss=900.0, account=account(equity=1_000.0), now=NOW)
        large = evaluate(entry=1000.0, stop_loss=900.0, account=account(equity=100_000.0), now=NOW)
        self.assertAlmostEqual(small.risk_amount, 5.0)
        self.assertAlmostEqual(large.risk_amount, 500.0)

    def test_a_very_tight_stop_is_capped_by_the_notional_limit(self) -> None:
        # Risk sizing alone would buy far more than 10% of equity here.
        decision = evaluate(entry=1000.0, stop_loss=999.0, account=account(), now=NOW)
        self.assertTrue(decision.approved)
        self.assertLessEqual(decision.notional, 10_000.0 * 0.10 + 1e-9)
        self.assertTrue(any("capped" in r for r in decision.reasons))
        # Capping means risking less than the allowance, never more.
        self.assertLess(decision.risk_amount, 50.0)

    def test_a_short_sizes_the_same_as_a_long(self) -> None:
        long_side = evaluate(entry=1000.0, stop_loss=900.0, account=account(), now=NOW)
        short_side = evaluate(entry=1000.0, stop_loss=1100.0, account=account(), now=NOW)
        self.assertAlmostEqual(long_side.quantity, short_side.quantity)


class RefusalTests(unittest.TestCase):
    def test_no_stop_loss_is_refused(self) -> None:
        decision = evaluate(entry=1000.0, stop_loss=None, account=account(), now=NOW)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.quantity, 0.0)
        self.assertTrue(any("no stop loss" in r for r in decision.reasons))

    def test_a_stop_on_the_entry_is_refused(self) -> None:
        decision = evaluate(entry=1000.0, stop_loss=1000.0, account=account(), now=NOW)
        self.assertFalse(decision.approved)

    def test_a_stop_inside_the_noise_is_refused(self) -> None:
        # 0.01% away: fees and spread eat it, and noise takes it out.
        decision = evaluate(entry=1000.0, stop_loss=999.9, account=account(), now=NOW)
        self.assertFalse(decision.approved)
        self.assertTrue(any("floor" in r for r in decision.reasons))

    def test_a_non_positive_entry_is_refused(self) -> None:
        self.assertFalse(evaluate(entry=0.0, stop_loss=1.0, account=account(), now=NOW).approved)
        self.assertFalse(evaluate(entry=-5.0, stop_loss=1.0, account=account(), now=NOW).approved)

    def test_an_empty_account_is_refused(self) -> None:
        decision = evaluate(entry=1000.0, stop_loss=900.0, account=account(equity=0.0), now=NOW)
        self.assertFalse(decision.approved)

    def test_a_stale_snapshot_is_refused(self) -> None:
        # Sizing against an equity figure from ten minutes ago is how a bot
        # keeps buying after the account is already empty.
        stale = account(as_of=NOW - timedelta(minutes=10))
        decision = evaluate(entry=1000.0, stop_loss=900.0, account=stale, now=NOW)
        self.assertFalse(decision.approved)
        self.assertTrue(any("old" in r for r in decision.reasons))

    def test_a_snapshot_from_the_future_is_refused(self) -> None:
        skewed = account(as_of=NOW + timedelta(minutes=5))
        decision = evaluate(entry=1000.0, stop_loss=900.0, account=skewed, now=NOW)
        self.assertFalse(decision.approved)
        self.assertTrue(any("clocks disagree" in r for r in decision.reasons))

    def test_too_many_open_positions_is_refused(self) -> None:
        decision = evaluate(
            entry=1000.0, stop_loss=900.0, account=account(open_positions=3), now=NOW
        )
        self.assertFalse(decision.approved)
        self.assertTrue(any("already open" in r for r in decision.reasons))

    def test_a_dust_sized_order_is_refused(self) -> None:
        # Tiny equity makes the risk-sized notional fall under the venue floor.
        # The account is at its peak, so only the dust rule can reject it.
        decision = evaluate(
            entry=1000.0, stop_loss=500.0, account=account(equity=100.0), now=NOW
        )
        self.assertFalse(decision.approved)
        self.assertTrue(any("minimum" in r for r in decision.reasons))


class HaltTests(unittest.TestCase):
    def test_the_daily_loss_limit_halts_trading(self) -> None:
        hurt = account(realised_pnl_today=-400.0)  # 4% of 10 000, limit is 3%
        decision = evaluate(entry=1000.0, stop_loss=900.0, account=hurt, now=NOW)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.halt, Halt.DAILY_LOSS)

    def test_a_profitable_day_does_not_halt(self) -> None:
        up = account(realised_pnl_today=500.0)
        self.assertEqual(daily_loss_percent(up), 0.0)
        self.assertTrue(evaluate(entry=1000.0, stop_loss=900.0, account=up, now=NOW).approved)

    def test_the_drawdown_limit_halts_trading(self) -> None:
        sunk = account(equity=8_000.0, peak_equity=10_000.0)  # 20% down, limit 15%
        decision = evaluate(entry=1000.0, stop_loss=900.0, account=sunk, now=NOW)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.halt, Halt.DRAWDOWN)

    def test_the_cooldown_halts_trading_until_it_expires(self) -> None:
        paused = account(consecutive_losses=3, cooldown_until=NOW + timedelta(minutes=30))
        during = evaluate(entry=1000.0, stop_loss=900.0, account=paused, now=NOW)
        self.assertFalse(during.approved)
        self.assertEqual(during.halt, Halt.COOLDOWN)

        after = evaluate(
            entry=1000.0,
            stop_loss=900.0,
            account=replace_as_of(paused, NOW + timedelta(minutes=31)),
            now=NOW + timedelta(minutes=31),
        )
        self.assertTrue(after.approved)

    def test_the_kill_switch_outranks_everything(self) -> None:
        decision = evaluate(
            entry=1000.0, stop_loss=900.0, account=account(kill_switch=True), now=NOW
        )
        self.assertFalse(decision.approved)
        self.assertEqual(decision.halt, Halt.KILL_SWITCH)

    def test_a_healthy_account_is_not_halted(self) -> None:
        halt, reason = check_halt(account(), RiskConfig(), NOW)
        self.assertEqual(halt, Halt.NONE)
        self.assertIsNone(reason)


class MeasureTests(unittest.TestCase):
    def test_drawdown_is_zero_at_a_new_peak(self) -> None:
        self.assertEqual(drawdown_percent(account(equity=12_000.0, peak_equity=12_000.0)), 0.0)

    def test_drawdown_measures_the_fall_from_peak(self) -> None:
        self.assertAlmostEqual(
            drawdown_percent(account(equity=9_000.0, peak_equity=10_000.0)), 10.0
        )

    def test_kill_switch_trips_at_the_drawdown_limit(self) -> None:
        config = RiskConfig()
        self.assertFalse(
            should_trip_kill_switch(account(equity=9_000.0, peak_equity=10_000.0), config)
        )
        self.assertTrue(
            should_trip_kill_switch(account(equity=8_500.0, peak_equity=10_000.0), config)
        )


class StateTransitionTests(unittest.TestCase):
    def test_a_win_resets_the_losing_streak(self) -> None:
        state = account(consecutive_losses=2)
        after = state.with_trade_result(120.0, NOW, RiskConfig())
        self.assertEqual(after.consecutive_losses, 0)
        self.assertAlmostEqual(after.equity, 10_120.0)
        self.assertIsNone(after.cooldown_until)

    def test_a_losing_streak_arms_the_cooldown(self) -> None:
        config = RiskConfig()
        state = account()
        for _ in range(3):
            state = state.with_trade_result(-50.0, NOW, config)
        self.assertEqual(state.consecutive_losses, 3)
        self.assertIsNotNone(state.cooldown_until)
        self.assertEqual(state.cooldown_until, NOW + timedelta(minutes=config.cooldown_minutes))

    def test_two_losses_do_not_arm_the_cooldown(self) -> None:
        config = RiskConfig()
        state = account()
        for _ in range(2):
            state = state.with_trade_result(-50.0, NOW, config)
        self.assertIsNone(state.cooldown_until)

    def test_the_peak_only_ratchets_up(self) -> None:
        state = account().with_trade_result(500.0, NOW, RiskConfig())
        self.assertAlmostEqual(state.peak_equity, 10_500.0)
        after_loss = state.with_trade_result(-800.0, NOW, RiskConfig())
        self.assertAlmostEqual(after_loss.equity, 9_700.0)
        self.assertAlmostEqual(after_loss.peak_equity, 10_500.0)

    def test_daily_pnl_accumulates(self) -> None:
        config = RiskConfig()
        state = account().with_trade_result(-30.0, NOW, config).with_trade_result(-20.0, NOW, config)
        self.assertAlmostEqual(state.realised_pnl_today, -50.0)


class SerialisationTests(unittest.TestCase):
    def test_decision_is_json_safe(self) -> None:
        import json

        payload = evaluate(entry=1000.0, stop_loss=900.0, account=account(), now=NOW).to_dict()
        json.dumps(payload)
        self.assertTrue(payload["approved"])
        self.assertEqual(payload["halt"], "none")
        self.assertTrue(payload["reasons"])


def replace_as_of(state: AccountState, when: datetime) -> AccountState:
    from dataclasses import replace as _replace

    return _replace(state, as_of=when)


if __name__ == "__main__":
    unittest.main()
