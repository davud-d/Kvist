# -*- coding: utf-8 -*-
"""The FastAPI app: settings, database, routers, error handlers and the built frontend."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import auth, billing, gateway, keys, ledger, machines, tasks, web
from .config import Settings, load_settings
from .db import make_engine, make_session_factory, upgrade
from .errors import ApiError, api_error_handler, unhandled_error_handler, validation_error_handler
from .events import EventHub
from .gateway import Gateway
from .providers import MachineProvider, make_provider
from .secrets import Vault, install_log_scrubber

log = logging.getLogger("kvist_cloud")

# State-changing API calls must carry this header. A cross-site form or <img> cannot set it,
# and a cross-site fetch() that sets it needs a CORS preflight this server never grants.
CLIENT_HEADER = "x-kvist-client"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.loop = asyncio.get_running_loop()
    loops = [asyncio.create_task(machines.sweep_forever(app)), asyncio.create_task(billing.sweep_forever(app))]
    try:
        yield
    finally:
        for loop in loops:
            loop.cancel()
        for task in list(app.state.background):
            task.cancel()
        close = getattr(app.state.provider, "close", None)
        if close is not None:
            await close()
        app.state.engine.dispose()


def create_app(
    settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    provider: MachineProvider | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    install_log_scrubber(
        [
            settings.session_secret,
            settings.master_key,
            settings.github_client_secret,
            settings.paddle_webhook_secret,
            settings.paddle_api_key,
        ]
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    upgrade(settings.database_url)
    engine = make_engine(settings.database_url)

    app = FastAPI(title="Kvist Cloud", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.http_transport = http_transport
    app.state.vault = Vault(settings.master_key)
    app.state.provider = provider or make_provider(settings)
    app.state.background = set()
    app.state.on_signed_in = machines.on_signed_in
    app.state.gateway = Gateway()
    app.state.hub = EventHub()
    # The ledger's clock; tests move it by hand.
    app.state.clock = time.time

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.middleware("http")
    async def require_client_header(request: Request, call_next):
        if (
            request.url.path.startswith("/api/")
            and request.method not in SAFE_METHODS
            and request.headers.get(CLIENT_HEADER) != "1"
        ):
            return JSONResponse(
                {"error": {"code": "csrf", "message": "Reload the page. / Обнови страницу."}}, status_code=403
            )
        return await call_next(request)

    app.include_router(auth.router)
    app.include_router(keys.router)
    app.include_router(machines.router)
    app.include_router(gateway.router)
    app.include_router(tasks.router)
    app.include_router(billing.router)
    app.include_router(ledger.router)
    web.install(app)
    app.include_router(web.router)
    return app
