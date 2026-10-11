"""Placing orders. The only module in the project that can move money.

Everything here is written on the assumption that it will one day run against a
real account with real balance, at 3am, unattended, after a bug elsewhere. So:

**Testnet is the default and live takes two deliberate switches.** Pointing at
the real book needs `BINANCE_TESTNET=false` *and* `LIVE_TRADING_ARMED=true`.
One forgotten variable can only ever make the bot safer, never riskier.

**A key that can withdraw is refused.** Before the first live order the broker
asks Binance what the key is allowed to do and stops if withdrawals are
enabled. A trading bot has no business holding a key that can empty the
account, and this is the one check that catches it before the damage, not after.

**Quantities round down, never up.** The risk layer decided what a loss may
cost; rounding a size up would quietly spend more than that.

**Every order carries a deterministic client order id.** A retry after a
timeout re-sends the same id, and Binance rejects the duplicate instead of
opening a second position. Without that, the most dangerous moment in the whole
system is a network blip between "order sent" and "order confirmed".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol, Sequence

MAINNET_BASE = "https://api.binance.com"
TESTNET_BASE = "https://testnet.binance.vision"

# Binance's cap on a client order id, and the characters it accepts.
MAX_CLIENT_ORDER_ID = 36


class BrokerError(RuntimeError):
    """An order could not be placed, or should not be."""


class LiveTradingNotArmed(BrokerError):
    """Live mode was asked for without both switches set."""


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


def side_for(direction: str) -> Side:
    normalised = (direction or "").strip().lower()
    if normalised == "long":
        return Side.BUY
    if normalised == "short":
        return Side.SELL
    raise BrokerError(f"no order side for direction {direction!r}")


# ------------------------------------------------------------------- rounding --


@dataclass(frozen=True)
class InstrumentRules:
    """The exchange's filters for one symbol.

    An order that ignores these is rejected outright, which in a loop means a
    setup is seen, approved, and then silently dropped every single time.
    """

    symbol: str
    step_size: float = 0.00001
    tick_size: float = 0.01
    min_quantity: float = 0.0
    min_notional: float = 0.0

    def round_quantity(self, quantity: float) -> float:
        """Floor to the step. Down, always: see the module docstring."""
        if self.step_size <= 0:
            return quantity
        steps = math.floor(quantity / self.step_size + 1e-9)
        return round(steps * self.step_size, 12)

    def round_price(self, price: float) -> float:
        """Nearest tick. A price is not a budget, so nearest is right here."""
        if self.tick_size <= 0:
            return price
        ticks = round(price / self.tick_size)
        return round(ticks * self.tick_size, 12)

    def reject_reason(self, quantity: float, price: float) -> Optional[str]:
        if quantity <= 0:
            return f"quantity rounds to {quantity} at a {self.step_size} step"
        if quantity < self.min_quantity:
            return f"quantity {quantity} below the {self.min_quantity} minimum"
        notional = quantity * price
        if notional < self.min_notional:
            return f"notional {notional:.2f} below the {self.min_notional} minimum"
        return None

    @classmethod
    def from_exchange_info(cls, entry: dict) -> "InstrumentRules":
        step = tick = min_qty = min_notional = None
        for item in entry.get("filters") or []:
            kind = item.get("filterType")
            if kind == "LOT_SIZE":
                step = _as_float(item.get("stepSize"))
                min_qty = _as_float(item.get("minQty"))
            elif kind == "PRICE_FILTER":
                tick = _as_float(item.get("tickSize"))
            elif kind in ("MIN_NOTIONAL", "NOTIONAL"):
                min_notional = _as_float(item.get("minNotional"))
        return cls(
            symbol=entry.get("symbol", ""),
            step_size=step if step else 0.00001,
            tick_size=tick if tick else 0.01,
            min_quantity=min_qty or 0.0,
            min_notional=min_notional or 0.0,
        )


def _as_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------- orders --


@dataclass(frozen=True)
class OrderReceipt:
    """What came back. ``raw`` is kept verbatim for the audit trail."""

    symbol: str
    side: Side
    client_order_id: str
    order_id: Optional[str]
    status: str
    quantity: float
    filled_quantity: float
    average_price: Optional[float]
    raw: dict = field(default_factory=dict)

    @property
    def filled(self) -> bool:
        return self.status.upper() == "FILLED" and self.filled_quantity > 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "client_order_id": self.client_order_id,
            "order_id": self.order_id,
            "status": self.status,
            "quantity": self.quantity,
            "filled_quantity": self.filled_quantity,
            "average_price": self.average_price,
        }


def client_order_id(
    *,
    symbol: str,
    direction: str,
    interval: str,
    bar_open_time: int,
    tag: str = "at",
) -> str:
    """A deterministic id for "this setup, on this bar".

    The same signal re-sent after a timeout produces the same id, so Binance
    rejects the duplicate rather than opening a second position. Deriving it
    from the bar also means a loop that runs twice within one candle cannot
    double up.
    """
    seed = f"{symbol.upper()}|{direction.lower()}|{interval}|{bar_open_time}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    base = f"{tag}-{symbol.upper()[:8]}-{direction.lower()[:5]}-{digest}"
    return base[:MAX_CLIENT_ORDER_ID]


class Broker(Protocol):
    name: str
    testnet: bool

    def rules(self, symbol: str) -> InstrumentRules: ...

    def balance(self, asset: str) -> float: ...

    def place_market_entry(
        self, *, symbol: str, side: Side, quantity: float, client_order_id: str
    ) -> OrderReceipt: ...

    def place_protective_exit(
        self,
        *,
        symbol: str,
        side: Side,
        quantity: float,
        stop_price: float,
        take_profit: float,
        client_order_id: str,
    ) -> OrderReceipt: ...

    def open_orders(self, symbol: str) -> list[dict]: ...

    def cancel_all(self, symbol: str) -> None: ...


# ---------------------------------------------------------------------- paper --


@dataclass
class PaperBroker:
    """Fills at the asked price, in memory. No network, no money.

    This is not a toy: it is how the executor and the loop are tested, and it
    is the mode the project runs in until someone deliberately arms live
    trading. Its fills are optimistic (no slippage, no partials), so paper
    results flatter reality — a known limitation, not a hidden one.
    """

    quote_balance: float = 10_000.0
    name: str = "paper"
    testnet: bool = True
    instrument_rules: dict[str, InstrumentRules] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)
    placed: list[OrderReceipt] = field(default_factory=list)
    protective: list[OrderReceipt] = field(default_factory=list)
    _seen_ids: set = field(default_factory=set)
    _order_counter: int = 0

    def rules(self, symbol: str) -> InstrumentRules:
        return self.instrument_rules.get(symbol.upper(), InstrumentRules(symbol=symbol.upper()))

    def balance(self, asset: str) -> float:
        return self.quote_balance

    def _next_id(self) -> str:
        self._order_counter += 1
        return f"paper-{self._order_counter}"

    def place_market_entry(
        self, *, symbol: str, side: Side, quantity: float, client_order_id: str
    ) -> OrderReceipt:
        if client_order_id in self._seen_ids:
            # Same contract as the exchange: a duplicate id is refused, so a
            # retried signal cannot open a second position even on paper.
            raise BrokerError(f"duplicate client order id {client_order_id}")
        self._seen_ids.add(client_order_id)
        price = self.prices.get(symbol.upper(), 0.0)
        notional = quantity * price
        if side is Side.BUY:
            if notional > self.quote_balance:
                raise BrokerError(
                    f"paper balance {self.quote_balance:.2f} cannot fund {notional:.2f}"
                )
            self.quote_balance -= notional
        else:
            self.quote_balance += notional
        receipt = OrderReceipt(
            symbol=symbol.upper(),
            side=side,
            client_order_id=client_order_id,
            order_id=self._next_id(),
            status="FILLED",
            quantity=quantity,
            filled_quantity=quantity,
            average_price=price,
            raw={"paper": True},
        )
        self.placed.append(receipt)
        return receipt

    def place_protective_exit(
        self,
        *,
        symbol: str,
        side: Side,
        quantity: float,
        stop_price: float,
        take_profit: float,
        client_order_id: str,
    ) -> OrderReceipt:
        receipt = OrderReceipt(
            symbol=symbol.upper(),
            side=side,
            client_order_id=client_order_id,
            order_id=self._next_id(),
            status="NEW",
            quantity=quantity,
            filled_quantity=0.0,
            average_price=None,
            raw={"paper": True, "stop_price": stop_price, "take_profit": take_profit},
        )
        self.protective.append(receipt)
        return receipt

    def open_orders(self, symbol: str) -> list[dict]:
        return [o.to_dict() for o in self.protective if o.symbol == symbol.upper()]

    def cancel_all(self, symbol: str) -> None:
        self.protective = [o for o in self.protective if o.symbol != symbol.upper()]


# -------------------------------------------------------------------- binance --


@dataclass
class BinanceSpotBroker:
    """Signed spot trading against Binance.

    Spot cannot short. A short proposal is refused here with a clear message
    rather than mapped onto a sell of inventory the account does not hold —
    that mapping is how a "short signal" quietly becomes "liquidate the bag".
    Shorting needs futures or margin, which is not wired.
    """

    api_key: str
    api_secret: str
    testnet: bool = True
    live_armed: bool = False
    timeout: float = 10.0
    recv_window: int = 5000
    name: str = "binance-spot"
    _rules_cache: dict[str, InstrumentRules] = field(default_factory=dict)
    _permissions_checked: bool = False

    @property
    def base_url(self) -> str:
        return TESTNET_BASE if self.testnet else MAINNET_BASE

    @property
    def is_live(self) -> bool:
        return not self.testnet

    # -- guards ------------------------------------------------------------

    def ensure_armed(self) -> None:
        """Refuse to trade the real book unless both switches are set."""
        if not self.is_live:
            return
        if not self.live_armed:
            raise LiveTradingNotArmed(
                "live trading needs LIVE_TRADING_ARMED=true as well as "
                "BINANCE_TESTNET=false; refusing to place a real order"
            )
        if not self._permissions_checked:
            self.assert_key_cannot_withdraw()

    def assert_key_cannot_withdraw(self) -> None:
        """Refuse a key that can move funds off the exchange.

        Fails closed: if the restrictions cannot be read, live trading does not
        proceed. An unknown permission set is not a safe one.
        """
        try:
            payload = self._signed_request("GET", "/sapi/v1/account/apiRestrictions")
        except BrokerError as exc:
            raise BrokerError(
                "could not verify that the API key cannot withdraw "
                f"({exc}); refusing to trade live with unknown permissions"
            ) from exc
        if payload.get("enableWithdrawals"):
            raise BrokerError(
                "this API key has withdrawals enabled — create a trade-only key "
                "before arming live trading"
            )
        self._permissions_checked = True

    # -- plumbing ----------------------------------------------------------

    def _signed_request(self, method: str, path: str, params: Optional[dict] = None) -> dict:
        if not self.api_key or not self.api_secret:
            raise BrokerError("Binance trading needs BINANCE_API_KEY and BINANCE_API_SECRET")

        ordered = list((params or {}).items())
        ordered.append(("recvWindow", self.recv_window))
        ordered.append(("timestamp", int(time.time() * 1000)))
        query = urllib.parse.urlencode(ordered)
        # Sign exactly the bytes that will be sent; re-encoding after signing
        # is the classic way to produce a valid signature over a different
        # request.
        signature = hmac.new(
            self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        signed = f"{query}&signature={signature}"

        url = f"{self.base_url}{path}"
        data = None
        if method.upper() in ("POST", "PUT", "DELETE"):
            data = signed.encode("utf-8")
        else:
            url = f"{url}?{signed}"

        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "X-MBX-APIKEY": self.api_key,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            raise BrokerError(f"Binance rejected {method} {path} ({exc.code}): {body}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            # Deliberately not retried. A timeout on an order leaves the
            # outcome unknown, and the safe move is to stop and let the next
            # cycle reconcile — the deterministic client order id makes that
            # reconciliation possible.
            raise BrokerError(f"could not reach Binance for {method} {path}: {exc}") from exc
        return payload if isinstance(payload, dict) else {"result": payload}

    def _public_request(self, path: str, params: Optional[dict] = None) -> dict:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            raise BrokerError(f"Binance rejected GET {path} ({exc.code}): {body}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BrokerError(f"could not reach Binance for GET {path}: {exc}") from exc

    # -- reads -------------------------------------------------------------

    def rules(self, symbol: str) -> InstrumentRules:
        symbol = symbol.upper()
        if symbol in self._rules_cache:
            return self._rules_cache[symbol]
        payload = self._public_request("/api/v3/exchangeInfo", {"symbol": symbol})
        symbols = payload.get("symbols") or []
        if not symbols:
            raise BrokerError(f"Binance knows no symbol {symbol}")
        rules = InstrumentRules.from_exchange_info(symbols[0])
        self._rules_cache[symbol] = rules
        return rules

    def balance(self, asset: str = "USDT") -> float:
        payload = self._signed_request("GET", "/api/v3/account")
        for entry in payload.get("balances") or []:
            if entry.get("asset") == asset.upper():
                return _as_float(entry.get("free")) or 0.0
        return 0.0

    def open_orders(self, symbol: str) -> list[dict]:
        payload = self._signed_request("GET", "/api/v3/openOrders", {"symbol": symbol.upper()})
        result = payload.get("result", payload)
        return result if isinstance(result, list) else []

    # -- writes ------------------------------------------------------------

    def place_market_entry(
        self, *, symbol: str, side: Side, quantity: float, client_order_id: str
    ) -> OrderReceipt:
        self.ensure_armed()
        if side is Side.SELL:
            raise BrokerError(
                "spot cannot open a short; a short needs futures or margin, "
                "which is not wired in this build"
            )
        payload = self._signed_request(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol.upper(),
                "side": side.value,
                "type": "MARKET",
                "quantity": _format_decimal(quantity),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "FULL",
            },
        )
        return _receipt_from(payload, symbol, side, client_order_id, quantity)

    def place_protective_exit(
        self,
        *,
        symbol: str,
        side: Side,
        quantity: float,
        stop_price: float,
        take_profit: float,
        client_order_id: str,
    ) -> OrderReceipt:
        """One OCO carrying both the stop and the target.

        An OCO rather than two orders because two orders can both fill: price
        spikes through the target, then reverses through the stop, and the
        account is left short the asset it never meant to sell.
        """
        self.ensure_armed()
        rules = self.rules(symbol)
        payload = self._signed_request(
            "POST",
            "/api/v3/order/oco",
            {
                "symbol": symbol.upper(),
                "side": side.value,
                "quantity": _format_decimal(quantity),
                "price": _format_decimal(rules.round_price(take_profit)),
                "stopPrice": _format_decimal(rules.round_price(stop_price)),
                # A stop-limit, not a stop-market: a stop-market in a thin book
                # fills anywhere. The limit sits a tick beyond the trigger so it
                # still fills in normal conditions.
                "stopLimitPrice": _format_decimal(
                    rules.round_price(_beyond(stop_price, side, rules.tick_size))
                ),
                "stopLimitTimeInForce": "GTC",
                "listClientOrderId": client_order_id,
            },
        )
        return OrderReceipt(
            symbol=symbol.upper(),
            side=side,
            client_order_id=client_order_id,
            order_id=str(payload.get("orderListId") or ""),
            status=str(payload.get("listOrderStatus") or "NEW"),
            quantity=quantity,
            filled_quantity=0.0,
            average_price=None,
            raw=payload,
        )

    def cancel_all(self, symbol: str) -> None:
        self._signed_request("DELETE", "/api/v3/openOrders", {"symbol": symbol.upper()})


def _beyond(stop_price: float, side: Side, tick: float) -> float:
    """The limit price for a stop-limit, one tick past the trigger."""
    offset = max(tick, stop_price * 0.0005)
    return stop_price - offset if side is Side.SELL else stop_price + offset


def _format_decimal(value: float) -> str:
    """Plain decimal text. Binance rejects scientific notation outright."""
    return f"{value:.8f}".rstrip("0").rstrip(".") or "0"


def _receipt_from(
    payload: dict, symbol: str, side: Side, client_order_id: str, quantity: float
) -> OrderReceipt:
    fills = payload.get("fills") or []
    filled = _as_float(payload.get("executedQty")) or 0.0
    average = None
    if fills:
        spent = sum(
            (_as_float(f.get("price")) or 0.0) * (_as_float(f.get("qty")) or 0.0) for f in fills
        )
        got = sum(_as_float(f.get("qty")) or 0.0 for f in fills)
        average = spent / got if got else None
    elif filled and _as_float(payload.get("cummulativeQuoteQty")):
        average = (_as_float(payload.get("cummulativeQuoteQty")) or 0.0) / filled
    return OrderReceipt(
        symbol=symbol.upper(),
        side=side,
        client_order_id=str(payload.get("clientOrderId") or client_order_id),
        order_id=str(payload.get("orderId") or ""),
        status=str(payload.get("status") or "UNKNOWN"),
        quantity=quantity,
        filled_quantity=filled,
        average_price=average,
        raw=payload,
    )


def build_broker(*, paper: Optional[bool] = None) -> Broker:
    """The broker the environment describes.

    Paper unless told otherwise, and live needs both switches. Read the
    defaults as a sentence: nothing set means nothing at risk.
    """
    if paper is None:
        paper = _flag("PAPER_TRADING", default=True)
    if paper:
        return PaperBroker()

    testnet = _flag("BINANCE_TESTNET", default=True)
    armed = _flag("LIVE_TRADING_ARMED", default=False)
    broker = BinanceSpotBroker(
        api_key=(os.getenv("BINANCE_API_KEY") or "").strip(),
        api_secret=(os.getenv("BINANCE_API_SECRET") or "").strip(),
        testnet=testnet,
        live_armed=armed,
    )
    if broker.is_live and not armed:
        raise LiveTradingNotArmed(
            "BINANCE_TESTNET=false without LIVE_TRADING_ARMED=true; "
            "refusing to build a broker that would trade the real book"
        )
    return broker


def _flag(name: str, *, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"false", "0", "no", "off"}:
        return False
    if raw in {"true", "1", "yes", "on"}:
        return True
    # An unrecognised value is not a vote for the riskier option.
    return default
