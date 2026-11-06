# -*- coding: utf-8 -*-
"""Sign-in with GitHub OAuth; the session is a signed cookie.

Scopes are ``read:user repo`` so authorized private repositories can be cloned.
With ``AUTH_DEV_LOGIN=true`` and no GitHub app configured, the button signs in a local dev user
without a network call (loopback only,
enforced in config.py).
"""
from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import get_db
from .errors import ApiError, not_signed_in
from .models import User
from .validation import InputModel

SESSION_COOKIE = "kc_session"
OAUTH_COOKIE = "kc_oauth"
SESSION_MAX_AGE = 30 * 24 * 3600
OAUTH_MAX_AGE = 600
GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_USER = "https://api.github.com/user"
DEV_CODE = "dev-login"

router = APIRouter()


def _serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=salt)


def app_http_client(app) -> httpx.AsyncClient:
    """Outbound HTTP; tests swap the transport, so no test reaches GitHub, a model provider or Platega."""
    return httpx.AsyncClient(transport=app.state.http_transport, timeout=10.0)


def http_client(request: Request) -> httpx.AsyncClient:
    return app_http_client(request.app)


def safe_next(raw: str | None, default: str = "/start") -> str:
    """Only same-site paths: '/x', never '//evil' or 'https://evil'."""
    if not raw or not raw.startswith("/") or raw.startswith("//") or "\\" in raw or any(ord(c) < 32 for c in raw):
        return default
    return raw


def set_session(response: Response, settings: Settings, user: User) -> None:
    value = _serializer(settings, "session").dumps({"u": user.id, "v": user.session_version})
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


def user_from_cookie(db: Session, settings: Settings, value: str | None) -> User | None:
    if not value:
        return None
    try:
        data = _serializer(settings, "session").loads(value, max_age=SESSION_MAX_AGE)
    except BadSignature:
        return None
    user = db.get(User, data.get("u"))
    if user is None or user.session_version != data.get("v"):
        return None
    return user


def optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return user_from_cookie(db, request.app.state.settings, request.cookies.get(SESSION_COOKIE))


def current_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise not_signed_in()
    return user


def admin_user(request: Request, user: User = Depends(current_user)) -> User:
    if user.github_login.lower() not in request.app.state.settings.admin_logins:
        raise ApiError(403, "forbidden", "Admins only. / Только для администраторов.")
    return user


@router.get("/auth/github/login")
async def github_login(request: Request, next: str | None = None) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    state = secrets.token_urlsafe(24)
    target = safe_next(next)
    if settings.github_client_id:
        query = urlencode(
            {
                "client_id": settings.github_client_id,
                "redirect_uri": f"{settings.public_url}/auth/github/callback",
                "scope": "read:user repo",
                "state": state,
                "allow_signup": "true",
            }
        )
        response = RedirectResponse(f"{GITHUB_AUTHORIZE}?{query}", status_code=302)
    elif settings.auth_dev_login:
        response = RedirectResponse(
            f"/auth/github/callback?{urlencode({'code': DEV_CODE, 'state': state})}", status_code=302
        )
    else:
        raise ApiError(
            503, "github_not_configured", "GitHub sign-in is not configured. / Вход через GitHub не настроен."
        )
    response.set_cookie(
        OAUTH_COOKIE,
        _serializer(settings, "oauth").dumps({"s": state, "n": target}),
        max_age=OAUTH_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/auth",
    )
    return response


async def _github_profile(request: Request, code: str) -> tuple[dict, str]:
    settings: Settings = request.app.state.settings
    if not settings.github_client_id:
        if settings.auth_dev_login and code == DEV_CODE:
            return {"id": 1, "login": "dev", "name": "Dev User", "avatar_url": "", "email": ""}, ""
        raise ApiError(400, "oauth_failed", "Sign-in failed, try again. / Вход не удался, попробуй ещё раз.")
    async with http_client(request) as client:
        try:
            token_response = await client.post(
                GITHUB_TOKEN,
                data={
                    "client_id": settings.github_client_id,
                    "client_secret": settings.github_client_secret,
                    "code": code,
                    "redirect_uri": f"{settings.public_url}/auth/github/callback",
                },
                headers={"Accept": "application/json"},
            )
            token = token_response.json().get("access_token", "") if token_response.status_code == 200 else ""
            if not token:
                raise ApiError(400, "oauth_failed", "GitHub did not confirm the sign-in. / GitHub не подтвердил вход.")
            profile_response = await client.get(
                GITHUB_USER, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
            )
        except httpx.HTTPError as error:
            raise ApiError(502, "github_unreachable", "GitHub is not answering. / GitHub не отвечает.") from error
    if profile_response.status_code != 200:
        raise ApiError(400, "oauth_failed", "GitHub did not confirm the sign-in. / GitHub не подтвердил вход.")
    return profile_response.json(), token


def _upsert_user(db: Session, profile: dict, locale: str) -> User:
    github_id = int(profile["id"])
    user = db.scalars(select(User).where(User.github_id == github_id)).first()
    if user is None:
        user = User(github_id=github_id, locale=locale)
        db.add(user)
    user.github_login = str(profile.get("login") or "")
    user.name = str(profile.get("name") or "")
    user.avatar_url = str(profile.get("avatar_url") or "")
    user.email = str(profile.get("email") or "")
    raw_created = profile.get("created_at")
    try:
        created = datetime.fromisoformat(raw_created.replace("Z", "+00:00")) if raw_created else None
        user.github_created_at = created if created is not None and created.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        user.github_created_at = None
    db.flush()
    return user


def request_locale(request: Request) -> str:
    chosen = request.cookies.get("kc_lang", "")
    if chosen in {"en", "ru"}:
        return chosen
    return "ru" if request.headers.get("accept-language", "").lower().startswith("ru") else "en"


@router.get("/auth/github/callback")
async def github_callback(
    request: Request,
    code: str = "",
    state: str = "",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    try:
        saved = _serializer(settings, "oauth").loads(request.cookies.get(OAUTH_COOKIE, ""), max_age=OAUTH_MAX_AGE)
    except BadSignature as error:
        raise ApiError(400, "oauth_state", "The sign-in link expired, start again. / Ссылка входа устарела.") from error
    if not code or not secrets.compare_digest(str(saved.get("s", "")), state):
        raise ApiError(400, "oauth_state", "The sign-in link expired, start again. / Ссылка входа устарела.")
    profile, github_token = await _github_profile(request, code)
    user = _upsert_user(db, profile, request_locale(request))
    if github_token:
        # Encrypted at rest; used only for GitHub listing and authenticated clones.
        user.github_token_enc = request.app.state.vault.encrypt(github_token)
    hook: Callable[[Request, Session, User, str], Awaitable[None]] | None = request.app.state.on_signed_in
    if hook is not None:
        await hook(request, db, user, github_token)
    db.commit()
    response = RedirectResponse(safe_next(saved.get("n")), status_code=302)
    response.delete_cookie(OAUTH_COOKIE, path="/auth")
    set_session(response, settings, user)
    return response


@router.get("/api/me")
def me(request: Request, user: User = Depends(current_user)) -> dict[str, object]:
    return {
        "id": user.id,
        "login": user.github_login,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "locale": user.locale,
        "is_admin": user.github_login.lower() in request.app.state.settings.admin_logins,
    }


@router.get("/api/github/repos")
async def github_repos(request: Request, user: User = Depends(current_user)) -> dict[str, object]:
    """Repositories accessible to this OAuth token, including private ones."""
    if not user.github_token_enc:
        return {"repos": [], "reason": "no_token"}
    token = request.app.state.vault.decrypt(user.github_token_enc)
    async with http_client(request) as client:
        try:
            response = await client.get(
                "https://api.github.com/user/repos",
                params={"per_page": 30, "sort": "pushed", "visibility": "all"},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
        except httpx.HTTPError as error:
            raise ApiError(502, "github_unreachable", "GitHub is not answering. / GitHub не отвечает.") from error
    if response.status_code == 401:
        return {"repos": [], "reason": "token_expired"}
    if response.status_code != 200:
        raise ApiError(502, "github_unreachable", "GitHub is not answering. / GitHub не отвечает.")
    repos = [
        {"name": str(item.get("full_name", "")), "url": str(item.get("clone_url", "")),
         "description": str(item.get("description") or ""), "private": bool(item.get("private"))}
        for item in response.json()
        if isinstance(item, dict)
    ]
    return {"repos": repos, "reason": ""}


class MeIn(InputModel):
    locale: Literal["en", "ru"]


@router.patch("/api/me")
def update_me(
    payload: MeIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    user.locale = payload.locale
    db.commit()
    return me(request, user)


@router.post("/auth/logout")
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
