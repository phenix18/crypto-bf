"""
Routes Module

所有 API 路由定义入口。
"""

import time
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from config import CORS_ORIGINS
from routes_agent import register_agent_routes
from routes_challenges import register_challenge_routes
from routes_experiments import register_experiment_routes
from routes_market import register_market_routes
from routes_misc import register_misc_routes
from routes_research import register_research_routes
from routes_shared import RouteContext
from routes_signals import register_signal_routes
from routes_team_missions import register_team_mission_routes
from routes_trading import register_trading_routes
from routes_users import register_user_routes


def create_app(lifespan: Optional[Callable[[FastAPI], Any]] = None) -> FastAPI:
    """Build the ASGI app.

    ``lifespan`` is the async context manager that owns process-wide startup and
    shutdown (background tasks, cache warming). The API entry point in main.py
    supplies one; tests call ``create_app()`` without it so they get the routes
    without the side effects.
    """
    app = FastAPI(title='AI-Trader API', lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=['*'],
        allow_headers=['*'],
    )

    @app.middleware('http')
    async def add_process_time_header(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        response.headers['X-Process-Time'] = str(time.time() - start_time)
        return response

    ctx = RouteContext()
    register_market_routes(app, ctx)
    register_agent_routes(app, ctx)
    register_signal_routes(app, ctx)
    register_trading_routes(app, ctx)
    register_experiment_routes(app, ctx)
    register_research_routes(app, ctx)
    register_challenge_routes(app, ctx)
    register_team_mission_routes(app, ctx)
    register_user_routes(app, ctx)
    register_misc_routes(app)
    return app
