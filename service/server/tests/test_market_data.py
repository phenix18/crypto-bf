"""Market data sources: the boundary where bad data has to stop.

Most of these are about rejection rather than parsing. A malformed candle does
not raise anywhere downstream — it propagates through every indicator and comes
out the far end as a confident signal — so the tests that matter here are the
ones proving the boundary refuses it.
"""

import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from indicators import Candle  # noqa: E402
from market_data import (  # noqa: E402
    MAINNET_BASE,
    TESTNET_BASE,
    BinanceSource,
    CoinMarketCapQuotes,
    MarketDataError,
    ReplaySource,
    SyntheticSource,
    build_source,
    parse_cmc_entry,
    validate,
)


def candle(open_time=0, open_=100.0, high=101.0, low=99.0, close=100.5, volume=10.0):
    return Candle(
        open_time=open_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def series(count, start_time=0, step=60_000):
    """A clean ascending series: each bar's range brackets its own close."""
    out = []
    for i in range(count):
        close = 100.0 + i
        out.append(
            candle(
                open_time=start_time + i * step,
                open_=close,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
            )
        )
    return out


class FakeResponse:
    """Minimal stand-in for what urlopen yields."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b'{"msg":"nope"}'):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


# --------------------------------------------------------------------- replay --


class ReplaySourceTests(unittest.TestCase):
    def test_fetch_returns_the_most_recent_window(self):
        source = ReplaySource()
        source.add("btcusdt", "5m", series(50))
        window = source.fetch("BTCUSDT", "5m", 10)
        self.assertEqual(len(window), 10)
        self.assertEqual(window[-1].close, 149.0)

    def test_symbol_lookup_is_case_insensitive(self):
        source = ReplaySource()
        source.add("BTCUSDT", "5m", series(5))
        self.assertEqual(len(source.fetch("btcusdt", "5m", 5)), 5)

    def test_limit_larger_than_the_series_returns_everything(self):
        source = ReplaySource()
        source.add("BTCUSDT", "5m", series(7))
        self.assertEqual(len(source.fetch("BTCUSDT", "5m", 500)), 7)

    def test_unknown_symbol_raises(self):
        source = ReplaySource()
        with self.assertRaises(MarketDataError):
            source.fetch("ETHUSDT", "5m", 10)

    def test_static_mode_is_stable_across_fetches(self):
        source = ReplaySource()
        source.add("BTCUSDT", "5m", series(30))
        first = source.fetch("BTCUSDT", "5m", 10)
        second = source.fetch("BTCUSDT", "5m", 10)
        self.assertEqual([c.close for c in first], [c.close for c in second])

    def test_streaming_mode_advances_one_candle_per_fetch(self):
        source = ReplaySource(streaming=True)
        source.add("BTCUSDT", "5m", series(30))
        first = source.fetch("BTCUSDT", "5m", 10)
        second = source.fetch("BTCUSDT", "5m", 10)
        self.assertEqual(len(second), 10)
        self.assertEqual(second[-1].close, first[-1].close + 1)
        self.assertEqual(second[0].close, first[0].close + 1)

    def test_streaming_mode_raises_once_past_the_end(self):
        # The real failure this guards: clamping at the end would hand the same
        # window back forever and a backtest would never finish.
        source = ReplaySource(streaming=True)
        source.add("BTCUSDT", "5m", series(12))
        for _ in range(3):  # windows ending at 10, 11, 12
            source.fetch("BTCUSDT", "5m", 10)
        with self.assertRaises(MarketDataError) as caught:
            source.fetch("BTCUSDT", "5m", 10)
        self.assertIn("exhausted", str(caught.exception))

    def test_reset_rewinds_the_cursor(self):
        source = ReplaySource(streaming=True)
        source.add("BTCUSDT", "5m", series(12))
        first = source.fetch("BTCUSDT", "5m", 10)
        source.fetch("BTCUSDT", "5m", 10)
        source.reset()
        again = source.fetch("BTCUSDT", "5m", 10)
        self.assertEqual([c.close for c in again], [c.close for c in first])

    def test_streaming_cursors_are_per_series(self):
        source = ReplaySource(streaming=True)
        source.add("BTCUSDT", "5m", series(30))
        source.add("ETHUSDT", "5m", series(30))
        source.fetch("BTCUSDT", "5m", 10)
        source.fetch("BTCUSDT", "5m", 10)
        btc = source.fetch("BTCUSDT", "5m", 10)
        eth = source.fetch("ETHUSDT", "5m", 10)
        self.assertNotEqual(btc[-1].close, eth[-1].close)

    def test_add_copies_the_candles(self):
        original = series(5)
        source = ReplaySource()
        source.add("BTCUSDT", "5m", original)
        original.append(candle(open_time=999))
        self.assertEqual(len(source.fetch("BTCUSDT", "5m", 50)), 5)


# ------------------------------------------------------------------ synthetic --


class SyntheticSourceTests(unittest.TestCase):
    def source(self, now=1_700_000_000.0):
        return SyntheticSource(clock=lambda: now)

    def test_it_returns_the_asked_for_window(self):
        self.assertEqual(len(self.source().fetch("BTCUSDT", "5m", 300)), 300)

    def test_what_it_produces_passes_the_validator(self):
        # The demo source has to obey the same rules as the exchange, or it
        # hides bugs the real feed would expose.
        candles = self.source().fetch("BTCUSDT", "5m", 400)
        self.assertEqual(len(validate(candles, "BTCUSDT", "5m")), 400)

    def test_the_same_symbol_always_prints_the_same_history(self):
        first = self.source().fetch("BTCUSDT", "5m", 50)
        second = self.source().fetch("BTCUSDT", "5m", 50)
        self.assertEqual([c.close for c in first], [c.close for c in second])

    def test_different_symbols_do_not_print_identical_charts(self):
        btc = self.source().fetch("BTCUSDT", "5m", 50)
        eth = self.source().fetch("ETHUSDT", "5m", 50)
        self.assertNotEqual([c.close for c in btc], [c.close for c in eth])

    def test_the_window_advances_with_the_clock(self):
        early = self.source(1_700_000_000.0).fetch("BTCUSDT", "5m", 50)
        later = self.source(1_700_000_000.0 + 300.0).fetch("BTCUSDT", "5m", 50)
        self.assertNotEqual(early[-1].close, later[-1].close)

    def test_the_newest_candle_is_the_current_bar(self):
        # A chart drawn from this should show now, not 1970.
        now = 1_700_000_000.0
        candles = self.source(now).fetch("BTCUSDT", "5m", 50)
        expected = (int(now * 1000 // 300_000) - 1) * 300_000
        self.assertEqual(candles[-1].open_time, expected)

    def test_candles_are_in_ascending_order(self):
        candles = self.source().fetch("BTCUSDT", "15m", 100)
        times = [c.open_time for c in candles]
        self.assertEqual(times, sorted(times))
        self.assertEqual(times[1] - times[0], 900_000)

    def test_an_unknown_interval_raises(self):
        with self.assertRaises(MarketDataError):
            self.source().fetch("BTCUSDT", "7m", 50)

    def test_a_limit_beyond_the_series_is_capped(self):
        source = SyntheticSource(bars=200, clock=lambda: 1_700_000_000.0)
        self.assertEqual(len(source.fetch("BTCUSDT", "5m", 10_000)), 200)

    def test_it_says_what_it_is(self):
        # The name travels with every response so a screen fed by this can say
        # so instead of passing invented prices off as the market.
        self.assertEqual(self.source().name, "synthetic")


# ------------------------------------------------------------------ validation --


class ValidateTests(unittest.TestCase):
    def test_clean_candles_pass_through(self):
        candles = series(5)
        self.assertEqual(validate(candles, "BTCUSDT", "5m"), candles)

    def test_empty_is_rejected(self):
        with self.assertRaises(MarketDataError):
            validate([], "BTCUSDT", "5m")

    def test_nan_price_is_rejected(self):
        with self.assertRaises(MarketDataError) as caught:
            validate([candle(close=float("nan"))], "BTCUSDT", "5m")
        self.assertIn("non-finite", str(caught.exception))

    def test_infinite_price_is_rejected(self):
        with self.assertRaises(MarketDataError):
            validate([candle(high=float("inf"))], "BTCUSDT", "5m")

    def test_zero_price_is_rejected(self):
        with self.assertRaises(MarketDataError) as caught:
            validate([candle(low=0.0)], "BTCUSDT", "5m")
        self.assertIn("non-positive", str(caught.exception))

    def test_negative_price_is_rejected(self):
        with self.assertRaises(MarketDataError):
            validate([candle(low=-1.0)], "BTCUSDT", "5m")

    def test_inverted_high_low_is_rejected(self):
        with self.assertRaises(MarketDataError) as caught:
            validate([candle(high=99.0, low=101.0, open_=100.0, close=100.0)], "BTCUSDT", "5m")
        self.assertIn("below low", str(caught.exception))

    def test_open_outside_the_range_is_rejected(self):
        with self.assertRaises(MarketDataError) as caught:
            validate([candle(open_=105.0)], "BTCUSDT", "5m")
        self.assertIn("opens outside", str(caught.exception))

    def test_close_outside_the_range_is_rejected(self):
        with self.assertRaises(MarketDataError) as caught:
            validate([candle(close=105.0)], "BTCUSDT", "5m")
        self.assertIn("closes outside", str(caught.exception))

    def test_negative_volume_is_rejected(self):
        with self.assertRaises(MarketDataError) as caught:
            validate([candle(volume=-1.0)], "BTCUSDT", "5m")
        self.assertIn("negative volume", str(caught.exception))

    def test_zero_volume_is_allowed(self):
        # Illiquid pairs print empty bars. That is thin, not corrupt.
        self.assertEqual(len(validate([candle(volume=0.0)], "BTCUSDT", "5m")), 1)

    def test_out_of_order_candles_are_rejected(self):
        candles = [candle(open_time=120_000), candle(open_time=60_000)]
        with self.assertRaises(MarketDataError) as caught:
            validate(candles, "BTCUSDT", "5m")
        self.assertIn("ascending", str(caught.exception))

    def test_duplicate_timestamps_are_rejected(self):
        candles = [candle(open_time=60_000), candle(open_time=60_000)]
        with self.assertRaises(MarketDataError):
            validate(candles, "BTCUSDT", "5m")

    def test_the_message_names_the_symbol_and_interval(self):
        with self.assertRaises(MarketDataError) as caught:
            validate([candle(close=float("nan"))], "ETHUSDT", "15m")
        self.assertIn("ETHUSDT", str(caught.exception))
        self.assertIn("15m", str(caught.exception))


# --------------------------------------------------------------------- candles --


class CandleParsingTests(unittest.TestCase):
    ROW = [
        1_700_000_000_000, "100.10", "101.20", "99.50", "100.90", "12.5",
        1_700_000_059_999, "1250.0", 42, "6.0", "600.0", "0",
    ]

    def test_prices_arrive_as_strings_and_come_out_as_floats(self):
        parsed = Candle.from_binance(self.ROW)
        self.assertEqual(parsed.open_time, 1_700_000_000_000)
        self.assertAlmostEqual(parsed.open, 100.10)
        self.assertAlmostEqual(parsed.high, 101.20)
        self.assertAlmostEqual(parsed.low, 99.50)
        self.assertAlmostEqual(parsed.close, 100.90)
        self.assertAlmostEqual(parsed.volume, 12.5)

    def test_a_short_row_raises(self):
        with self.assertRaises(IndexError):
            Candle.from_binance([1, "1", "2"])


# --------------------------------------------------------------------- binance --


class BinanceSourceTests(unittest.TestCase):
    def test_testnet_is_the_default_base(self):
        self.assertEqual(BinanceSource().base_url, TESTNET_BASE)

    def test_mainnet_takes_an_explicit_flag(self):
        self.assertEqual(BinanceSource(testnet=False).base_url, MAINNET_BASE)

    def test_an_unknown_interval_never_leaves_the_process(self):
        source = BinanceSource()
        with mock.patch("urllib.request.urlopen") as opened:
            with self.assertRaises(MarketDataError) as caught:
                source.fetch("BTCUSDT", "7m", 100)
        opened.assert_not_called()
        self.assertIn("not a Binance interval", str(caught.exception))

    def test_the_request_carries_an_uppercased_symbol(self):
        source = BinanceSource()
        seen = {}

        def capture(url):
            seen["url"] = url
            return []

        source._get_json = capture
        with self.assertRaises(MarketDataError):  # empty payload fails validation
            source.fetch("btcusdt", "5m", 10)
        self.assertIn("symbol=BTCUSDT", seen["url"])
        self.assertIn("interval=5m", seen["url"])
        self.assertIn(TESTNET_BASE, seen["url"])

    def test_the_limit_is_capped_at_the_exchange_maximum(self):
        source = BinanceSource()
        seen = {}

        def capture(url):
            seen["url"] = url
            return []

        source._get_json = capture
        with self.assertRaises(MarketDataError):
            source.fetch("BTCUSDT", "5m", 50_000)
        self.assertIn("limit=1000", seen["url"])

    def test_a_non_positive_limit_becomes_one(self):
        source = BinanceSource()
        seen = {}

        def capture(url):
            seen["url"] = url
            return []

        source._get_json = capture
        with self.assertRaises(MarketDataError):
            source.fetch("BTCUSDT", "5m", 0)
        self.assertIn("limit=1", seen["url"])

    def test_rows_become_validated_candles(self):
        source = BinanceSource()
        source._get_json = lambda url: [
            [60_000, "100", "101", "99", "100.5", "5", 119_999, "0", 0, "0", "0", "0"],
            [120_000, "100.5", "102", "100", "101.5", "6", 179_999, "0", 0, "0", "0", "0"],
        ]
        candles = source.fetch("BTCUSDT", "1m", 2)
        self.assertEqual(len(candles), 2)
        self.assertAlmostEqual(candles[-1].close, 101.5)

    def test_corrupt_rows_from_the_exchange_are_rejected(self):
        source = BinanceSource()
        source._get_json = lambda url: [
            [60_000, "100", "99", "101", "100.5", "5", 119_999, "0", 0, "0", "0", "0"],
        ]
        with self.assertRaises(MarketDataError) as caught:
            source.fetch("BTCUSDT", "1m", 1)
        self.assertIn("below low", str(caught.exception))

    def test_a_malformed_row_is_reported_as_market_data_error(self):
        source = BinanceSource()
        source._get_json = lambda url: [[60_000, "oops"]]
        with self.assertRaises(MarketDataError) as caught:
            source.fetch("BTCUSDT", "1m", 1)
        self.assertIn("malformed", str(caught.exception))

    def test_a_non_list_payload_is_rejected(self):
        source = BinanceSource()
        source._get_json = lambda url: {"code": -1121, "msg": "Invalid symbol."}
        with self.assertRaises(MarketDataError) as caught:
            source.fetch("BTCUSDT", "1m", 1)
        self.assertIn("unexpected klines payload", str(caught.exception))


class BinanceTransportTests(unittest.TestCase):
    def source(self):
        return BinanceSource(max_retries=2, backoff_seconds=0.0)

    def test_a_good_response_is_decoded(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b'[1, 2]')):
            self.assertEqual(self.source()._get_json("http://x"), [1, 2])

    def test_a_4xx_is_not_retried(self):
        # Retrying a rejected request cannot fix it and burns the rate limit —
        # and a 418/419 ban gets longer every time you knock.
        opened = mock.Mock(side_effect=http_error(400, b'{"msg":"Invalid symbol."}'))
        with mock.patch("urllib.request.urlopen", opened):
            with self.assertRaises(MarketDataError) as caught:
                self.source()._get_json("http://x")
        self.assertEqual(opened.call_count, 1)
        self.assertIn("Invalid symbol", str(caught.exception))

    def test_a_5xx_is_retried_and_can_succeed(self):
        opened = mock.Mock(side_effect=[
            http_error(502),
            http_error(503),
            FakeResponse(b'[{"ok": true}]'),
        ])
        with mock.patch("urllib.request.urlopen", opened):
            self.assertEqual(self.source()._get_json("http://x"), [{"ok": True}])
        self.assertEqual(opened.call_count, 3)

    def test_retries_are_bounded(self):
        opened = mock.Mock(side_effect=http_error(500))
        with mock.patch("urllib.request.urlopen", opened):
            with self.assertRaises(MarketDataError):
                self.source()._get_json("http://x")
        self.assertEqual(opened.call_count, 3)  # the attempt plus two retries

    def test_a_network_failure_is_retried_then_reported(self):
        opened = mock.Mock(side_effect=urllib.error.URLError("no route to host"))
        with mock.patch("urllib.request.urlopen", opened):
            with self.assertRaises(MarketDataError) as caught:
                self.source()._get_json("http://x")
        self.assertEqual(opened.call_count, 3)
        self.assertIn("could not reach Binance", str(caught.exception))

    def test_unparseable_json_is_reported_not_raised_raw(self):
        opened = mock.Mock(return_value=FakeResponse(b"<html>maintenance</html>"))
        with mock.patch("urllib.request.urlopen", opened):
            with self.assertRaises(MarketDataError):
                self.source()._get_json("http://x")

    def test_the_backoff_grows_between_attempts(self):
        source = BinanceSource(max_retries=2, backoff_seconds=0.4)
        opened = mock.Mock(side_effect=http_error(500))
        with mock.patch("urllib.request.urlopen", opened):
            with mock.patch("market_data.time.sleep") as slept:
                with self.assertRaises(MarketDataError):
                    source._get_json("http://x")
        self.assertEqual([call.args[0] for call in slept.call_args_list], [0.4, 0.8])


# -------------------------------------------------------------- coinmarketcap --


class CoinMarketCapTests(unittest.TestCase):
    PAYLOAD = {
        "data": {
            "BTC": {
                "name": "Bitcoin",
                "symbol": "BTC",
                "quote": {
                    "USD": {
                        "price": 64250.5,
                        "percent_change_1h": 0.4,
                        "percent_change_24h": -1.2,
                        "percent_change_7d": 5.5,
                        "volume_24h": 2.5e10,
                        "market_cap": 1.26e12,
                    }
                },
            }
        }
    }

    def test_an_entry_is_flattened(self):
        parsed = parse_cmc_entry(self.PAYLOAD["data"]["BTC"])
        self.assertEqual(parsed["symbol"], "BTC")
        self.assertAlmostEqual(parsed["price"], 64250.5)
        self.assertAlmostEqual(parsed["percent_change_24h"], -1.2)

    def test_a_list_entry_takes_the_first(self):
        parsed = parse_cmc_entry([self.PAYLOAD["data"]["BTC"]])
        self.assertEqual(parsed["name"], "Bitcoin")

    def test_an_empty_list_entry_yields_empty_fields(self):
        parsed = parse_cmc_entry([])
        self.assertIsNone(parsed["price"])
        self.assertIsNone(parsed["symbol"])

    def test_a_missing_convert_currency_yields_no_price(self):
        parsed = parse_cmc_entry(self.PAYLOAD["data"]["BTC"], convert="EUR")
        self.assertIsNone(parsed["price"])
        self.assertEqual(parsed["symbol"], "BTC")

    def test_a_missing_key_is_refused_before_any_request(self):
        with mock.patch("urllib.request.urlopen") as opened:
            with self.assertRaises(MarketDataError) as caught:
                CoinMarketCapQuotes(api_key="").quotes(["BTC"])
        opened.assert_not_called()
        self.assertIn("API key", str(caught.exception))

    def test_no_symbols_means_no_request(self):
        with mock.patch("urllib.request.urlopen") as opened:
            self.assertEqual(CoinMarketCapQuotes(api_key="k").quotes([]), {})
        opened.assert_not_called()

    def test_quotes_are_parsed_per_symbol(self):
        import json

        body = json.dumps(self.PAYLOAD).encode()
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            quotes = CoinMarketCapQuotes(api_key="k").quotes(["btc"])
        self.assertIn("BTC", quotes)
        self.assertAlmostEqual(quotes["BTC"]["price"], 64250.5)

    def test_an_http_error_is_wrapped(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(401, b"bad key")):
            with self.assertRaises(MarketDataError) as caught:
                CoinMarketCapQuotes(api_key="k").quotes(["BTC"])
        self.assertIn("401", str(caught.exception))

    def test_an_unexpected_payload_is_rejected(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b'{"status":{}}')):
            with self.assertRaises(MarketDataError) as caught:
                CoinMarketCapQuotes(api_key="k").quotes(["BTC"])
        self.assertIn("unexpected CoinMarketCap payload", str(caught.exception))


# ---------------------------------------------------------------- build_source --


class BuildSourceTests(unittest.TestCase):
    def test_the_default_is_binance_on_testnet(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            source = build_source()
        self.assertIsInstance(source, BinanceSource)
        self.assertTrue(source.testnet)

    def test_mainnet_needs_a_deliberate_opt_out(self):
        for value in ("false", "FALSE", "0", "no", "off"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"BINANCE_TESTNET": value}, clear=True):
                    self.assertFalse(build_source().testnet)

    def test_anything_else_stays_on_testnet(self):
        # Including a typo: "flase" must not silently point at the live book.
        for value in ("true", "1", "yes", "flase", ""):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"BINANCE_TESTNET": value}, clear=True):
                    self.assertTrue(build_source().testnet)

    def test_an_explicit_argument_beats_the_environment(self):
        with mock.patch.dict(os.environ, {"BINANCE_TESTNET": "true"}, clear=True):
            self.assertFalse(build_source(testnet=False).testnet)

    def test_the_synthetic_provider_is_selectable(self):
        with mock.patch.dict(os.environ, {"MARKET_DATA_PROVIDER": "synthetic"}, clear=True):
            self.assertIsInstance(build_source(), SyntheticSource)

    def test_the_replay_provider_is_selectable(self):
        with mock.patch.dict(os.environ, {"MARKET_DATA_PROVIDER": "replay"}, clear=True):
            self.assertIsInstance(build_source(), ReplaySource)

    def test_an_unknown_provider_raises(self):
        with mock.patch.dict(os.environ, {"MARKET_DATA_PROVIDER": "ftx"}, clear=True):
            with self.assertRaises(MarketDataError):
                build_source()


if __name__ == "__main__":
    unittest.main()
