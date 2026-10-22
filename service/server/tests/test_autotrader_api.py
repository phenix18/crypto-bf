"""The engine singleton and the HTTP surface over it.

Two things get the most attention: that the engine is genuinely one object with
one position book, and that the caller can tell "no setup" from "not allowed to
trade" — an interface where those two look the same is one nobody can debug.
"""

import json
import math
import os
import sys
import unittest

from fastapi.testclient import TestClient

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from broker import BrokerError, PaperBroker  # noqa: E402
from engine import (  # noqa: E402
    AutoTradeDisabled,
    EngineConfig,
    build_engine,
    get_engine,
    reset_engine,
)
from indicators import Candle  # noqa: E402
from market_data import MarketDataError, ReplaySource, SyntheticSource  # noqa: E402
from routes import create_app  # noqa: E402
from unittest import mock  # noqa: E402


def build(prices, wick=0.004):
    return [
        Candle(
            open_time=i * 300_000,
            open=c,
            high=c * (1 + wick),
            low=c * (1 - wick),
            close=c,
            volume=1000.0,
        )
        for i, c in enumerate(prices)
    ]


def uptrend(n=120, start=100.0, step=0.004):
    return [start * (1 + step) ** i + math.sin(i / 3.0) * start * 0.001 for i in range(n)]


def replay_source(symbols=("BTCUSDT",), interval="5m"):
    source = ReplaySource()
    for symbol in symbols:
        source.add(symbol, interval, build(uptrend()))
    return source


def engine_for(*, auto_trade=False, symbols=("BTCUSDT",), broker=None, source=None):
    config = EngineConfig(
        watchlist=tuple(symbols), interval="5m", auto_trade=auto_trade, equity=10_000.0
    )
    return build_engine(
        config,
        source=source or replay_source(symbols),
        broker=broker or PaperBroker(quote_balance=10_000.0),
    )


class ModeTests(unittest.TestCase):
    def test_the_defaults_are_the_timid_ones(self):
        mode = engine_for().mode()
        self.assertTrue(mode["paper"])
        self.assertTrue(mode["testnet"])
        self.assertFalse(mode["live"])
        self.assertFalse(mode["auto_trade"])
        self.assertFalse(mode["model_available"])

    def test_the_data_source_names_itself(self):
        engine = engine_for(source=SyntheticSource())
        self.assertEqual(engine.mode()["data_source"], "synthetic")


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine_for()
        self.payload = self.engine.chart("BTCUSDT", "5m", 120)

    def test_the_candles_come_back_oldest_first(self):
        times = [c["t"] for c in self.payload["candles"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(times), 120)

    def test_every_indicator_series_lines_up_with_the_candles(self):
        # A chart whose overlay is one bar out of step draws the stop in the
        # wrong place, which is the kind of lie a screen should not be able to
        # tell.
        count = len(self.payload["candles"])
        for name, series in self.payload["indicators"].items():
            with self.subTest(indicator=name):
                self.assertEqual(len(series), count)

    def test_the_warm_up_is_null_rather_than_zero(self):
        self.assertIsNone(self.payload["indicators"]["rsi"][0])

    def test_the_proposal_rides_along_with_the_chart(self):
        proposal = self.payload["proposal"]
        for key in ("direction", "confluence", "stop_loss", "take_profit", "votes", "readings"):
            self.assertIn(key, proposal)

    def test_the_levels_come_with_their_touch_counts(self):
        levels = self.payload["levels"]
        self.assertIn("support", levels)
        self.assertIn("resistance", levels)
        for level in levels["support"] + levels["resistance"]:
            self.assertIn("touches", level)

    def test_the_whole_payload_is_json_safe(self):
        self.assertIsInstance(json.dumps(self.payload), str)

    def test_a_closed_market_data_path_raises_rather_than_inventing_candles(self):
        with self.assertRaises(MarketDataError):
            engine_for().chart("DOGEUSDT", "5m", 100)


class DecisionTests(unittest.TestCase):
    def test_analysing_places_nothing(self):
        engine = engine_for()
        decision = engine.analyse("BTCUSDT")
        self.assertTrue(decision["approved"])
        self.assertEqual(engine.trader.broker.placed, [])

    def test_trading_is_refused_while_auto_trade_is_off(self):
        engine = engine_for(auto_trade=False)
        with self.assertRaises(AutoTradeDisabled):
            engine.trade("BTCUSDT")
        self.assertEqual(engine.trader.broker.placed, [])

    def test_trading_works_once_armed(self):
        engine = engine_for(auto_trade=True)
        decision = engine.trade("BTCUSDT")
        self.assertEqual(decision["stage"], "executed")
        self.assertEqual(len(engine.trader.broker.placed), 1)

    def test_a_sweep_with_auto_trade_off_only_reports(self):
        engine = engine_for(auto_trade=False, symbols=("BTCUSDT", "ETHUSDT"))
        decisions = engine.sweep()
        self.assertEqual(len(decisions), 2)
        self.assertEqual(engine.trader.broker.placed, [])

    def test_a_sweep_once_armed_trades(self):
        engine = engine_for(auto_trade=True, symbols=("BTCUSDT", "ETHUSDT"))
        decisions = engine.sweep()
        self.assertTrue(any(d["stage"] == "executed" for d in decisions))

    def test_the_engine_keeps_one_position_book(self):
        # Two engines would each size trades as though the other's exposure did
        # not exist; this is the test that the API path shares one.
        engine = engine_for(auto_trade=True)
        engine.trade("BTCUSDT")
        self.assertEqual(len(engine.status()["portfolio"]["open_positions"]), 1)
        self.assertEqual(len(engine.trader.portfolio.positions), 1)


class ControlTests(unittest.TestCase):
    def test_engaging_the_kill_switch_shows_up_in_the_book_and_the_audit(self):
        engine = engine_for()
        book = engine.set_kill_switch(True)
        self.assertTrue(book["kill_switch"])
        self.assertTrue(any(e["kind"] == "kill_switch" for e in engine.trader.audit))

    def test_the_kill_switch_stops_the_pipeline_at_the_risk_layer(self):
        engine = engine_for(auto_trade=True)
        engine.set_kill_switch(True)
        self.assertEqual(engine.analyse("BTCUSDT")["stage"], "risk")

    def test_closing_an_unheld_symbol_returns_nothing(self):
        self.assertIsNone(engine_for().close("ETHUSDT", 100.0))

    def test_closing_a_held_position_records_it(self):
        engine = engine_for(auto_trade=True)
        engine.trade("BTCUSDT")
        held = engine.trader.portfolio.positions["BTCUSDT"]
        closed = engine.close("BTCUSDT", held.entry)
        self.assertEqual(closed["reason"], "manual")
        self.assertEqual(engine.trader.portfolio.positions, {})

    def test_the_audit_trail_comes_back_newest_first(self):
        engine = engine_for(auto_trade=True)
        engine.trade("BTCUSDT")
        entries = engine.audit(10)["entries"]
        self.assertEqual(entries[0]["stage"], "executed")

    def test_the_calibration_view_reports_the_gate_in_force(self):
        payload = engine_for().calibration()
        self.assertIn("confluence_gate", payload["config"])
        self.assertEqual(payload["buckets"], [])


class EngineConfigTests(unittest.TestCase):
    def test_the_watchlist_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"WATCHLIST": " btcusdt , ethusdt "}, clear=True):
            self.assertEqual(EngineConfig.from_env().watchlist, ("BTCUSDT", "ETHUSDT"))

    def test_an_empty_watchlist_falls_back_to_the_default(self):
        with mock.patch.dict(os.environ, {"WATCHLIST": " , "}, clear=True):
            self.assertEqual(EngineConfig.from_env().watchlist, EngineConfig.watchlist)

    def test_auto_trading_is_off_unless_asked_for(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(EngineConfig.from_env().auto_trade)

    def test_an_unreadable_flag_keeps_auto_trading_off(self):
        with mock.patch.dict(os.environ, {"AUTO_TRADE_ENABLED": "ture"}, clear=True):
            self.assertFalse(EngineConfig.from_env().auto_trade)

    def test_auto_trading_can_be_armed(self):
        with mock.patch.dict(os.environ, {"AUTO_TRADE_ENABLED": "true"}, clear=True):
            self.assertTrue(EngineConfig.from_env().auto_trade)

    def test_the_confluence_gate_is_settable_from_the_environment(self):
        # The one number a user is most likely to want to move.
        with mock.patch.dict(os.environ, {"CONFLUENCE_GATE": "80"}, clear=True):
            config = EngineConfig.from_env()
        self.assertEqual(config.confluence_gate, 80.0)
        engine = build_engine(config, source=replay_source(), broker=PaperBroker())
        self.assertEqual(engine.trader.calibration_config.confluence_gate, 80.0)
        self.assertEqual(engine.calibration()["config"]["confluence_gate"], 80.0)

    def test_a_bad_gate_value_keeps_the_default(self):
        with mock.patch.dict(os.environ, {"CONFLUENCE_GATE": "high"}, clear=True):
            self.assertEqual(EngineConfig.from_env().confluence_gate, EngineConfig.confluence_gate)

    def test_a_bad_equity_value_does_not_crash_the_build(self):
        with mock.patch.dict(os.environ, {"PAPER_EQUITY": "lots"}, clear=True):
            self.assertEqual(EngineConfig.from_env().equity, EngineConfig.equity)


class SingletonTests(unittest.TestCase):
    def tearDown(self):
        reset_engine(None)

    def test_the_same_engine_comes_back_every_time(self):
        reset_engine(engine_for())
        self.assertIs(get_engine(), get_engine())

    def test_it_can_be_replaced_for_a_test(self):
        first = engine_for()
        reset_engine(first)
        self.assertIs(get_engine(), first)
        second = engine_for()
        reset_engine(second)
        self.assertIs(get_engine(), second)


# ------------------------------------------------------------------- the API --


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine_for(symbols=("BTCUSDT",))
        reset_engine(self.engine)
        self.client = TestClient(create_app())

    def tearDown(self):
        reset_engine(None)

    def arm(self):
        self.engine.config = EngineConfig(
            watchlist=self.engine.config.watchlist,
            interval=self.engine.config.interval,
            auto_trade=True,
            equity=self.engine.config.equity,
        )

    def test_status_reports_the_mode_and_the_book(self):
        payload = self.client.get("/api/auto/status").json()
        self.assertFalse(payload["mode"]["auto_trade"])
        self.assertIn("equity", payload["portfolio"])
        self.assertEqual(payload["watchlist"], ["BTCUSDT"])

    def test_the_chart_endpoint_serves_candles_and_overlays(self):
        response = self.client.get("/api/auto/chart", params={"symbol": "BTCUSDT", "limit": 90})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["candles"]), 90)
        self.assertEqual(len(payload["indicators"]["rsi"]), 90)

    def test_an_unavailable_market_is_a_503_with_the_reason(self):
        # Not a 500: the caller needs to know the feed is the problem.
        response = self.client.get("/api/auto/chart", params={"symbol": "NOPEUSDT"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("replay", response.json()["detail"])

    def test_analysing_returns_the_whole_decision(self):
        response = self.client.post("/api/auto/analyse", json={"symbol": "BTCUSDT"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["stage"], "approved")
        self.assertTrue(payload["council"]["opinions"])

    def test_a_nonsense_symbol_is_rejected_before_anything_runs(self):
        self.assertEqual(
            self.client.post("/api/auto/analyse", json={"symbol": "x"}).status_code, 422
        )

    def test_trading_while_disarmed_is_a_409_not_a_silent_refusal(self):
        response = self.client.post("/api/auto/trade", json={"symbol": "BTCUSDT"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("AUTO_TRADE_ENABLED", response.json()["detail"])

    def test_trading_once_armed_returns_the_orders(self):
        self.arm()
        response = self.client.post("/api/auto/trade", json={"symbol": "BTCUSDT"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["stage"], "executed")
        self.assertEqual(len(payload["orders"]), 2)

    def test_a_rejected_entry_comes_back_as_a_refused_decision(self):
        self.arm()
        with mock.patch.object(
            self.engine.trader.broker,
            "place_market_entry",
            side_effect=BrokerError("-1013 filter failure"),
        ):
            response = self.client.post("/api/auto/trade", json={"symbol": "BTCUSDT"})
        # The executor catches a rejected entry and reports it as a refused
        # decision, so the call itself succeeds and says why.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stage"], "execution")

    def test_releasing_the_kill_switch_needs_saying_so(self):
        response = self.client.post("/api/auto/kill-switch", json={"engaged": False})
        self.assertEqual(response.status_code, 400)
        self.assertIn("release", response.json()["detail"])

    def test_the_kill_switch_can_be_engaged_and_then_released(self):
        engaged = self.client.post("/api/auto/kill-switch", json={"engaged": True})
        self.assertTrue(engaged.json()["kill_switch"])
        released = self.client.post(
            "/api/auto/kill-switch", json={"engaged": False, "confirm": "release"}
        )
        self.assertFalse(released.json()["kill_switch"])

    def test_closing_nothing_is_a_404(self):
        response = self.client.post("/api/auto/close", json={"symbol": "BTCUSDT", "price": 100})
        self.assertEqual(response.status_code, 404)

    def test_closing_a_position_returns_the_trade(self):
        self.arm()
        self.client.post("/api/auto/trade", json={"symbol": "BTCUSDT"})
        held = self.engine.trader.portfolio.positions["BTCUSDT"]
        response = self.client.post(
            "/api/auto/close", json={"symbol": "BTCUSDT", "price": held.take_profit}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["outcome"], "win")

    def test_a_negative_price_is_rejected(self):
        self.assertEqual(
            self.client.post("/api/auto/close", json={"symbol": "BTCUSDT", "price": -1}).status_code,
            422,
        )

    def test_the_audit_endpoint_bounds_its_limit(self):
        self.assertEqual(self.client.get("/api/auto/audit", params={"limit": 501}).status_code, 422)
        self.assertEqual(self.client.get("/api/auto/audit", params={"limit": 10}).status_code, 200)

    def test_the_calibration_endpoint_answers_with_an_empty_record(self):
        payload = self.client.get("/api/auto/calibration").json()
        self.assertEqual(payload["buckets"], [])
        self.assertIn("min_sample", payload["config"])


if __name__ == "__main__":
    unittest.main()


class LoopTests(unittest.IsolatedAsyncioTestCase):
    """The background loop. Mostly: that it starts only when armed, and that
    one bad sweep does not end it — a dead loop leaves open positions with
    nobody watching them."""

    def setUp(self):
        import logging

        self.logger = logging.getLogger("test-auto-loop")
        self.logger.addHandler(logging.NullHandler())

    def tearDown(self):
        reset_engine(None)

    def test_nothing_starts_while_disarmed(self):
        import tasks_autotrader

        reset_engine(engine_for(auto_trade=False))
        self.assertEqual(tasks_autotrader.start_auto_trader(self.logger), [])

    def test_the_period_has_a_floor(self):
        import tasks_autotrader

        with mock.patch.dict(os.environ, {"AUTO_TRADE_PERIOD_SECONDS": "0.2"}, clear=True):
            self.assertEqual(
                tasks_autotrader.period_seconds(), tasks_autotrader.MIN_PERIOD_SECONDS
            )

    def test_an_unreadable_period_uses_the_default(self):
        import tasks_autotrader

        with mock.patch.dict(os.environ, {"AUTO_TRADE_PERIOD_SECONDS": "soon"}, clear=True):
            self.assertEqual(
                tasks_autotrader.period_seconds(), tasks_autotrader.DEFAULT_PERIOD_SECONDS
            )

    async def test_one_cycle_sweeps_the_watchlist(self):
        import tasks_autotrader

        engine = engine_for(auto_trade=True)
        done = await tasks_autotrader.auto_trade_loop(self.logger, engine, iterations=1)
        self.assertEqual(done, 1)
        self.assertEqual(len(engine.trader.portfolio.positions), 1)

    async def test_a_failing_sweep_does_not_end_the_loop(self):
        import tasks_autotrader

        engine = engine_for(auto_trade=True)
        with mock.patch.object(engine, "sweep", side_effect=RuntimeError("exchange down")):
            done = await tasks_autotrader.auto_trade_loop(self.logger, engine, iterations=2)
        self.assertEqual(done, 2)

    async def test_the_loop_can_be_cancelled(self):
        import asyncio

        import tasks_autotrader

        engine = engine_for(auto_trade=True)
        with mock.patch.object(tasks_autotrader, "period_seconds", return_value=15.0):
            task = asyncio.create_task(tasks_autotrader.auto_trade_loop(self.logger, engine))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
