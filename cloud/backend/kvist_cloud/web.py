# -*- coding: utf-8 -*-
"""The built frontend: the prerendered pricing page, the app shell for every screen, the assets."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import SESSION_COOKIE, request_locale, user_from_cookie
from .db import session_scope
from .funnel import record_pricing_view

router = APIRouter()
APP_PATHS = ("/start", "/onboarding", "/new", "/dashboard", "/settings", "/admin")


def csp(public_url: str) -> str:
    socket = public_url.replace("https://", "wss://").replace("http://", "ws://")
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' https://cdn.paddle.com",
            "style-src 'self' 'unsafe-inline' https://cdn.paddle.com",
            "img-src 'self' data: https://avatars.githubusercontent.com",
            "font-src 'self'",
            f"connect-src 'self' {socket} https://*.paddle.com",
            "frame-src https://*.paddle.com",
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'self' https://github.com",
        ]
    )


def install(app: FastAPI) -> None:
    dist: Path = app.state.settings.frontend_dist
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", csp(app.state.settings.public_url))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/assets/fonts/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif not request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response


def _page(request: Request, name: str) -> Response:
    dist: Path = request.app.state.settings.frontend_dist
    path = dist / f"{name}.{request_locale(request)}.html"
    if not path.exists():
        return PlainTextResponse("The frontend is not built: cd cloud/frontend && npm run build", status_code=503)
    return FileResponse(path, media_type="text/html; charset=utf-8")


def _signed_in(request: Request) -> bool:
    with session_scope(request.app.state.session_factory) as db:
        return user_from_cookie(db, request.app.state.settings, request.cookies.get(SESSION_COOKIE)) is not None


def _with_lang(request: Request, response: Response) -> Response:
    """?lang=ru on any page: remember the choice (the pricing page's switch is a plain link)."""
    lang = request.query_params.get("lang")
    if lang in ("en", "ru"):
        response.set_cookie("kc_lang", lang, max_age=365 * 24 * 3600, samesite="lax", path="/")
    return response


@router.get("/", include_in_schema=False)
@router.get("/pricing", include_in_schema=False)
def pricing(request: Request) -> Response:
    lang = request.query_params.get("lang")
    if lang in ("en", "ru"):
        response = RedirectResponse(request.url.path, status_code=303)
        return _with_lang(request, response)
    if request.url.path == "/" and _signed_in(request):
        return RedirectResponse("/dashboard", status_code=302)
    response = _page(request, "pricing")
    if response.status_code == 200:
        with session_scope(request.app.state.session_factory) as db:
            record_pricing_view(db, request, response)
    return response


def _app_page(request: Request) -> Response:
    if not _signed_in(request):
        return RedirectResponse("/", status_code=302)
    return _page(request, "app")


for _path in APP_PATHS:
    router.add_api_route(_path, _app_page, methods=["GET"], include_in_schema=False)
router.add_api_route("/tasks/{task_id}", _app_page, methods=["GET"], include_in_schema=False)
