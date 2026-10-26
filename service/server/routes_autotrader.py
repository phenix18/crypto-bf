"""HTTP surface for the auto-trader.

Read endpoints are free. The one endpoint that can place an order is the one
that refuses unless auto-trading has been armed in the environment, and it says
so rather than failing quietly — an interface that cannot tell "no setup" from
"not allowed to trade" is one nobody can debug.

Errors are mapped rather than leaked: a closed network is a 503 with the reason
the market data layer gave, a rejected order is a 502, and auto-trading being
off is a 409. The caller should be able to tell those three apart from the
status code alone.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from broker import BrokerError
from engine import AutoTradeDisabled, get_engine
from market_data import MarketDataError
from routes_shared import RouteContext


class SymbolRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=24)
    interval: Optional[str] = None


class CloseRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=24)
    price: float = Field(gt=0)


class KillSwitchRequest(BaseModel):
    engaged: bool
    # Clearing the halt is a human decision, so it has to be spelled out. The
    # field exists to make an accidental or replayed request a no-op.
    confirm: str = ""


def register_autotrader_routes(app: FastAPI, ctx: RouteContext) -> None:
    @app.get("/api/auto/status")
    def auto_status() -> dict:
        return get_engine().status()

    @app.get("/api/auto/chart")
    def auto_chart(
        symbol: str = Query(min_length=3, max_length=24),
        interval: Optional[str] = None,
        limit: int = Query(default=0, ge=0, le=1000),
    ) -> dict:
        try:
            return get_engine().chart(symbol, interval, limit)
        except MarketDataError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/auto/analyse")
    def auto_analyse(request: SymbolRequest) -> dict:
        try:
            return get_engine().analyse(request.symbol, request.interval)
        except MarketDataError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/auto/trade")
    def auto_trade(request: SymbolRequest) -> dict:
        try:
            return get_engine().trade(request.symbol, request.interval)
        except AutoTradeDisabled as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except MarketDataError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except BrokerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/auto/calibration")
    def auto_calibration() -> dict:
        return get_engine().calibration()

    @app.get("/api/auto/audit")
    def auto_audit(limit: int = Query(default=100, ge=1, le=500)) -> dict:
        return get_engine().audit(limit)

    @app.post("/api/auto/close")
    def auto_close(request: CloseRequest) -> dict:
        try:
            closed = get_engine().close(request.symbol, request.price)
        except BrokerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if closed is None:
            raise HTTPException(status_code=404, detail=f"no open position in {request.symbol}")
        return closed

    @app.post("/api/auto/kill-switch")
    def auto_kill_switch(request: KillSwitchRequest) -> dict:
        if not request.engaged and request.confirm.strip().lower() != "release":
            raise HTTPException(
                status_code=400,
                detail=(
                    'releasing the kill switch needs confirm="release"; whatever '
                    "tripped it should be understood first"
                ),
            )
        return get_engine().set_kill_switch(request.engaged)
