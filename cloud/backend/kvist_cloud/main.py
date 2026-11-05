# -*- coding: utf-8 -*-
"""The FastAPI app: settings, database, routers, error handlers and the built frontend."""
from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import auth, keys
from .config import Settings, load_settings
from .db import make_engine, make_session_factory, upgrade
from .errors import ApiError, api_error_handler, unhandled_error_handler, validation_error_handler
from .secrets import Vault, install_log_scrubber

log = logging.getLogger("kvist_cloud")

# State-changing API calls must carry this header. A cross-site form or <img> cannot set it,
# and a cross-site fetch() that sets it needs a CORS preflight this server never grants.
CLIENT_HEADER = "x-kvist-client"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_app(
    settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    install_log_scrubber(
        [settings.session_secret, settings.master_key, settings.github_client_secret, settings.paddle_webhook_secret]
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    upgrade(settings.database_url)
    engine = make_engine(settings.database_url)

    app = FastAPI(title="Kvist Cloud", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.http_transport = http_transport
    app.state.vault = Vault(settings.master_key)
    app.state.on_signed_in = None

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
    return app
