# -*- coding: utf-8 -*-
"""The built frontend: the prerendered pricing page, the app shell for every screen, the assets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import SESSION_COOKIE, me, request_locale, user_from_cookie
from .db import session_scope
from .errors import ApiError
from .funnel import record_pricing_view
from .models import Task, User

router = APIRouter()
LEGAL_DIR = Path(__file__).resolve().parents[2] / "legal"
LEGAL_FILES = {"/terms": "terms.html", "/privacy": "privacy.html",
               "/terms.pdf": "terms.pdf", "/privacy.pdf": "privacy.pdf"}
APP_PATHS = ("/start", "/onboarding", "/dashboard", "/settings", "/admin")


# What the phone app's page loads from Google besides itself: the Google Pay button (its script, its picture, its
# sheet). Only /m/ gets it; no other page of the site may load a third party's script.
GOOGLE_PAY_SCRIPT = "https://pay.google.com"
GOOGLE_PAY_IMAGES = "https://www.gstatic.com https://pay.google.com"


def mobile_build_id(folder: Path) -> str:
    """Names the phone app's files as they are now: the address of every script and style carries it."""
    digest = hashlib.sha256()
    for file in sorted(folder.rglob("*")):
        if file.is_file():
            digest.update(str(file.relative_to(folder)).encode())
            digest.update(file.read_bytes())
    return digest.hexdigest()[:12]


def is_phone_app(path: str) -> bool:
    return path == "/m" or path.startswith("/m/")


# The phone page is also the cloud half of the IDE panels: VS Code's webview (vscode-webview:, inside the workbench's
# vscode-file:) and the JetBrains panel's JCEF page (file:). frame-ancestors checks every ancestor, so all are named.
IDE_FRAME_ANCESTORS = "'self' vscode-webview: vscode-file: file:"


def csp(public_url: str, google_pay: bool = False, ide_frame: bool = False) -> str:
    socket = public_url.replace("https://", "wss://").replace("http://", "ws://")
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'" + (f" {GOOGLE_PAY_SCRIPT}" if google_pay else ""),
            "style-src 'self' 'unsafe-inline'",
            # blob: is the pixel window showing the agent's screen, one picture at a time, from a fetch it made itself.
            "img-src 'self' data: blob: https://avatars.githubusercontent.com"
            + (f" {GOOGLE_PAY_IMAGES}" if google_pay else ""),
            "font-src 'self'",
            f"connect-src 'self' {socket}" + (f" {GOOGLE_PAY_SCRIPT}" if google_pay else ""),
            *([f"frame-src {GOOGLE_PAY_SCRIPT}"] if google_pay else []),
            "frame-ancestors " + (IDE_FRAME_ANCESTORS if ide_frame else "'none'"),
            "base-uri 'none'",
            "form-action 'self' https://github.com",
        ]
    )


def install(app: FastAPI) -> None:
    dist: Path = app.state.settings.frontend_dist
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    # The workspace page is built from cloud/workspace and served at /workspace.
    workspace = dist / "workspace"
    if workspace.is_dir():
        async def workspace_page() -> FileResponse:
            # The same page at every address of a session or a project; what it shows, it asks the API for,
            # and the API answers only the owner.
            return FileResponse(workspace / "index.html", media_type="text/html; charset=utf-8")

        for path in ("/workspace/s/{public_id}", "/workspace/p/{project_id}"):
            app.add_api_route(path, workspace_page, methods=["GET"], include_in_schema=False)
        app.add_api_route("/pixel", old_workspace_address, methods=["GET"], include_in_schema=False)
        app.add_api_route("/pixel/{rest:path}", old_workspace_address, methods=["GET"], include_in_schema=False)
        app.mount("/workspace", StaticFiles(directory=workspace, html=True), name="workspace")
    # The phone app: one page at every address of a session or a project, the files beside it.
    mobile = dist / "mobile"
    if mobile.is_dir():
        build = mobile_build_id(mobile)
        page = (mobile / "index.html").read_text(encoding="utf-8").replace("__BUILD__", build)

        async def mobile_page() -> Response:
            return HTMLResponse(page, headers={"Cache-Control": "no-store"})

        async def mobile_build() -> Response:
            """What the open page asks when the app comes back to the front: has a new release been put up meanwhile."""
            return PlainTextResponse(build, headers={"Cache-Control": "no-store"})

        async def mobile_file(build_id: str, path: str) -> FileResponse:
            """The page's scripts and styles live at /m/v/<build>/...: a new release is a new address, so a browser or a
            proxy that keeps files for hours (Cloudflare's does) can never show an old version of the app."""
            target = (mobile / path).resolve()
            if not target.is_file() or mobile.resolve() not in target.parents:
                raise ApiError(404, "not_found", "Not found. / Не найдено.")
            return FileResponse(target)

        for path in ("/m", "/m/", "/m/s/{public_id}", "/m/p/{project_id}"):
            app.add_api_route(path, mobile_page, methods=["GET"], include_in_schema=False)
        app.add_api_route("/m/build", mobile_build, methods=["GET"], include_in_schema=False)
        app.add_api_route("/m/v/{build_id}/{path:path}", mobile_file, methods=["GET"], include_in_schema=False)
        app.mount("/m", StaticFiles(directory=mobile), name="mobile")
    # HTML, CSS, JS and JSON shrink to about a third; on slow 3G that is most of the first screen.
    app.add_middleware(GZipMiddleware, minimum_size=500)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        settings = app.state.settings
        phone_app = is_phone_app(request.url.path)
        google_pay = settings.wallet is not None and phone_app
        policy = csp(settings.public_url, google_pay, ide_frame=phone_app)
        response.headers.setdefault("Content-Security-Policy", policy)
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
        "providers": list_providers(request),
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


def _legal_route(name: str):
    media = "application/pdf" if name.endswith(".pdf") else "text/html; charset=utf-8"

    def serve() -> Response:
        return FileResponse(LEGAL_DIR / name, media_type=media)

    return serve


def old_workspace_address(request: Request, rest: str = "") -> RedirectResponse:
    """The workspace lived at /pixel/; a bookmark or a link from then opens the same place at /workspace/."""
    target = "/workspace/" + quote(rest, safe="/")
    if request.url.query:
        target += "?" + request.url.query
    return RedirectResponse(target, status_code=308)


@router.get("/new", include_in_schema=False)
def new_task() -> Response:
    """Tasks are written in the workspace now; an old bookmark or link lands there."""
    return RedirectResponse("/workspace/", status_code=307)


for _path in APP_PATHS:
    router.add_api_route(_path, _app_page, methods=["GET"], include_in_schema=False)
router.add_api_route("/tasks/{task_id}", _app_page, methods=["GET"], include_in_schema=False)
for _path, _name in LEGAL_FILES.items():
    # Public on purpose: the documents must be readable before anyone signs in, and by the payment provider.
    router.add_api_route(_path, _legal_route(_name), methods=["GET"], include_in_schema=False)
