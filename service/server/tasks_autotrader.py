"""The background loop that runs the watchlist on a timer.

It exists only when auto-trading is armed. Disarmed, this module starts
nothing: there is no "it was running but not trading" state to reason about,
which is one fewer thing that can be misread at 3am.

The sweep itself is synchronous and talks to an exchange over the network, so
it runs in a worker thread. Blocking the event loop inside a trading loop would
stall every other request in the process, including the one a human reaches for
when they want the kill switch.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from engine import Engine, get_engine

# A floor, not a suggestion. A loop tighter than this adds exchange rate-limit
# bans to whatever else is going wrong, and no 5m strategy needs it.
MIN_PERIOD_SECONDS = 15.0
DEFAULT_PERIOD_SECONDS = 60.0


def period_seconds() -> float:
    try:
        asked = float((os.getenv("AUTO_TRADE_PERIOD_SECONDS") or "").strip())
    except (TypeError, ValueError):
        return DEFAULT_PERIOD_SECONDS
    return max(MIN_PERIOD_SECONDS, asked)


async def auto_trade_loop(logger, engine: Optional[Engine] = None, iterations: int = 0) -> int:
    """Sweep the watchlist forever, or ``iterations`` times in a test.

    One bad sweep never ends the loop. A symbol that cannot be fetched, a
    rejected order, a provider that changed its payload — all of it is logged
    and the next cycle tries again, because a trading loop that dies on the
    first error leaves open positions with nobody watching them.
    """
    engine = engine or get_engine()
    delay = period_seconds()
    completed = 0

    while True:
        try:
            decisions = await asyncio.to_thread(engine.sweep)
            executed = [d for d in decisions if d.get("stage") == "executed"]
            if executed:
                logger.info(
                    "Auto-trader placed %s order(s): %s",
                    len(executed),
                    ", ".join(f"{d['symbol']} {d['proposal'].get('direction')}" for d in executed),
                )
            else:
                logger.debug("Auto-trader swept %s symbol(s), nothing taken", len(decisions))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.warning("Auto-trader sweep failed: %s: %s", type(exc).__name__, exc)

        completed += 1
        if iterations and completed >= iterations:
            return completed
        await asyncio.sleep(delay)


def start_auto_trader(logger) -> list:
    """Start the loop if, and only if, auto-trading is armed."""
    engine = get_engine()
    if not engine.config.auto_trade:
        logger.info(
            "Auto-trader idle: AUTO_TRADE_ENABLED is not set. The analysis "
            "endpoints still work; no orders will be placed."
        )
        return []

    mode = engine.mode()
    logger.info(
        "Auto-trader armed: broker=%s testnet=%s watchlist=%s interval=%s every %.0fs",
        mode["broker"],
        mode["testnet"],
        ",".join(engine.config.watchlist),
        engine.config.interval,
        period_seconds(),
    )
    return [asyncio.create_task(auto_trade_loop(logger, engine))]
