# -*- coding: utf-8 -*-
"""The built frontend: the prerendered pricing page, the app shell for every screen, the assets."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import SESSION_COOKIE, me, request_locale, user_from_cookie
from .db import session_scope
from .funnel import record_pricing_view
from .models import Task, User

router = APIRouter()
APP_PATHS = ("/start", "/onboarding", "/dashboard", "/settings", "/admin")


def csp(public_url: str) -> str:
    socket = public_url.replace("https://", "wss://").replace("http://", "ws://")
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' https://cdn.paddle.com",
            "style-src 'self' 'unsafe-inline' https://cdn.paddle.com",
            # blob: is the pixel window showing the agent's screen, one picture at a time, from a fetch it made itself.
            "img-src 'self' data: blob: https://avatars.githubusercontent.com",
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
    pixel = dist / "pixel"
    if pixel.is_dir():
        async def pixel_page() -> FileResponse:
            # The same page at every address of a session or a project; what it shows, it asks the API for,
            # and the API answers only the owner.
            return FileResponse(pixel / "index.html", media_type="text/html; charset=utf-8")

        for path in ("/pixel/s/{public_id}", "/pixel/p/{project_id}"):
            app.add_api_route(path, pixel_page, methods=["GET"], include_in_schema=False)
        app.mount("/pixel", StaticFiles(directory=pixel, html=True), name="pixel")
    # HTML, CSS, JS and JSON shrink to about a third; on slow 3G that is most of the first screen.
    app.add_middleware(GZipMiddleware, minimum_size=500)

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


BOOT_SLOT = '<script id="boot" type="application/json">null</script>'


def _boot(request: Request, db: Session, user: User) -> dict[str, object]:
    """What the first screen needs, in the page itself: on slow 3G each saved round trip is 0.4 s."""
    from .billing import billing_config, subscription_view
    from .keys import key_summary, list_providers

    return {
        "me": me(request, user),
        "subscription": subscription_view(db, user),
        "billing": billing_config(request),
        "key": key_summary(user),
        "providers": list_providers(),
        "has_tasks": db.scalar(select(Task.id).where(Task.user_id == user.id).limit(1)) is not None,
    }


def _app_page(request: Request) -> Response:
    with session_scope(request.app.state.session_factory) as db:
        user = user_from_cookie(db, request.app.state.settings, request.cookies.get(SESSION_COOKIE))
        profile = _boot(request, db, user) if user is not None else None
    if profile is None:
        return RedirectResponse("/", status_code=302)
    page = _page(request, "app")
    if not isinstance(page, FileResponse):
        return page
    html = Path(page.path).read_text(encoding="utf-8")
    # The signed-in person, in the page: the app starts without asking /api/me first.
    boot = json.dumps(profile).replace("<", "\\u003c")
    html = html.replace(BOOT_SLOT, f'<script id="boot" type="application/json">{boot}</script>', 1)
    return HTMLResponse(html)


@router.get("/new", include_in_schema=False)
def new_task() -> Response:
    """Tasks are written in the workspace now; an old bookmark or link lands there."""
    return RedirectResponse("/pixel/", status_code=307)


for _path in APP_PATHS:
    router.add_api_route(_path, _app_page, methods=["GET"], include_in_schema=False)
router.add_api_route("/tasks/{task_id}", _app_page, methods=["GET"], include_in_schema=False)
