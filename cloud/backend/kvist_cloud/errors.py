# -*- coding: utf-8 -*-
"""API errors: a stable code the frontend translates, a bilingual fallback, never a traceback."""
from __future__ import annotations

import logging
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("kvist_cloud")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: object) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


def support_code() -> str:
    return "K-" + secrets.token_hex(4).upper()


async def api_error_handler(_request: Request, error: ApiError) -> JSONResponse:
    body: dict[str, object] = {"code": error.code, "message": error.message, **error.extra}
    return JSONResponse({"error": body}, status_code=error.status)


async def unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
    code = support_code()
    # The traceback goes to the log (scrubbed by its filter), the person gets a code to quote.
    log.error("unhandled error %s on %s %s", code, request.method, request.url.path, exc_info=error)
    return JSONResponse(
        {
            "error": {
                "code": "internal",
                "message": "Something went wrong on our side. / Что-то сломалось на нашей стороне.",
                "support_code": code,
            }
        },
        status_code=500,
    )


def not_signed_in() -> ApiError:
    return ApiError(401, "not_signed_in", "Sign in first. / Сначала войди.")


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found. / Не найдено: {what}.")
