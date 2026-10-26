"""The order layer. These are the tests that stand between a bug and a balance.

Three properties get the most attention, because each one has a well-known way
of going wrong in production: live mode must take two deliberate switches, a
quantity must never round up, and a retried signal must not open a second
position.
"""

import hashlib
import hmac
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.parse
from unittest import mock

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from broker import (  # noqa: E402
    MAINNET_BASE,
    MAX_CLIENT_ORDER_ID,
    TESTNET_BASE,
    BinanceSpotBroker,
    BrokerError,
    InstrumentRules,
    LiveTradingNotArmed,
    PaperBroker,
    Side,
    _format_decimal,
    build_broker,
    client_order_id,
    side_for,
)


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b'{"code":-2010,"msg":"insufficient balance"}'):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


BTC_RULES = InstrumentRules(
    symbol="BTCUSDT", step_size=0.00001, tick_size=0.01, min_quantity=0.00001, min_notional=10.0
)


class SideTests(unittest.TestCase):
    def test_long_buys_and_short_sells(self):
        self.assertEqual(side_for("long"), Side.BUY)
        self.assertEqual(side_for("SHORT"), Side.SELL)

    def test_flat_has_no_side(self):
        with self.assertRaises(BrokerError):
            side_for("flat")

    def test_nonsense_has_no_side(self):
        with self.assertRaises(BrokerError):
            side_for("")


class InstrumentRulesTests(unittest.TestCase):
    def test_a_quantity_rounds_down(self):
        # Rounding up would spend more than the risk layer authorised.
        rules = InstrumentRules(symbol="X", step_size=0.001)
        self.assertAlmostEqual(rules.round_quantity(1.23456), 1.234)

    def test_a_quantity_already_on_the_step_is_untouched(self):
        rules = InstrumentRules(symbol="X", step_size=0.001)
        self.assertAlmostEqual(rules.round_quantity(1.234), 1.234)

    def test_a_quantity_below_one_step_rounds_to_zero(self):
        rules = InstrumentRules(symbol="X", step_size=0.1)
        self.assertEqual(rules.round_quantity(0.05), 0.0)

    def test_a_whole_lot_step_floors_to_an_integer(self):
        rules = InstrumentRules(symbol="X", step_size=1.0)
        self.assertEqual(rules.round_quantity(7.9), 7.0)

    def test_a_price_rounds_to_the_nearest_tick(self):
        rules = InstrumentRules(symbol="X", tick_size=0.5)
        self.assertAlmostEqual(rules.round_price(100.4), 100.5)
        self.assertAlmostEqual(rules.round_price(100.2), 100.0)

    def test_a_zero_step_leaves_values_alone(self):
        rules = InstrumentRules(symbol="X", step_size=0.0, tick_size=0.0)
        self.assertEqual(rules.round_quantity(1.23456789), 1.23456789)
        self.assertEqual(rules.round_price(1.23456789), 1.23456789)

    def test_a_zero_quantity_is_rejected(self):
        self.assertIn("rounds to", BTC_RULES.reject_reason(0.0, 50_000.0))

    def test_a_quantity_below_the_minimum_is_rejected(self):
        rules = InstrumentRules(symbol="X", min_quantity=1.0, min_notional=0.0)
        self.assertIn("below the 1.0 minimum", rules.reject_reason(0.5, 100.0))

    def test_a_notional_below_the_minimum_is_rejected(self):
        self.assertIn("notional", BTC_RULES.reject_reason(0.0001, 50.0))

    def test_a_good_order_has_no_reason_to_reject(self):
        self.assertIsNone(BTC_RULES.reject_reason(0.001, 50_000.0))

    def test_filters_are_read_off_exchange_info(self):
        rules = InstrumentRules.from_exchange_info(
            {
                "symbol": "ETHUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00010000", "minQty": "0.00100000"},
                    {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
                ],
            }
        )
        self.assertEqual(rules.symbol, "ETHUSDT")
        self.assertAlmostEqual(rules.tick_size, 0.01)
        self.assertAlmostEqual(rules.step_size, 0.0001)
        self.assertAlmostEqual(rules.min_quantity, 0.001)
        self.assertAlmostEqual(rules.min_notional, 5.0)

    def test_the_older_min_notional_filter_name_is_understood(self):
        rules = InstrumentRules.from_exchange_info(
            {"symbol": "X", "filters": [{"filterType": "MIN_NOTIONAL", "minNotional": "10"}]}
        )
        self.assertAlmostEqual(rules.min_notional, 10.0)

    def test_missing_filters_fall_back_to_safe_defaults(self):
        rules = InstrumentRules.from_exchange_info({"symbol": "X", "filters": []})
        self.assertGreater(rules.step_size, 0)
        self.assertGreater(rules.tick_size, 0)


class ClientOrderIdTests(unittest.TestCase):
    def kwargs(self, **overrides):
        base = {
            "symbol": "BTCUSDT",
            "direction": "long",
            "interval": "5m",
            "bar_open_time": 1_700_000_000_000,
        }
        base.update(overrides)
        return base

    def test_the_same_setup_gives_the_same_id(self):
        # This is the idempotency guarantee: a retry after a timeout re-sends
        # the same id and the exchange refuses the duplicate.
        self.assertEqual(
            client_order_id(**self.kwargs()), client_order_id(**self.kwargs())
        )

    def test_a_different_bar_gives_a_different_id(self):
        self.assertNotEqual(
            client_order_id(**self.kwargs()),
            client_order_id(**self.kwargs(bar_open_time=1_700_000_300_000)),
        )

    def test_a_different_direction_gives_a_different_id(self):
        self.assertNotEqual(
            client_order_id(**self.kwargs()),
            client_order_id(**self.kwargs(direction="short")),
        )

    def test_a_different_symbol_gives_a_different_id(self):
        self.assertNotEqual(
            client_order_id(**self.kwargs()),
            client_order_id(**self.kwargs(symbol="ETHUSDT")),
        )

    def test_a_different_interval_gives_a_different_id(self):
        self.assertNotEqual(
            client_order_id(**self.kwargs()), client_order_id(**self.kwargs(interval="15m"))
        )

    def test_it_fits_the_exchange_limit(self):
        got = client_order_id(**self.kwargs(symbol="1000SATSUSDT"))
        self.assertLessEqual(len(got), MAX_CLIENT_ORDER_ID)

    def test_it_uses_only_characters_binance_accepts(self):
        got = client_order_id(**self.kwargs())
        self.assertTrue(all(c.isalnum() or c in "-_" for c in got))

    def test_the_case_of_the_symbol_does_not_matter(self):
        self.assertEqual(
            client_order_id(**self.kwargs(symbol="btcusdt")),
            client_order_id(**self.kwargs(symbol="BTCUSDT")),
        )


class FormatDecimalTests(unittest.TestCase):
    def test_small_numbers_do_not_become_scientific_notation(self):
        # Binance rejects "1e-05" outright, and the loop would silently never
        # place an order on a cheap asset.
        self.assertEqual(_format_decimal(0.00001), "0.00001")

    def test_trailing_zeros_are_trimmed(self):
        self.assertEqual(_format_decimal(1.50000000), "1.5")

    def test_a_whole_number_keeps_no_point(self):
        self.assertEqual(_format_decimal(42.0), "42")


class PaperBrokerTests(unittest.TestCase):
    def broker(self):
        return PaperBroker(quote_balance=1000.0, prices={"BTCUSDT": 100.0})

    def test_an_entry_fills_at_the_asked_price(self):
        receipt = self.broker().place_market_entry(
            symbol="BTCUSDT", side=Side.BUY, quantity=2.0, client_order_id="a"
        )
        self.assertTrue(receipt.filled)
        self.assertEqual(receipt.average_price, 100.0)
        self.assertEqual(receipt.filled_quantity, 2.0)

    def test_a_buy_spends_the_quote_balance(self):
        broker = self.broker()
        broker.place_market_entry(
            symbol="BTCUSDT", side=Side.BUY, quantity=2.0, client_order_id="a"
        )
        self.assertAlmostEqual(broker.balance("USDT"), 800.0)

    def test_a_recorded_exit_gives_the_cash_back(self):
        # Paper's protective orders never fill on their own; settle() reads the
        # bar and tells the broker here. Without it the cash only ever went out.
        broker = self.broker()
        broker.place_market_entry(
            symbol="BTCUSDT", side=Side.BUY, quantity=2.0, client_order_id="a"
        )
        broker.record_exit_fill(
            symbol="BTCUSDT", side=Side.SELL, quantity=2.0, price=110.0
        )
        self.assertAlmostEqual(broker.balance("USDT"), 1020.0)

    def test_a_recorded_exit_on_a_short_spends(self):
        broker = self.broker()
        broker.place_market_entry(
            symbol="BTCUSDT", side=Side.SELL, quantity=2.0, client_order_id="a"
        )
        self.assertAlmostEqual(broker.balance("USDT"), 1200.0)
        broker.record_exit_fill(
            symbol="BTCUSDT", side=Side.BUY, quantity=2.0, price=90.0
        )
        self.assertAlmostEqual(broker.balance("USDT"), 1020.0)

    def test_an_order_larger_than_the_balance_is_refused(self):
        with self.assertRaises(BrokerError):
            self.broker().place_market_entry(
                symbol="BTCUSDT", side=Side.BUY, quantity=50.0, client_order_id="a"
            )

    def test_a_duplicate_client_order_id_is_refused(self):
        # The paper broker holds the same contract as the exchange, so a retry
        # bug surfaces in tests rather than in production.
        broker = self.broker()
        broker.place_market_entry(
            symbol="BTCUSDT", side=Side.BUY, quantity=1.0, client_order_id="same"
        )
        with self.assertRaises(BrokerError):
            broker.place_market_entry(
                symbol="BTCUSDT", side=Side.BUY, quantity=1.0, client_order_id="same"
            )

    def test_a_protective_exit_is_recorded_as_resting(self):
        broker = self.broker()
        receipt = broker.place_protective_exit(
            symbol="BTCUSDT",
            side=Side.SELL,
            quantity=1.0,
            stop_price=95.0,
            take_profit=110.0,
            client_order_id="x",
        )
        self.assertEqual(receipt.status, "NEW")
        self.assertFalse(receipt.filled)
        self.assertEqual(len(broker.open_orders("BTCUSDT")), 1)

    def test_cancel_all_clears_only_that_symbol(self):
        broker = self.broker()
        broker.prices["ETHUSDT"] = 50.0
        for symbol in ("BTCUSDT", "ETHUSDT"):
            broker.place_protective_exit(
                symbol=symbol,
                side=Side.SELL,
                quantity=1.0,
                stop_price=1.0,
                take_profit=2.0,
                client_order_id=f"x-{symbol}",
            )
        broker.cancel_all("BTCUSDT")
        self.assertEqual(broker.open_orders("BTCUSDT"), [])
        self.assertEqual(len(broker.open_orders("ETHUSDT")), 1)

    def test_default_rules_are_returned_for_an_unknown_symbol(self):
        self.assertEqual(self.broker().rules("DOGEUSDT").symbol, "DOGEUSDT")

    def test_it_declares_itself_as_testnet(self):
        self.assertTrue(self.broker().testnet)


class ArmingTests(unittest.TestCase):
    def test_testnet_needs_no_arming(self):
        broker = BinanceSpotBroker(api_key="k", api_secret="s", testnet=True)
        self.assertEqual(broker.base_url, TESTNET_BASE)
        broker.ensure_armed()  # must not raise

    def test_live_without_the_second_switch_is_refused(self):
        broker = BinanceSpotBroker(api_key="k", api_secret="s", testnet=False, live_armed=False)
        self.assertEqual(broker.base_url, MAINNET_BASE)
        with self.assertRaises(LiveTradingNotArmed):
            broker.ensure_armed()

    def test_live_checks_that_the_key_cannot_withdraw(self):
        broker = BinanceSpotBroker(api_key="k", api_secret="s", testnet=False, live_armed=True)
        with mock.patch.object(
            broker, "_signed_request", return_value={"enableWithdrawals": False}
        ) as called:
            broker.ensure_armed()
        self.assertEqual(called.call_args.args[1], "/sapi/v1/account/apiRestrictions")

    def test_a_key_that_can_withdraw_is_refused(self):
        broker = BinanceSpotBroker(api_key="k", api_secret="s", testnet=False, live_armed=True)
        with mock.patch.object(broker, "_signed_request", return_value={"enableWithdrawals": True}):
            with self.assertRaises(BrokerError) as caught:
                broker.ensure_armed()
        self.assertIn("trade-only key", str(caught.exception))

    def test_unverifiable_permissions_fail_closed(self):
        # An unknown permission set is not a safe one.
        broker = BinanceSpotBroker(api_key="k", api_secret="s", testnet=False, live_armed=True)
        with mock.patch.object(broker, "_signed_request", side_effect=BrokerError("403")):
            with self.assertRaises(BrokerError) as caught:
                broker.ensure_armed()
        self.assertIn("could not verify", str(caught.exception))

    def test_the_permission_check_runs_once(self):
        broker = BinanceSpotBroker(api_key="k", api_secret="s", testnet=False, live_armed=True)
        with mock.patch.object(
            broker, "_signed_request", return_value={"enableWithdrawals": False}
        ) as called:
            broker.ensure_armed()
            broker.ensure_armed()
        self.assertEqual(called.call_count, 1)

    def test_an_order_on_an_unarmed_live_broker_never_reaches_the_network(self):
        broker = BinanceSpotBroker(api_key="k", api_secret="s", testnet=False, live_armed=False)
        with mock.patch("urllib.request.urlopen") as opened:
            with self.assertRaises(LiveTradingNotArmed):
                broker.place_market_entry(
                    symbol="BTCUSDT", side=Side.BUY, quantity=1.0, client_order_id="x"
                )
        opened.assert_not_called()


class SignedRequestTests(unittest.TestCase):
    def broker(self):
        return BinanceSpotBroker(api_key="key-123", api_secret="secret-abc", testnet=True)

    def capture(self, payload=None):
        captured = {}

        def fake(request, timeout=None):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            captured["body"] = None if request.data is None else request.data.decode()
            captured["method"] = request.get_method()
            return FakeResponse(payload if payload is not None else {"ok": True})

        return captured, fake

    def test_missing_credentials_are_refused_before_any_request(self):
        broker = BinanceSpotBroker(api_key="", api_secret="", testnet=True)
        with mock.patch("urllib.request.urlopen") as opened:
            with self.assertRaises(BrokerError):
                broker._signed_request("GET", "/api/v3/account")
        opened.assert_not_called()

    def test_the_signature_covers_exactly_what_is_sent(self):
        captured, fake = self.capture()
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            self.broker()._signed_request("GET", "/api/v3/account", {"symbol": "BTCUSDT"})
        query = captured["url"].split("?", 1)[1]
        body, signature = query.rsplit("&signature=", 1)
        expected = hmac.new(b"secret-abc", body.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(signature, expected)

    def test_a_get_carries_its_parameters_in_the_url(self):
        captured, fake = self.capture()
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            self.broker()._signed_request("GET", "/api/v3/account", {"symbol": "BTCUSDT"})
        self.assertIsNone(captured["body"])
        self.assertIn("symbol=BTCUSDT", captured["url"])
        self.assertIn("recvWindow=5000", captured["url"])
        self.assertIn("timestamp=", captured["url"])

    def test_a_post_carries_its_parameters_in_the_body(self):
        captured, fake = self.capture()
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            self.broker()._signed_request("POST", "/api/v3/order", {"symbol": "BTCUSDT"})
        self.assertEqual(captured["method"], "POST")
        self.assertIn("symbol=BTCUSDT", captured["body"])
        self.assertNotIn("?", captured["url"])

    def test_the_key_goes_in_the_header_and_the_secret_goes_nowhere(self):
        captured, fake = self.capture()
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            self.broker()._signed_request("POST", "/api/v3/order", {"symbol": "BTCUSDT"})
        headers = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(headers["x-mbx-apikey"], "key-123")
        self.assertNotIn("secret-abc", captured["body"])
        self.assertNotIn("secret-abc", captured["url"])

    def test_a_rejection_is_reported_with_the_exchanges_own_message(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(400)):
            with self.assertRaises(BrokerError) as caught:
                self.broker()._signed_request("POST", "/api/v3/order", {})
        self.assertIn("insufficient balance", str(caught.exception))

    def test_a_timeout_is_not_retried(self):
        # A timeout on an order leaves the outcome unknown. Re-sending blind is
        # how one signal becomes two positions; the next cycle reconciles.
        opened = mock.Mock(side_effect=TimeoutError("took too long"))
        with mock.patch("urllib.request.urlopen", opened):
            with self.assertRaises(BrokerError):
                self.broker()._signed_request("POST", "/api/v3/order", {})
        self.assertEqual(opened.call_count, 1)


class BinanceOrderTests(unittest.TestCase):
    def broker(self):
        broker = BinanceSpotBroker(api_key="k", api_secret="s", testnet=True)
        broker._rules_cache["BTCUSDT"] = BTC_RULES
        return broker

    def test_a_short_on_spot_is_refused_with_the_reason(self):
        # Mapping a short onto a sell would liquidate holdings the strategy
        # never meant to touch.
        broker = self.broker()
        with mock.patch("urllib.request.urlopen") as opened:
            with self.assertRaises(BrokerError) as caught:
                broker.place_market_entry(
                    symbol="BTCUSDT", side=Side.SELL, quantity=1.0, client_order_id="x"
                )
        opened.assert_not_called()
        self.assertIn("spot cannot open a short", str(caught.exception))

    def test_an_entry_sends_a_market_order_with_our_id(self):
        broker = self.broker()
        sent = {}

        def fake(method, path, params=None):
            sent["method"], sent["path"], sent["params"] = method, path, params
            return {
                "orderId": 55,
                "clientOrderId": params["newClientOrderId"],
                "status": "FILLED",
                "executedQty": "0.00100000",
                "fills": [{"price": "50000.00", "qty": "0.00100000"}],
            }

        with mock.patch.object(broker, "_signed_request", side_effect=fake):
            receipt = broker.place_market_entry(
                symbol="BTCUSDT", side=Side.BUY, quantity=0.001, client_order_id="at-btc-long-abc"
            )
        self.assertEqual(sent["path"], "/api/v3/order")
        self.assertEqual(sent["params"]["type"], "MARKET")
        self.assertEqual(sent["params"]["newClientOrderId"], "at-btc-long-abc")
        self.assertEqual(sent["params"]["quantity"], "0.001")
        self.assertTrue(receipt.filled)
        self.assertAlmostEqual(receipt.average_price, 50_000.0)
        self.assertEqual(receipt.order_id, "55")

    def test_an_average_price_is_weighted_across_partial_fills(self):
        broker = self.broker()
        payload = {
            "orderId": 1,
            "status": "FILLED",
            "executedQty": "2",
            "fills": [
                {"price": "100", "qty": "1"},
                {"price": "110", "qty": "1"},
            ],
        }
        with mock.patch.object(broker, "_signed_request", return_value=payload):
            receipt = broker.place_market_entry(
                symbol="BTCUSDT", side=Side.BUY, quantity=2.0, client_order_id="x"
            )
        self.assertAlmostEqual(receipt.average_price, 105.0)

    def test_an_unfilled_order_is_not_reported_as_filled(self):
        broker = self.broker()
        with mock.patch.object(
            broker, "_signed_request", return_value={"orderId": 2, "status": "EXPIRED"}
        ):
            receipt = broker.place_market_entry(
                symbol="BTCUSDT", side=Side.BUY, quantity=1.0, client_order_id="x"
            )
        self.assertFalse(receipt.filled)

    def test_the_protective_exit_is_one_oco_with_both_levels(self):
        # Two separate orders can both fill — a spike through the target then a
        # reversal through the stop leaves the account holding the opposite bet.
        broker = self.broker()
        sent = {}

        def fake(method, path, params=None):
            sent["path"], sent["params"] = path, params
            return {"orderListId": 9, "listOrderStatus": "EXECUTING"}

        with mock.patch.object(broker, "_signed_request", side_effect=fake):
            receipt = broker.place_protective_exit(
                symbol="BTCUSDT",
                side=Side.SELL,
                quantity=0.001,
                stop_price=49_000.004,
                take_profit=52_000.0,
                client_order_id="oco-1",
            )
        self.assertEqual(sent["path"], "/api/v3/order/oco")
        self.assertEqual(sent["params"]["price"], "52000")
        self.assertEqual(sent["params"]["stopPrice"], "49000")
        self.assertEqual(sent["params"]["stopLimitTimeInForce"], "GTC")
        self.assertEqual(receipt.order_id, "9")

    def test_the_stop_limit_sits_beyond_the_trigger_on_a_sell(self):
        broker = self.broker()
        sent = {}

        def fake(method, path, params=None):
            sent["params"] = params
            return {"orderListId": 1}

        with mock.patch.object(broker, "_signed_request", side_effect=fake):
            broker.place_protective_exit(
                symbol="BTCUSDT",
                side=Side.SELL,
                quantity=0.001,
                stop_price=50_000.0,
                take_profit=52_000.0,
                client_order_id="oco-2",
            )
        self.assertLess(float(sent["params"]["stopLimitPrice"]), 50_000.0)

    def test_rules_come_from_exchange_info_and_are_cached(self):
        broker = BinanceSpotBroker(api_key="k", api_secret="s", testnet=True)
        payload = {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"}],
                }
            ]
        }
        with mock.patch.object(broker, "_public_request", return_value=payload) as called:
            first = broker.rules("btcusdt")
            second = broker.rules("BTCUSDT")
        self.assertEqual(called.call_count, 1)
        self.assertIs(first, second)
        self.assertAlmostEqual(first.step_size, 0.001)

    def test_an_unknown_symbol_raises(self):
        broker = BinanceSpotBroker(api_key="k", api_secret="s", testnet=True)
        with mock.patch.object(broker, "_public_request", return_value={"symbols": []}):
            with self.assertRaises(BrokerError):
                broker.rules("NOPEUSDT")

    def test_the_balance_reads_the_free_amount_of_the_quote_asset(self):
        broker = self.broker()
        payload = {"balances": [{"asset": "BTC", "free": "0.5"}, {"asset": "USDT", "free": "250.75"}]}
        with mock.patch.object(broker, "_signed_request", return_value=payload):
            self.assertAlmostEqual(broker.balance("USDT"), 250.75)

    def test_an_absent_asset_reads_as_zero(self):
        broker = self.broker()
        with mock.patch.object(broker, "_signed_request", return_value={"balances": []}):
            self.assertEqual(broker.balance("USDT"), 0.0)


class BuildBrokerTests(unittest.TestCase):
    def test_nothing_set_means_nothing_at_risk(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(build_broker(), PaperBroker)

    def test_leaving_paper_mode_still_lands_on_testnet(self):
        with mock.patch.dict(os.environ, {"PAPER_TRADING": "false"}, clear=True):
            broker = build_broker()
        self.assertIsInstance(broker, BinanceSpotBroker)
        self.assertTrue(broker.testnet)

    def test_the_real_book_needs_both_switches(self):
        env = {"PAPER_TRADING": "false", "BINANCE_TESTNET": "false"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(LiveTradingNotArmed):
                build_broker()

    def test_both_switches_build_a_live_broker(self):
        env = {
            "PAPER_TRADING": "false",
            "BINANCE_TESTNET": "false",
            "LIVE_TRADING_ARMED": "true",
            "BINANCE_API_KEY": "k",
            "BINANCE_API_SECRET": "s",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            broker = build_broker()
        self.assertTrue(broker.is_live)
        self.assertTrue(broker.live_armed)

    def test_an_unreadable_flag_keeps_the_safer_default(self):
        # "ture" is not a vote for live trading.
        with mock.patch.dict(os.environ, {"PAPER_TRADING": "ture"}, clear=True):
            self.assertIsInstance(build_broker(), PaperBroker)

    def test_the_argument_overrides_the_environment(self):
        with mock.patch.dict(os.environ, {"PAPER_TRADING": "false"}, clear=True):
            self.assertIsInstance(build_broker(paper=True), PaperBroker)


if __name__ == "__main__":
    unittest.main()
