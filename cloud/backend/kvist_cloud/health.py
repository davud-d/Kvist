# -*- coding: utf-8 -*-
"""One endpoint for the load balancer, the deploy script and the uptime monitor."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db import session_scope

log = logging.getLogger("kvist_cloud")
router = APIRouter()


@router.get("/healthz", include_in_schema=False)
def healthz(request: Request) -> JSONResponse:
    """200 when the process can reach its database, 503 when it cannot. It says nothing else about the server."""
    try:
        with session_scope(request.app.state.session_factory) as db:
            db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.exception("health check: the database is unreachable")
        return JSONResponse({"status": "down"}, status_code=503, headers={"Cache-Control": "no-store"})
    return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
