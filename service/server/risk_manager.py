"""Position sizing and the limits that stop a bad run from becoming a ruinous one.

This module is the last thing between a proposal and real money, so it is
written to fail closed: every path that cannot prove a trade is safe rejects it.
A missing stop, an unknown equity, a stale account snapshot — none of those are
treated as "probably fine".

The sizing rule is risk-first. You choose what fraction of equity a losing trade
may cost; the distance to the stop then determines the quantity. Sizing by
notional instead ("always buy $500 of it") makes every trade's loss a function
of where the stop happens to land, which is how an account bleeds out on a run
of wide-stop trades.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional


class Halt(str, Enum):
    """Why the engine is refusing to open anything at all."""

    NONE = "none"
    DAILY_LOSS = "daily_loss"
    DRAWDOWN = "drawdown"
    COOLDOWN = "cooldown"
    KILL_SWITCH = "kill_switch"


@dataclass(frozen=True)
class RiskConfig:
    """Limits. Every one of them is a hard stop, not a suggestion.

    The defaults are deliberately timid. A bot that has never run against a real
    book should start small enough that a bug costs a lesson, not a balance.
    """

    # Share of equity a single losing trade may cost.
    risk_per_trade_percent: float = 0.5
    # Cap on a position's notional, as a share of equity, whatever the stop says.
    max_position_percent: float = 10.0
    max_open_positions: int = 3

    # Realised loss over one UTC day that halts trading until the next.
    max_daily_loss_percent: float = 3.0
    # Fall from peak equity that trips the kill switch and keeps it tripped.
    max_drawdown_percent: float = 15.0

    # Consecutive losers that trigger a pause, and how long it lasts.
    cooldown_losses: int = 3
    cooldown_minutes: int = 60

    # Exchange floor. Below this an order is rejected by the venue anyway.
    min_notional: float = 10.0
    # Refuse to risk a position whose stop is closer than this, in percent:
    # fees and spread eat the edge, and noise takes the stop out.
    min_stop_distance_percent: float = 0.05


@dataclass(frozen=True)
class AccountState:
    """What the risk layer needs to know about the account right now.

    ``as_of`` exists so a stale snapshot can be rejected rather than trusted.
    Sizing against an equity figure from twenty minutes ago is how a bot keeps
    buying after the account has already been emptied.
    """

    equity: float
    peak_equity: float
    realised_pnl_today: float
    open_positions: int
    consecutive_losses: int
    as_of: datetime
    cooldown_until: Optional[datetime] = None
    kill_switch: bool = False

    def with_trade_result(self, pnl: float, now: datetime, config: RiskConfig) -> "AccountState":
        """Fold a closed trade into the state, arming the cooldown on a losing run."""
        equity = self.equity + pnl
        losses = 0 if pnl > 0 else self.consecutive_losses + 1
        cooldown = self.cooldown_until
        if losses >= config.cooldown_losses:
            cooldown = now + timedelta(minutes=config.cooldown_minutes)
        return replace(
            self,
            equity=equity,
            peak_equity=max(self.peak_equity, equity),
            realised_pnl_today=self.realised_pnl_today + pnl,
            consecutive_losses=losses,
            cooldown_until=cooldown,
            as_of=now,
        )


@dataclass(frozen=True)
class RiskDecision:
    """Approved or not, how big, and the reasoning in full."""

    approved: bool
    quantity: float
    notional: float
    risk_amount: float
    halt: Halt
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "quantity": self.quantity,
            "notional": round(self.notional, 8),
            "risk_amount": round(self.risk_amount, 8),
            "halt": self.halt.value,
            "reasons": list(self.reasons),
        }


REJECTED = RiskDecision(
    approved=False, quantity=0.0, notional=0.0, risk_amount=0.0, halt=Halt.NONE
)


def drawdown_percent(account: AccountState) -> float:
    """Fall from peak equity, in percent. Zero when at or above the peak."""
    if account.peak_equity <= 0:
        return 0.0
    return max(0.0, (account.peak_equity - account.equity) / account.peak_equity * 100.0)


def daily_loss_percent(account: AccountState) -> float:
    """Today's realised loss as a percent of equity. Zero when up on the day."""
    if account.equity <= 0:
        return 100.0
    if account.realised_pnl_today >= 0:
        return 0.0
    return abs(account.realised_pnl_today) / account.equity * 100.0


def check_halt(
    account: AccountState,
    config: RiskConfig,
    now: datetime,
) -> tuple[Halt, Optional[str]]:
    """Account-level stops, checked before any per-trade reasoning.

    Order matters: the kill switch is reported ahead of the condition that
    tripped it, so the operator sees the thing that needs a human to clear.
    """
    if account.kill_switch:
        return Halt.KILL_SWITCH, "kill switch is engaged"

    fall = drawdown_percent(account)
    if fall >= config.max_drawdown_percent:
        return Halt.DRAWDOWN, (
            f"drawdown {fall:.2f}% has reached the {config.max_drawdown_percent}% limit"
        )

    loss = daily_loss_percent(account)
    if loss >= config.max_daily_loss_percent:
        return Halt.DAILY_LOSS, (
            f"down {loss:.2f}% on the day, limit is {config.max_daily_loss_percent}%"
        )

    if account.cooldown_until is not None and now < account.cooldown_until:
        remaining = (account.cooldown_until - now).total_seconds() / 60.0
        return Halt.COOLDOWN, (
            f"cooling off after {account.consecutive_losses} losses in a row, "
            f"{remaining:.0f} min remaining"
        )

    return Halt.NONE, None


def evaluate(
    *,
    entry: float,
    stop_loss: Optional[float],
    account: AccountState,
    config: Optional[RiskConfig] = None,
    now: Optional[datetime] = None,
    max_snapshot_age_seconds: float = 120.0,
) -> RiskDecision:
    """Size a trade, or refuse it and say why.

    Keyword-only on purpose: a positional mix-up between ``entry`` and
    ``stop_loss`` would invert the risk calculation silently.
    """
    config = config or RiskConfig()
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    halt, halt_reason = check_halt(account, config, now)
    if halt is not Halt.NONE:
        return replace(REJECTED, halt=halt, reasons=[halt_reason or halt.value])

    # A stop is not optional. Without one there is no defined loss, so there is
    # no way to size the trade and nothing to cap the damage.
    if stop_loss is None:
        reasons.append("no stop loss: refusing to open an undefined risk")
        return replace(REJECTED, reasons=reasons)

    if entry <= 0:
        reasons.append(f"entry price {entry} is not positive")
        return replace(REJECTED, reasons=reasons)

    if account.equity <= 0:
        reasons.append("account equity is zero or negative")
        return replace(REJECTED, reasons=reasons)

    age = (now - account.as_of).total_seconds()
    if age > max_snapshot_age_seconds:
        reasons.append(
            f"account snapshot is {age:.0f}s old, older than the "
            f"{max_snapshot_age_seconds:.0f}s limit"
        )
        return replace(REJECTED, reasons=reasons)
    if age < -5:
        # Clock skew between the bot and the exchange; do not guess which is right.
        reasons.append(f"account snapshot is {-age:.0f}s in the future, clocks disagree")
        return replace(REJECTED, reasons=reasons)

    if account.open_positions >= config.max_open_positions:
        reasons.append(
            f"{account.open_positions} positions already open, limit is "
            f"{config.max_open_positions}"
        )
        return replace(REJECTED, reasons=reasons)

    stop_distance = abs(entry - stop_loss)
    stop_percent = stop_distance / entry * 100.0
    if stop_distance <= 0:
        reasons.append("stop loss sits on the entry price")
        return replace(REJECTED, reasons=reasons)
    if stop_percent < config.min_stop_distance_percent:
        reasons.append(
            f"stop is {stop_percent:.3f}% away, closer than the "
            f"{config.min_stop_distance_percent}% floor"
        )
        return replace(REJECTED, reasons=reasons)

    risk_amount = account.equity * config.risk_per_trade_percent / 100.0
    quantity = risk_amount / stop_distance
    notional = quantity * entry

    cap = account.equity * config.max_position_percent / 100.0
    if notional > cap:
        # The stop is tight enough that risk-based sizing would over-leverage.
        # Cap the notional and accept risking less than the full allowance.
        quantity = cap / entry
        notional = cap
        risk_amount = quantity * stop_distance
        reasons.append(
            f"size capped at {config.max_position_percent}% of equity, "
            f"risking {risk_amount:.2f} instead of the full allowance"
        )

    if notional < config.min_notional:
        reasons.append(
            f"notional {notional:.2f} is below the {config.min_notional} exchange minimum"
        )
        return replace(REJECTED, reasons=reasons)

    reasons.append(
        f"risking {risk_amount:.2f} ({config.risk_per_trade_percent}% of "
        f"{account.equity:.2f}) over a {stop_percent:.2f}% stop"
    )
    return RiskDecision(
        approved=True,
        quantity=quantity,
        notional=notional,
        risk_amount=risk_amount,
        halt=Halt.NONE,
        reasons=reasons,
    )


def should_trip_kill_switch(account: AccountState, config: RiskConfig) -> bool:
    """Whether the drawdown has gone far enough to need a human before resuming."""
    return drawdown_percent(account) >= config.max_drawdown_percent
