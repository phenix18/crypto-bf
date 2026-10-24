"""The loop: look at the market, decide, place the orders, learn from the result.

This is where the other modules meet. The order of the gates is the design, and
it is deliberately pessimistic — each stage can only refuse what the one before
it allowed:

    candles -> strategy -> risk sizing -> calibration gate -> AI council -> orders

The strategy proposes a direction. The risk layer decides whether the account
can afford it and how big. The calibration gate asks whether setups like this
have actually worked. The council reviews all of it and can veto. Only then do
orders go out, stop first in the same breath as the entry.

Closed trades feed straight back into the calibration history, so the gate
tightens or loosens on evidence as the record grows. That is the learning part:
not a model being retrained, but the thing that decides getting measurably
better informed.

Two rules worth stating outright, because both are easy to get wrong and
expensive when you do:

**One position per symbol, one trade per bar.** The entry id is derived from the
bar, so a loop that runs three times inside one candle still places one order.

**A position without a stop does not exist.** If the protective order fails
after the entry filled, the position is closed immediately at market. A naked
position left running is the worst state this system can be in, worse than a
small realised loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

import calibration
import risk_manager
from ai_agents import CouncilConfig, build_briefing, deliberate
from broker import Broker, BrokerError, PaperBroker, Side, client_order_id, side_for
from calibration import CalibrationConfig, Outcome, TradeRecord
from indicators import Candle
from llm_client import LlmClient, NullClient
from market_data import CandleSource
from risk_manager import AccountState, RiskConfig
from strategy_engine import StrategyConfig, analyse


@dataclass(frozen=True)
class Position:
    """An open trade, with the levels it was opened under."""

    symbol: str
    interval: str
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    quantity: float
    opened_at: datetime
    bar_open_time: int
    confluence: float
    regime: str
    entry_order_id: str

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop_loss)

    def unrealised(self, price: float) -> float:
        move = price - self.entry if self.direction == "long" else self.entry - price
        return move * self.quantity

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "direction": self.direction,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "quantity": self.quantity,
            "opened_at": self.opened_at.isoformat(),
            "bar_open_time": self.bar_open_time,
            "confluence": self.confluence,
            "regime": self.regime,
            "entry_order_id": self.entry_order_id,
        }


@dataclass(frozen=True)
class ClosedTrade:
    """A finished trade, in money and in R."""

    position: Position
    exit_price: float
    outcome: Outcome
    r_multiple: float
    pnl: float
    closed_at: datetime
    reason: str

    def to_dict(self) -> dict:
        return {
            "position": self.position.to_dict(),
            "exit_price": self.exit_price,
            "outcome": self.outcome.value,
            "r_multiple": round(self.r_multiple, 4),
            "pnl": round(self.pnl, 8),
            "closed_at": self.closed_at.isoformat(),
            "reason": self.reason,
        }

    def to_record(self) -> TradeRecord:
        return TradeRecord(
            symbol=self.position.symbol,
            interval=self.position.interval,
            regime=self.position.regime,
            direction=self.position.direction,
            confluence=self.position.confluence,
            outcome=self.outcome,
            r_multiple=self.r_multiple,
        )


# ------------------------------------------------------------------ portfolio --


@dataclass
class Portfolio:
    """Follows the main balance: equity, the peak, the day, the losing streak.

    This is the "agent that watches the balance" the user asked for, and it is
    bookkeeping rather than intelligence on purpose. The judgement lives in
    `risk_manager`; this part has to be boring and exactly right, because every
    limit in the system is computed from these numbers.
    """

    equity: float
    starting_equity: float = 0.0
    peak_equity: float = 0.0
    realised_pnl_today: float = 0.0
    consecutive_losses: int = 0
    cooldown_until: Optional[datetime] = None
    kill_switch: bool = False
    day: Optional[str] = None
    positions: dict[str, Position] = field(default_factory=dict)
    closed: list[ClosedTrade] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.starting_equity = self.starting_equity or self.equity
        self.peak_equity = self.peak_equity or self.equity

    def account_state(self, now: Optional[datetime] = None) -> AccountState:
        now = now or datetime.now(timezone.utc)
        self.roll_day(now)
        return AccountState(
            equity=self.equity,
            peak_equity=self.peak_equity,
            realised_pnl_today=self.realised_pnl_today,
            open_positions=len(self.positions),
            consecutive_losses=self.consecutive_losses,
            as_of=now,
            cooldown_until=self.cooldown_until,
            kill_switch=self.kill_switch,
        )

    def roll_day(self, now: datetime) -> None:
        """Reset the day's realised loss when the UTC date turns over.

        Without this the daily-loss halt becomes a permanent one: a bad Tuesday
        would keep the bot flat for the rest of the month.
        """
        today = now.astimezone(timezone.utc).date().isoformat()
        if self.day is None:
            self.day = today
        elif today != self.day:
            self.day = today
            self.realised_pnl_today = 0.0

    def open(self, position: Position) -> None:
        if position.symbol in self.positions:
            raise ValueError(f"already holding {position.symbol}")
        self.positions[position.symbol] = position

    def close(
        self,
        position: Position,
        *,
        exit_price: float,
        now: datetime,
        reason: str,
        config: Optional[RiskConfig] = None,
    ) -> ClosedTrade:
        config = config or RiskConfig()
        outcome, r_multiple = calibration.classify(
            position.entry, exit_price, position.stop_loss, position.direction
        )
        pnl = position.unrealised(exit_price)

        self.positions.pop(position.symbol, None)
        state = AccountState(
            equity=self.equity,
            peak_equity=self.peak_equity,
            realised_pnl_today=self.realised_pnl_today,
            open_positions=len(self.positions),
            consecutive_losses=self.consecutive_losses,
            as_of=now,
            cooldown_until=self.cooldown_until,
            kill_switch=self.kill_switch,
        ).with_trade_result(pnl, now, config)

        self.equity = state.equity
        self.peak_equity = state.peak_equity
        self.realised_pnl_today = state.realised_pnl_today
        self.consecutive_losses = state.consecutive_losses
        self.cooldown_until = state.cooldown_until
        if risk_manager.should_trip_kill_switch(state, config):
            # Latched on purpose: clearing it needs a human, because whatever
            # produced a drawdown this size is not something the loop can
            # reason its way out of.
            self.kill_switch = True

        trade = ClosedTrade(
            position=position,
            exit_price=exit_price,
            outcome=outcome,
            r_multiple=r_multiple,
            pnl=pnl,
            closed_at=now,
            reason=reason,
        )
        self.closed.append(trade)
        return trade

    def to_dict(self) -> dict:
        return {
            "equity": round(self.equity, 8),
            "starting_equity": round(self.starting_equity, 8),
            "peak_equity": round(self.peak_equity, 8),
            "realised_pnl_today": round(self.realised_pnl_today, 8),
            "total_pnl": round(self.equity - self.starting_equity, 8),
            "drawdown_percent": round(
                risk_manager.drawdown_percent(self.account_state()), 4
            ),
            "consecutive_losses": self.consecutive_losses,
            "kill_switch": self.kill_switch,
            "open_positions": [p.to_dict() for p in self.positions.values()],
            "closed_trades": len(self.closed),
            "day": self.day,
        }


# ------------------------------------------------------------------- decision --


# Every stage a decision can stop at. The interface carries a label for each
# one (service/frontend/src/autotraderLabels.ts), so adding a stage here
# without adding its label there leaves the screen showing a raw English word
# to a reader who chose another language. A test on each side pins the list.
STAGES: tuple[str, ...] = (
    "strategy",
    "risk",
    "calibration",
    "council",
    "approved",
    "executed",
    "execution",
    "position",
    "bar",
    "error",
)


@dataclass(frozen=True)
class Decision:
    """Everything that went into one verdict, for the UI and the audit trail."""

    symbol: str
    interval: str
    at: datetime
    approved: bool
    stage: str          # where it stopped: strategy, risk, calibration, council, executed
    reasons: list[str]
    proposal: dict = field(default_factory=dict)
    risk: dict = field(default_factory=dict)
    calibration: dict = field(default_factory=dict)
    council: dict = field(default_factory=dict)
    quantity: float = 0.0
    orders: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "at": self.at.isoformat(),
            "approved": self.approved,
            "stage": self.stage,
            "reasons": list(self.reasons),
            "proposal": self.proposal,
            "risk": self.risk,
            "calibration": self.calibration,
            "council": self.council,
            "quantity": self.quantity,
            "orders": list(self.orders),
        }


# ----------------------------------------------------------------- the trader --


@dataclass
class AutoTrader:
    """Runs the pipeline for a symbol, and the orders if everything agrees."""

    source: CandleSource
    broker: Broker
    portfolio: Portfolio
    strategy_config: StrategyConfig = field(default_factory=StrategyConfig)
    risk_config: RiskConfig = field(default_factory=RiskConfig)
    calibration_config: CalibrationConfig = field(default_factory=CalibrationConfig)
    council_config: CouncilConfig = field(default_factory=CouncilConfig)
    llm_client: LlmClient = field(default_factory=NullClient)
    history: list[TradeRecord] = field(default_factory=list)
    audit: list[dict] = field(default_factory=list)
    candle_limit: int = 300
    breadth: dict[str, dict] = field(default_factory=dict)
    # Bars already traded, so a loop running several times inside one candle
    # does not stack positions.
    traded_bars: set = field(default_factory=set)
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    # -- reading -----------------------------------------------------------

    def candles(self, symbol: str, interval: str) -> list[Candle]:
        return self.source.fetch(symbol, interval, self.candle_limit)

    # -- the pipeline ------------------------------------------------------

    def evaluate(
        self,
        symbol: str,
        interval: str,
        *,
        candles: Optional[Sequence[Candle]] = None,
        now: Optional[datetime] = None,
    ) -> Decision:
        """Run every gate and report where it stopped. Places no orders."""
        now = now or self.clock()
        candles = list(candles) if candles is not None else self.candles(symbol, interval)

        proposal = analyse(symbol, interval, candles, self.strategy_config)
        payload = proposal.to_dict()
        if not proposal.actionable:
            return Decision(
                symbol=symbol,
                interval=interval,
                at=now,
                approved=False,
                stage="strategy",
                reasons=proposal.blockers or ["no directional edge in this bar"],
                proposal=payload,
            )

        account = self.portfolio.account_state(now)
        risk = risk_manager.evaluate(
            entry=proposal.entry,
            stop_loss=proposal.stop_loss,
            account=account,
            config=self.risk_config,
            now=now,
        )
        if not risk.approved:
            return Decision(
                symbol=symbol,
                interval=interval,
                at=now,
                approved=False,
                stage="risk",
                reasons=risk.reasons,
                proposal=payload,
                risk=risk.to_dict(),
            )

        verdict = calibration.gate(
            confluence=proposal.confluence,
            regime=proposal.regime.value,
            direction=proposal.direction.value,
            history=self.history,
            config=self.calibration_config,
        )
        if not verdict.allowed:
            return Decision(
                symbol=symbol,
                interval=interval,
                at=now,
                approved=False,
                stage="calibration",
                reasons=verdict.reasons,
                proposal=payload,
                risk=risk.to_dict(),
                calibration=verdict.to_dict(),
            )

        council = deliberate(
            briefing=build_briefing(
                symbol=symbol,
                interval=interval,
                proposal=payload,
                risk=risk.to_dict(),
                calibration=verdict.to_dict(),
                breadth=self.breadth.get(symbol.upper(), {}),
                account=self.portfolio.to_dict(),
            ),
            client=self.llm_client,
            config=self.council_config,
        )

        return Decision(
            symbol=symbol,
            interval=interval,
            at=now,
            approved=council.approved,
            stage="council" if not council.approved else "approved",
            reasons=council.reasons,
            proposal=payload,
            risk=risk.to_dict(),
            calibration=verdict.to_dict(),
            council=council.to_dict(),
            quantity=risk.quantity,
        )

    # -- acting ------------------------------------------------------------

    def trade(
        self,
        symbol: str,
        interval: str,
        *,
        candles: Optional[Sequence[Candle]] = None,
        now: Optional[datetime] = None,
    ) -> Decision:
        """Evaluate, and place the orders if every gate agreed."""
        now = now or self.clock()
        candles = list(candles) if candles is not None else self.candles(symbol, interval)

        # Settle anything already open before deciding anything new: the risk
        # layer sizes against open positions, so stale ones would distort it.
        self.settle(symbol, interval, candles, now=now)

        symbol_key = symbol.upper()
        if symbol_key in self.portfolio.positions:
            return self._refused(
                symbol, interval, now, "position", [f"already holding {symbol_key}"]
            )

        bar_open_time = candles[-1].open_time if candles else 0
        bar_key = (symbol_key, interval, bar_open_time)
        if bar_key in self.traded_bars:
            return self._refused(
                symbol, interval, now, "bar", ["this bar has already been traded"]
            )

        decision = self.evaluate(symbol, interval, candles=candles, now=now)
        self._record(decision)
        if not decision.approved:
            return decision

        return self._execute(decision, bar_open_time=bar_open_time, now=now)

    def _execute(self, decision: Decision, *, bar_open_time: int, now: datetime) -> Decision:
        symbol = decision.symbol.upper()
        proposal = decision.proposal
        direction = proposal["direction"]
        entry = float(proposal["entry"])
        stop_loss = float(proposal["stop_loss"])
        take_profit = float(proposal["take_profit"])

        try:
            side = side_for(direction)
            rules = self.broker.rules(symbol)
        except BrokerError as exc:
            return self._failed(decision, [str(exc)])

        quantity = rules.round_quantity(decision.quantity)
        reason = rules.reject_reason(quantity, entry)
        if reason:
            return self._failed(decision, [f"exchange filters refuse this order: {reason}"])

        order_id = client_order_id(
            symbol=symbol, direction=direction, interval=decision.interval,
            bar_open_time=bar_open_time,
        )
        self.broker.mark_price(symbol, entry)

        try:
            entry_receipt = self.broker.place_market_entry(
                symbol=symbol, side=side, quantity=quantity, client_order_id=order_id
            )
        except BrokerError as exc:
            return self._failed(decision, [f"entry rejected: {exc}"])

        # Remember the bar as traded the moment an order goes out, not after the
        # whole sequence succeeds: a half-placed trade must not be retried into
        # a second position.
        self.traded_bars.add((symbol, decision.interval, bar_open_time))

        if not entry_receipt.filled:
            return self._failed(
                decision,
                [f"entry did not fill ({entry_receipt.status})"],
                orders=[entry_receipt.to_dict()],
            )

        filled_price = entry_receipt.average_price or entry
        filled_quantity = entry_receipt.filled_quantity or quantity
        orders = [entry_receipt.to_dict()]

        exit_side = Side.SELL if side is Side.BUY else Side.BUY
        try:
            protective = self.broker.place_protective_exit(
                symbol=symbol,
                side=exit_side,
                quantity=filled_quantity,
                stop_price=stop_loss,
                take_profit=take_profit,
                client_order_id=f"{order_id}-x"[-36:],
            )
            orders.append(protective.to_dict())
        except BrokerError as exc:
            # Unprotected is the worst state in the system. Get flat now and
            # take whatever that costs.
            unwound = self._emergency_exit(
                symbol, exit_side, filled_quantity, filled_price, order_id, now
            )
            orders.extend(unwound)
            return self._failed(
                decision,
                [f"protective order failed ({exc}); position closed immediately at market"],
                orders=orders,
            )

        position = Position(
            symbol=symbol,
            interval=decision.interval,
            direction=direction,
            entry=filled_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=filled_quantity,
            opened_at=now,
            bar_open_time=bar_open_time,
            confluence=float(proposal.get("confluence") or 0.0),
            regime=str(proposal.get("regime") or "unknown"),
            entry_order_id=order_id,
        )
        self.portfolio.open(position)

        executed = replace(
            decision,
            stage="executed",
            orders=orders,
            quantity=filled_quantity,
            reasons=list(decision.reasons)
            + [
                f"filled {filled_quantity} at {filled_price} with the stop at "
                f"{stop_loss} and the target at {take_profit}"
            ],
        )
        self._record(executed)
        return executed

    def _emergency_exit(
        self,
        symbol: str,
        exit_side: Side,
        quantity: float,
        price: float,
        order_id: str,
        now: datetime,
    ) -> list[dict]:
        try:
            receipt = self.broker.place_market_entry(
                symbol=symbol,
                side=exit_side,
                quantity=quantity,
                client_order_id=f"{order_id}-u"[-36:],
            )
            return [receipt.to_dict()]
        except BrokerError as exc:
            # Nothing left to do from here but shout. A latched kill switch and
            # an audit line are what a human needs to act on this.
            self.portfolio.kill_switch = True
            self._note(
                {
                    "kind": "unprotected_position",
                    "symbol": symbol,
                    "quantity": quantity,
                    "price": price,
                    "error": str(exc),
                    "at": now.isoformat(),
                    "message": (
                        "could not place a stop and could not unwind; kill switch "
                        "engaged, this needs a human"
                    ),
                }
            )
            return [{"error": str(exc), "symbol": symbol, "unwind": "failed"}]

    # -- settling ----------------------------------------------------------

    def settle(
        self,
        symbol: str,
        interval: str,
        candles: Sequence[Candle],
        *,
        now: Optional[datetime] = None,
    ) -> list[ClosedTrade]:
        """Resolve open positions against the latest bar.

        When a single bar covers both the stop and the target, it is scored as
        a loss. The bar does not say which came first, and assuming the good
        one is how a backtest — and then the calibration layer reading it —
        turns optimistic.
        """
        now = now or self.clock()
        position = self.portfolio.positions.get(symbol.upper())
        if position is None or not candles:
            return []

        closed: list[ClosedTrade] = []
        for candle in candles:
            if candle.open_time <= position.bar_open_time:
                continue  # the entry bar itself and anything before it
            if position.direction == "long":
                hit_stop = candle.low <= position.stop_loss
                hit_target = candle.high >= position.take_profit
            else:
                hit_stop = candle.high >= position.stop_loss
                hit_target = candle.low <= position.take_profit

            if not hit_stop and not hit_target:
                continue
            exit_price = position.stop_loss if hit_stop else position.take_profit
            reason = "stop" if hit_stop else "target"
            self._book_exit(position, exit_price)
            trade = self.portfolio.close(
                position, exit_price=exit_price, now=now, reason=reason, config=self.risk_config
            )
            # The learning step: the gate reads this on the next evaluation.
            self.history.append(trade.to_record())
            self._note({"kind": "closed", **trade.to_dict()})
            closed.append(trade)
            break
        return closed

    def _book_exit(self, position: Position, exit_price: float) -> None:
        """Annule les ordres de protection et fait constater la sortie au courtier.

        Les deux vont ensemble. La place tient son propre solde et n'a rien à
        constater ; le courtier papier, lui, ne voit jamais ses ordres de
        protection s'exécuter — sans ce rappel, l'entrée débite sa trésorerie
        et la sortie ne la rend pas.
        """
        self.broker.cancel_all(position.symbol)
        exit_side = Side.SELL if position.direction == "long" else Side.BUY
        self.broker.record_exit_fill(
            symbol=position.symbol,
            side=exit_side,
            quantity=position.quantity,
            price=exit_price,
        )

    def close_now(self, symbol: str, price: float, *, now: Optional[datetime] = None) -> Optional[ClosedTrade]:
        """Flatten a position at a given price. For the kill switch and for hand exits."""
        now = now or self.clock()
        position = self.portfolio.positions.get(symbol.upper())
        if position is None:
            return None
        self._book_exit(position, price)
        trade = self.portfolio.close(
            position, exit_price=price, now=now, reason="manual", config=self.risk_config
        )
        self.history.append(trade.to_record())
        self._note({"kind": "closed", **trade.to_dict()})
        return trade

    # -- the loop ----------------------------------------------------------

    def run_once(
        self, symbols: Sequence[str], interval: str, *, now: Optional[datetime] = None
    ) -> list[Decision]:
        """One pass over the watchlist. A failure on one symbol is not the others' problem."""
        decisions = []
        for symbol in symbols:
            try:
                decisions.append(self.trade(symbol, interval, now=now))
            except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the loop
                failed = Decision(
                    symbol=symbol,
                    interval=interval,
                    at=now or self.clock(),
                    approved=False,
                    stage="error",
                    reasons=[f"{type(exc).__name__}: {exc}"],
                )
                self._record(failed)
                decisions.append(failed)
        return decisions

    # -- plumbing ----------------------------------------------------------

    def _refused(self, symbol, interval, now, stage, reasons) -> Decision:
        decision = Decision(
            symbol=symbol, interval=interval, at=now, approved=False, stage=stage, reasons=reasons
        )
        self._record(decision)
        return decision

    def _failed(self, decision: Decision, reasons: list[str], orders=None) -> Decision:
        failed = replace(
            decision,
            approved=False,
            stage="execution",
            reasons=list(decision.reasons) + reasons,
            orders=list(orders or decision.orders),
        )
        self._record(failed)
        return failed

    def _record(self, decision: Decision) -> None:
        self._note({"kind": "decision", **decision.to_dict()})

    def _note(self, entry: dict) -> None:
        self.audit.append(entry)
        # The audit trail is for reading after something went wrong, so it is
        # capped rather than allowed to grow until the process dies.
        if len(self.audit) > 2000:
            del self.audit[: len(self.audit) - 2000]


def build_auto_trader(
    *,
    source: CandleSource,
    broker: Optional[Broker] = None,
    equity: float = 10_000.0,
    llm_client: Optional[LlmClient] = None,
) -> AutoTrader:
    """An auto trader wired for the safe defaults: paper broker, no model."""
    broker = broker or PaperBroker(quote_balance=equity)
    return AutoTrader(
        source=source,
        broker=broker,
        portfolio=Portfolio(equity=equity),
        llm_client=llm_client or NullClient(),
    )
