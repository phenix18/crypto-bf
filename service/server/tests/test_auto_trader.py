"""The loop, end to end on replayed candles.

These are the integration tests: real indicators, real risk arithmetic, real
calibration, a paper broker. Nothing here is mocked except the failures that
cannot be produced on purpose otherwise — a rejected order, a stop that will
not place.

The ones worth reading first are the refusals. Every gate in the pipeline can
only subtract, so each test that proves a gate still bites is a test that the
thing cannot trade when it should not.
"""

import math
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from ai_agents import CouncilConfig  # noqa: E402
from auto_trader import AutoTrader, Portfolio, Position, build_auto_trader  # noqa: E402
from broker import BrokerError, InstrumentRules, PaperBroker, Side  # noqa: E402
from calibration import CalibrationConfig, Outcome, TradeRecord  # noqa: E402
from indicators import Candle  # noqa: E402
from market_data import MarketDataError, ReplaySource  # noqa: E402
from risk_manager import RiskConfig  # noqa: E402

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def build(prices, wick=0.004, start_time=0, step=300_000):
    return [
        Candle(
            open_time=start_time + i * step,
            open=close,
            high=close * (1 + wick),
            low=close * (1 - wick),
            close=close,
            volume=1000.0,
        )
        for i, close in enumerate(prices)
    ]


def uptrend(n=120, start=100.0, step=0.004):
    return [start * (1 + step) ** i + math.sin(i / 3.0) * start * 0.001 for i in range(n)]


def chop(n=120, start=100.0, amplitude=0.006):
    return [start * (1 + math.sin(i / 2.0) * amplitude) for i in range(n)]


def source_for(prices, symbol="BTCUSDT", interval="5m"):
    source = ReplaySource()
    source.add(symbol, interval, build(prices))
    return source


def trader(prices=None, equity=10_000.0, **kwargs):
    source = kwargs.pop("source", None) or source_for(prices or uptrend())
    built = build_auto_trader(source=source, equity=equity)
    for key, value in kwargs.items():
        setattr(built, key, value)
    return built


def position(**overrides):
    base = dict(
        symbol="BTCUSDT",
        interval="5m",
        direction="long",
        entry=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        quantity=5.0,
        opened_at=NOW,
        bar_open_time=1_000,
        confluence=70.0,
        regime="trending_up",
        entry_order_id="at-1",
    )
    base.update(overrides)
    return Position(**base)


class FakeBroker(PaperBroker):
    """A paper broker that can be told to fail at a chosen step."""

    def __init__(self, *, entry_status="FILLED", protective_error=None, unwind_error=None, **kw):
        super().__init__(**kw)
        self.entry_status = entry_status
        self.protective_error = protective_error
        self.unwind_error = unwind_error
        self.cancelled = []

    def place_market_entry(self, *, symbol, side, quantity, client_order_id):
        if self.unwind_error and client_order_id.endswith("-u"):
            raise BrokerError(self.unwind_error)
        receipt = super().place_market_entry(
            symbol=symbol, side=side, quantity=quantity, client_order_id=client_order_id
        )
        if self.entry_status != "FILLED" and not client_order_id.endswith("-u"):
            return type(receipt)(
                symbol=receipt.symbol,
                side=receipt.side,
                client_order_id=receipt.client_order_id,
                order_id=receipt.order_id,
                status=self.entry_status,
                quantity=receipt.quantity,
                filled_quantity=0.0,
                average_price=None,
                raw={},
            )
        return receipt

    def place_protective_exit(self, **kwargs):
        if self.protective_error:
            raise BrokerError(self.protective_error)
        return super().place_protective_exit(**kwargs)

    def cancel_all(self, symbol):
        self.cancelled.append(symbol)
        super().cancel_all(symbol)


# ------------------------------------------------------------------ portfolio --


class PortfolioTests(unittest.TestCase):
    def test_the_peak_starts_at_the_opening_equity(self):
        book = Portfolio(equity=5_000.0)
        self.assertEqual(book.peak_equity, 5_000.0)
        self.assertEqual(book.starting_equity, 5_000.0)

    def test_the_account_snapshot_reflects_the_book(self):
        book = Portfolio(equity=1_000.0)
        book.open(position())
        state = book.account_state(NOW)
        self.assertEqual(state.open_positions, 1)
        self.assertEqual(state.as_of, NOW)
        self.assertEqual(state.equity, 1_000.0)

    def test_two_positions_in_one_symbol_are_refused(self):
        book = Portfolio(equity=1_000.0)
        book.open(position())
        with self.assertRaises(ValueError):
            book.open(position())

    def test_a_win_raises_equity_and_the_peak(self):
        book = Portfolio(equity=1_000.0)
        book.open(position())
        trade = book.close(position(), exit_price=104.0, now=NOW, reason="target")
        self.assertEqual(trade.outcome, Outcome.WIN)
        self.assertAlmostEqual(trade.pnl, 20.0)
        self.assertAlmostEqual(trade.r_multiple, 2.0)
        self.assertAlmostEqual(book.equity, 1_020.0)
        self.assertAlmostEqual(book.peak_equity, 1_020.0)

    def test_a_loss_lowers_equity_but_not_the_peak(self):
        book = Portfolio(equity=1_000.0)
        book.open(position())
        trade = book.close(position(), exit_price=98.0, now=NOW, reason="stop")
        self.assertEqual(trade.outcome, Outcome.LOSS)
        self.assertAlmostEqual(trade.r_multiple, -1.0)
        self.assertAlmostEqual(book.equity, 990.0)
        self.assertAlmostEqual(book.peak_equity, 1_000.0)

    def test_a_losing_run_arms_the_cooldown(self):
        book = Portfolio(equity=10_000.0)
        for _ in range(3):
            book.open(position())
            book.close(position(), exit_price=98.0, now=NOW, reason="stop")
        self.assertEqual(book.consecutive_losses, 3)
        self.assertIsNotNone(book.cooldown_until)

    def test_a_win_resets_the_losing_run(self):
        book = Portfolio(equity=10_000.0)
        book.open(position())
        book.close(position(), exit_price=98.0, now=NOW, reason="stop")
        book.open(position())
        book.close(position(), exit_price=104.0, now=NOW, reason="target")
        self.assertEqual(book.consecutive_losses, 0)

    def test_a_deep_drawdown_latches_the_kill_switch(self):
        book = Portfolio(equity=1_000.0)
        book.open(position(quantity=100.0))
        book.close(position(quantity=100.0), exit_price=98.0, now=NOW, reason="stop")
        self.assertTrue(book.kill_switch)
        # And it stays on: a recovery does not clear it, a human does.
        book.open(position())
        book.close(position(), exit_price=104.0, now=NOW, reason="target")
        self.assertTrue(book.kill_switch)

    def test_the_day_rolls_over_at_midnight_utc(self):
        # Without this the daily-loss halt becomes a permanent one.
        book = Portfolio(equity=1_000.0, realised_pnl_today=-50.0)
        book.account_state(NOW)
        self.assertAlmostEqual(book.realised_pnl_today, -50.0)
        book.account_state(NOW + timedelta(days=1))
        self.assertEqual(book.realised_pnl_today, 0.0)

    def test_the_day_does_not_roll_within_one_day(self):
        book = Portfolio(equity=1_000.0, realised_pnl_today=-50.0)
        book.account_state(NOW)
        book.account_state(NOW + timedelta(hours=6))
        self.assertAlmostEqual(book.realised_pnl_today, -50.0)

    def test_the_summary_carries_what_the_interface_needs(self):
        book = Portfolio(equity=1_000.0)
        book.open(position())
        payload = book.to_dict()
        for key in ("equity", "peak_equity", "total_pnl", "drawdown_percent", "kill_switch"):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["open_positions"]), 1)


# ------------------------------------------------------------------- pipeline --


class EvaluateTests(unittest.TestCase):
    def test_a_clean_trend_reaches_the_council_and_is_approved(self):
        decision = trader().evaluate("BTCUSDT", "5m", now=NOW)
        self.assertTrue(decision.approved)
        self.assertEqual(decision.stage, "approved")
        self.assertGreater(decision.quantity, 0.0)
        for layer in ("proposal", "risk", "calibration", "council"):
            self.assertTrue(getattr(decision, layer), layer)

    def test_a_directionless_market_stops_at_the_strategy(self):
        decision = trader(chop()).evaluate("BTCUSDT", "5m", now=NOW)
        self.assertFalse(decision.approved)
        self.assertEqual(decision.stage, "strategy")
        self.assertEqual(decision.risk, {})

    def test_the_kill_switch_stops_it_at_the_risk_layer(self):
        bot = trader()
        bot.portfolio.kill_switch = True
        decision = bot.evaluate("BTCUSDT", "5m", now=NOW)
        self.assertEqual(decision.stage, "risk")
        self.assertEqual(decision.calibration, {})
        self.assertTrue(any("kill switch" in r for r in decision.reasons))

    def test_an_unreachable_gate_stops_it_at_calibration(self):
        bot = trader(calibration_config=CalibrationConfig(confluence_gate=99.0))
        decision = bot.evaluate("BTCUSDT", "5m", now=NOW)
        self.assertEqual(decision.stage, "calibration")
        self.assertEqual(decision.council, {})

    def test_an_unreachable_conviction_floor_stops_it_at_the_council(self):
        bot = trader(council_config=CouncilConfig(min_conviction=0.99))
        decision = bot.evaluate("BTCUSDT", "5m", now=NOW)
        self.assertEqual(decision.stage, "council")
        self.assertFalse(decision.approved)
        self.assertTrue(decision.council["opinions"])

    def test_evaluating_places_no_orders(self):
        bot = trader()
        bot.evaluate("BTCUSDT", "5m", now=NOW)
        self.assertEqual(bot.broker.placed, [])
        self.assertEqual(bot.portfolio.positions, {})

    def test_a_losing_record_in_this_bucket_closes_the_gate(self):
        # The learning loop, from the other end: once the bucket has a real
        # sample and that sample loses, the same candles stop being tradable.
        bot = trader()
        allowed = bot.evaluate("BTCUSDT", "5m", now=NOW)
        self.assertTrue(allowed.approved)

        confluence = allowed.proposal["confluence"]
        bot.history.extend(
            TradeRecord(
                symbol="BTCUSDT",
                interval="5m",
                regime="trending_up",
                direction="long",
                confluence=confluence,
                outcome=Outcome.LOSS,
                r_multiple=-1.0,
            )
            for _ in range(30)
        )
        refused = bot.evaluate("BTCUSDT", "5m", now=NOW)
        self.assertEqual(refused.stage, "calibration")
        self.assertEqual(refused.calibration["basis"], "calibrated")

    def test_the_decision_serialises(self):
        payload = trader().evaluate("BTCUSDT", "5m", now=NOW).to_dict()
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertIn("at", payload)
        self.assertIsInstance(payload["reasons"], list)


# ------------------------------------------------------------------ execution --


class TradeTests(unittest.TestCase):
    def test_an_approved_trade_places_the_entry_and_the_stop_together(self):
        bot = trader()
        decision = bot.trade("BTCUSDT", "5m", now=NOW)
        self.assertEqual(decision.stage, "executed")
        self.assertEqual(len(decision.orders), 2)
        entry, protective = decision.orders
        self.assertEqual(entry["side"], "BUY")
        self.assertEqual(protective["side"], "SELL")
        self.assertEqual(len(bot.portfolio.positions), 1)

    def test_the_position_records_the_levels_it_was_opened_under(self):
        bot = trader()
        bot.trade("BTCUSDT", "5m", now=NOW)
        held = bot.portfolio.positions["BTCUSDT"]
        self.assertEqual(held.regime, "trending_up")
        self.assertGreater(held.confluence, 0.0)
        self.assertLess(held.stop_loss, held.entry)
        self.assertGreater(held.take_profit, held.entry)

    def test_the_same_bar_is_never_traded_twice(self):
        # A loop that runs three times inside one candle must still place one
        # order.
        bot = trader()
        bot.trade("BTCUSDT", "5m", now=NOW)
        bot.portfolio.positions.clear()  # pretend it closed, keep the bar
        again = bot.trade("BTCUSDT", "5m", now=NOW)
        self.assertEqual(again.stage, "bar")
        self.assertEqual(len(bot.broker.placed), 1)

    def test_a_held_symbol_is_not_traded_again(self):
        bot = trader()
        bot.trade("BTCUSDT", "5m", now=NOW)
        bot.traded_bars.clear()  # only the open position should stop it
        again = bot.trade("BTCUSDT", "5m", now=NOW)
        self.assertEqual(again.stage, "position")
        self.assertEqual(len(bot.broker.placed), 1)

    def test_an_order_below_the_exchange_minimum_is_refused_cleanly(self):
        broker = FakeBroker(quote_balance=10_000.0)
        broker.instrument_rules["BTCUSDT"] = InstrumentRules(
            symbol="BTCUSDT", step_size=0.001, tick_size=0.01, min_notional=10_000_000.0
        )
        bot = trader()
        bot.broker = broker
        decision = bot.trade("BTCUSDT", "5m", now=NOW)
        self.assertEqual(decision.stage, "execution")
        self.assertTrue(any("exchange filters" in r for r in decision.reasons))
        self.assertEqual(bot.portfolio.positions, {})
        self.assertEqual(broker.placed, [])

    def test_a_quantity_that_rounds_to_nothing_is_refused(self):
        broker = FakeBroker(quote_balance=10_000.0)
        broker.instrument_rules["BTCUSDT"] = InstrumentRules(
            symbol="BTCUSDT", step_size=1_000.0, tick_size=0.01
        )
        bot = trader()
        bot.broker = broker
        decision = bot.trade("BTCUSDT", "5m", now=NOW)
        self.assertEqual(decision.stage, "execution")
        self.assertEqual(broker.placed, [])

    def test_an_entry_that_does_not_fill_opens_no_position(self):
        bot = trader()
        bot.broker = FakeBroker(quote_balance=10_000.0, entry_status="EXPIRED")
        decision = bot.trade("BTCUSDT", "5m", now=NOW)
        self.assertEqual(decision.stage, "execution")
        self.assertTrue(any("did not fill" in r for r in decision.reasons))
        self.assertEqual(bot.portfolio.positions, {})
        self.assertEqual(bot.broker.protective, [])

    def test_a_failed_stop_closes_the_position_immediately(self):
        # Unprotected is the worst state in the system — worse than a small
        # realised loss.
        bot = trader()
        bot.broker = FakeBroker(quote_balance=10_000.0, protective_error="-2010 too many orders")
        decision = bot.trade("BTCUSDT", "5m", now=NOW)
        self.assertEqual(decision.stage, "execution")
        self.assertTrue(any("closed immediately" in r for r in decision.reasons))
        self.assertEqual(bot.portfolio.positions, {})
        sides = [o["side"] for o in decision.orders]
        self.assertEqual(sides, ["BUY", "SELL"])

    def test_a_failed_stop_and_a_failed_unwind_engage_the_kill_switch(self):
        bot = trader()
        bot.broker = FakeBroker(
            quote_balance=10_000.0,
            protective_error="stop rejected",
            unwind_error="exchange down",
        )
        decision = bot.trade("BTCUSDT", "5m", now=NOW)
        self.assertFalse(decision.approved)
        self.assertTrue(bot.portfolio.kill_switch)
        shouts = [a for a in bot.audit if a["kind"] == "unprotected_position"]
        self.assertEqual(len(shouts), 1)
        self.assertIn("needs a human", shouts[0]["message"])

    def test_a_refusal_before_the_orders_leaves_the_bar_tradable(self):
        # The bar is only burned once an order has actually gone out.
        bot = trader(council_config=CouncilConfig(min_conviction=0.99))
        bot.trade("BTCUSDT", "5m", now=NOW)
        self.assertEqual(bot.traded_bars, set())

    def test_every_decision_lands_in_the_audit_trail(self):
        bot = trader()
        bot.trade("BTCUSDT", "5m", now=NOW)
        kinds = [entry["kind"] for entry in bot.audit]
        self.assertEqual(kinds, ["decision", "decision"])
        self.assertEqual(bot.audit[-1]["stage"], "executed")

    def test_the_audit_trail_is_bounded(self):
        bot = trader()
        for index in range(2_100):
            bot._note({"kind": "noise", "index": index})
        self.assertEqual(len(bot.audit), 2_000)
        self.assertEqual(bot.audit[-1]["index"], 2_099)


# ------------------------------------------------------------------- settling --


class SettleTests(unittest.TestCase):
    def open_a_trade(self, bot=None):
        bot = bot or trader()
        bot.trade("BTCUSDT", "5m", now=NOW)
        return bot, bot.portfolio.positions["BTCUSDT"]

    def test_a_stop_touch_closes_the_trade_as_a_loss(self):
        bot, held = self.open_a_trade()
        bar = Candle(
            open_time=held.bar_open_time + 300_000,
            open=held.entry,
            high=held.entry,
            low=held.stop_loss - 0.5,
            close=held.stop_loss,
            volume=1.0,
        )
        closed = bot.settle("BTCUSDT", "5m", [bar], now=NOW)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].outcome, Outcome.LOSS)
        self.assertAlmostEqual(closed[0].r_multiple, -1.0, places=6)
        self.assertEqual(bot.portfolio.positions, {})

    def test_a_target_touch_closes_the_trade_as_a_win(self):
        bot, held = self.open_a_trade()
        bar = Candle(
            open_time=held.bar_open_time + 300_000,
            open=held.entry,
            high=held.take_profit + 0.5,
            low=held.entry,
            close=held.take_profit,
            volume=1.0,
        )
        closed = bot.settle("BTCUSDT", "5m", [bar], now=NOW)
        self.assertEqual(closed[0].outcome, Outcome.WIN)
        self.assertGreater(closed[0].r_multiple, 1.0)

    def test_a_bar_that_covers_both_levels_is_scored_as_the_loss(self):
        # The bar does not say which came first. Assuming the good one is how
        # a backtest, and then the calibration layer reading it, goes
        # optimistic.
        bot, held = self.open_a_trade()
        bar = Candle(
            open_time=held.bar_open_time + 300_000,
            open=held.entry,
            high=held.take_profit + 1.0,
            low=held.stop_loss - 1.0,
            close=held.entry,
            volume=1.0,
        )
        closed = bot.settle("BTCUSDT", "5m", [bar], now=NOW)
        self.assertEqual(closed[0].outcome, Outcome.LOSS)
        self.assertEqual(closed[0].reason, "stop")

    def test_the_entry_bar_cannot_resolve_the_trade(self):
        bot, held = self.open_a_trade()
        bar = Candle(
            open_time=held.bar_open_time,
            open=held.entry,
            high=held.take_profit + 5,
            low=held.stop_loss - 5,
            close=held.entry,
            volume=1.0,
        )
        self.assertEqual(bot.settle("BTCUSDT", "5m", [bar], now=NOW), [])
        self.assertIn("BTCUSDT", bot.portfolio.positions)

    def test_a_quiet_bar_leaves_the_trade_open(self):
        bot, held = self.open_a_trade()
        bar = Candle(
            open_time=held.bar_open_time + 300_000,
            open=held.entry,
            high=held.entry * 1.0005,
            low=held.entry * 0.9995,
            close=held.entry,
            volume=1.0,
        )
        self.assertEqual(bot.settle("BTCUSDT", "5m", [bar], now=NOW), [])
        self.assertIn("BTCUSDT", bot.portfolio.positions)

    def test_settling_cancels_the_resting_orders(self):
        broker = FakeBroker(quote_balance=10_000.0)
        bot = trader()
        bot.broker = broker
        bot, held = self.open_a_trade(bot)
        bar = Candle(
            open_time=held.bar_open_time + 300_000,
            open=held.entry,
            high=held.entry,
            low=held.stop_loss - 1,
            close=held.stop_loss,
            volume=1.0,
        )
        bot.settle("BTCUSDT", "5m", [bar], now=NOW)
        self.assertIn("BTCUSDT", broker.cancelled)
        self.assertEqual(broker.open_orders("BTCUSDT"), [])

    def test_a_closed_trade_joins_the_history_the_gate_reads(self):
        bot, held = self.open_a_trade()
        bar = Candle(
            open_time=held.bar_open_time + 300_000,
            open=held.entry,
            high=held.entry,
            low=held.stop_loss - 1,
            close=held.stop_loss,
            volume=1.0,
        )
        bot.settle("BTCUSDT", "5m", [bar], now=NOW)
        self.assertEqual(len(bot.history), 1)
        record = bot.history[0]
        self.assertEqual(record.regime, "trending_up")
        self.assertEqual(record.direction, "long")
        self.assertEqual(record.outcome, Outcome.LOSS)

    def test_settling_nothing_is_a_no_op(self):
        bot = trader()
        self.assertEqual(bot.settle("BTCUSDT", "5m", build(uptrend(5)), now=NOW), [])

    def test_closing_by_hand_records_the_trade(self):
        bot, held = self.open_a_trade()
        trade = bot.close_now("BTCUSDT", held.entry * 1.01, now=NOW)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.reason, "manual")
        self.assertEqual(len(bot.history), 1)
        self.assertEqual(bot.portfolio.positions, {})

    def test_closing_a_symbol_that_is_not_held_returns_nothing(self):
        self.assertIsNone(trader().close_now("ETHUSDT", 100.0, now=NOW))


# ----------------------------------------------------------------- the sweep --


class RunOnceTests(unittest.TestCase):
    def test_every_symbol_on_the_watchlist_gets_a_decision(self):
        source = ReplaySource()
        source.add("BTCUSDT", "5m", build(uptrend()))
        source.add("ETHUSDT", "5m", build(chop()))
        bot = trader(source=source)
        decisions = bot.run_once(["BTCUSDT", "ETHUSDT"], "5m", now=NOW)
        self.assertEqual([d.symbol for d in decisions], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(decisions[0].stage, "executed")
        self.assertEqual(decisions[1].stage, "strategy")

    def test_one_broken_symbol_does_not_stop_the_sweep(self):
        source = ReplaySource()
        source.add("BTCUSDT", "5m", build(uptrend()))
        bot = trader(source=source)
        decisions = bot.run_once(["MISSINGUSDT", "BTCUSDT"], "5m", now=NOW)
        self.assertEqual(decisions[0].stage, "error")
        self.assertIn("MarketDataError", decisions[0].reasons[0])
        self.assertEqual(decisions[1].stage, "executed")

    def test_the_open_position_limit_caps_the_sweep(self):
        source = ReplaySource()
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"):
            source.add(symbol, "5m", build(uptrend()))
        bot = trader(source=source, risk_config=RiskConfig(max_open_positions=2))
        decisions = bot.run_once(["BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"], "5m", now=NOW)
        executed = [d for d in decisions if d.stage == "executed"]
        self.assertEqual(len(executed), 2)
        self.assertEqual(len(bot.portfolio.positions), 2)
        self.assertTrue(any(d.stage == "risk" for d in decisions))


class BuildTests(unittest.TestCase):
    def test_the_default_wiring_is_paper_with_no_model(self):
        bot = build_auto_trader(source=source_for(uptrend()))
        self.assertIsInstance(bot.broker, PaperBroker)
        self.assertFalse(bot.llm_client.available)
        self.assertEqual(bot.portfolio.equity, 10_000.0)


if __name__ == "__main__":
    unittest.main()


class StageContractTests(unittest.TestCase):
    """The list of stages, pinned on both sides of the HTTP boundary.

    The interface has a label per stage. Adding one here without adding it
    there leaves a reader who chose French or Chinese looking at a raw English
    word, and nothing fails to build.
    """

    def source(self) -> str:
        import auto_trader

        return open(auto_trader.__file__, encoding="utf-8").read()

    def emitted_stages(self) -> set:
        """Every stage literal in the module, however it is written.

        Three spellings, because the scan missed one the first time it ran and
        a scan that silently misses a case is worse than no scan: the keyword
        argument, the branches of the ternary that picks between council and
        approved, and the two refusals that pass the stage positionally.
        """
        import re

        text = self.source()
        found = set(re.findall(r'stage="([a-z]+)"', text))
        for line in text.splitlines():
            if "stage=" in line:
                found.update(re.findall(r'else "([a-z]+)"', line))
        found.update(re.findall(r'now, "([a-z]+)", \[', text))
        return found

    def test_every_stage_the_code_emits_is_declared(self):
        from auto_trader import STAGES

        missing = self.emitted_stages() - set(STAGES)
        self.assertEqual(missing, set(), f"undeclared stages: {sorted(missing)}")

    def test_no_declared_stage_has_gone_stale(self):
        from auto_trader import STAGES

        unused = set(STAGES) - self.emitted_stages()
        self.assertEqual(unused, set(), f"declared but never emitted: {sorted(unused)}")

    def test_the_scan_actually_finds_something(self):
        # Guard against the two tests above passing because the regex matched
        # nothing at all.
        self.assertGreaterEqual(len(self.emitted_stages()), 8)

    def test_a_real_decision_reports_a_declared_stage(self):
        from auto_trader import STAGES

        bot = trader()
        for decision in (
            bot.evaluate("BTCUSDT", "5m", now=NOW),
            bot.trade("BTCUSDT", "5m", now=NOW),
            bot.trade("BTCUSDT", "5m", now=NOW),
        ):
            with self.subTest(stage=decision.stage):
                self.assertIn(decision.stage, STAGES)
