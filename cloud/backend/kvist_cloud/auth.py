# -*- coding: utf-8 -*-
"""Sign-in with GitHub OAuth; the session is a signed cookie.

Scopes are ``read:user repo`` so authorized private repositories can be cloned.
With ``AUTH_DEV_LOGIN=true`` and no GitHub app configured, the button signs in a local dev user
without a network call (loopback only,
enforced in config.py).
"""
from __future__ import annotations

import secrets
import zoneinfo
from collections.abc import Awaitable, Callable, Mapping
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
from .db import get_db, utcnow
from .errors import ApiError, not_signed_in
from .models import Notification, User
from .validation import InputModel

SESSION_COOKIE = "kc_session"
# The session of the cloud page framed inside an IDE panel (VS Code, JetBrains). A framed page is cross-site, where a
# Lax cookie is never sent, so this one is SameSite=None; Partitioned keeps it to frames under the IDE's own top-level
# page, and the API's X-Kvist-Client header and the WebSocket's Origin check still stop other sites from using it.
IDE_SESSION_COOKIE = "kc_ide_session"
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


def _ide_cookie(value: str, max_age: int) -> str:
    # Written by hand: Starlette writes Partitioned only on Python 3.14+. The value is URL-safe base64 and dots.
    # SameSite=None needs Secure; Chromium accepts a Secure cookie from http://localhost, so a local server works too.
    return (f"{IDE_SESSION_COOKIE}={value}; Max-Age={max_age}; Path=/; HttpOnly; Secure; SameSite=None; "
            "Partitioned")


def set_ide_session(response: Response, settings: Settings, user: User) -> None:
    value = _serializer(settings, "session").dumps({"u": user.id, "v": user.session_version})
    response.headers.append("set-cookie", _ide_cookie(value, SESSION_MAX_AGE))


def session_value(cookies: Mapping[str, str]) -> str | None:
    """The session a request carries: the site's own cookie, or the one a page framed in an IDE got."""
    return cookies.get(SESSION_COOKIE) or cookies.get(IDE_SESSION_COOKIE)


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
    return user_from_cookie(db, request.app.state.settings, session_value(request.cookies))


def signed_user(user: User | None = Depends(optional_user)) -> User:
    """Signed in, banned or not: the few calls a banned person still needs (who am I, sign out)."""
    if user is None:
        raise not_signed_in()
    return user


def is_owner(settings: Settings, user: User) -> bool:
    """Listed in ADMIN_GITHUB_LOGINS: cannot be demoted or banned from the panel."""
    return user.github_login.lower() in settings.admin_logins


def is_admin(settings: Settings, user: User) -> bool:
    return user.is_admin or is_owner(settings, user)


def ban_active(user: User, now: datetime | None = None) -> bool:
    if user.ban_permanent:
        return True
    return user.ban_until is not None and user.ban_until > (now or utcnow())


def ban_view(user: User) -> dict[str, object] | None:
    if not ban_active(user):
        return None
    return {"permanent": user.ban_permanent, "until": user.ban_until.isoformat() if user.ban_until else None,
            "reason": user.ban_reason}


def remember_timezone(db: Session, user: User, name: str | None) -> None:
    """Keep the zone the client reports; a name that is not a real IANA zone is ignored, not an error."""
    if not name or name == user.timezone or len(name) > 64:
        return
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return
    user.timezone = name
    db.commit()


def current_user(request: Request, user: User = Depends(signed_user), db: Session = Depends(get_db)) -> User:
    remember_timezone(db, user, request.headers.get("X-Time-Zone"))
    view = ban_view(user)
    if view is not None:
        raise ApiError(403, "banned", "This account is blocked. / Аккаунт заблокирован.", ban=view)
    if user.terms_accepted_at is None:
        raise ApiError(403, "terms_required",
                       "Accept the terms and the privacy policy first. / Сначала прими соглашение и политику.")
    return user


def admin_user(request: Request, user: User = Depends(current_user)) -> User:
    if not is_admin(request.app.state.settings, user):
        raise ApiError(403, "forbidden", "Admins only. / Только для администраторов.")
    return user


@router.get("/auth/github/login")
async def github_login(request: Request, next: str | None = None, link: bool = False,
                       user: User | None = Depends(optional_user)) -> RedirectResponse:
    """``link=1``: connect GitHub to the signed-in account (one made with Apple) instead of signing in with it."""
    settings: Settings = request.app.state.settings
    state = secrets.token_urlsafe(24)
    target = safe_next(next)
    saved: dict[str, object] = {"s": state, "n": target}
    if link:
        if user is None:
            raise not_signed_in()
        if user.github_id is not None:
            raise ApiError(409, "github_linked", "GitHub is already connected. / GitHub уже подключён.")
        saved["l"] = [user.id, user.session_version]
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
        _serializer(settings, "oauth").dumps(saved),
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


def _spent_trial(db: Session, github_id: int) -> datetime | None:
    """When an erased account of this GitHub user had taken the trial: a new account does not get another."""
    return db.scalar(select(User.trial_used_at).where(User.trial_github_id == github_id)
                     .order_by(User.trial_used_at).limit(1))


def cancel_pending_deletion(db: Session, user: User) -> None:
    """Back within the grace period (account.py): the deletion is off and the bell says so."""
    if user.delete_requested_at is None:
        return
    user.delete_requested_at = None
    db.add(Notification(user_id=user.id, kind="deletion_cancelled", params={}))


def _upsert_user(db: Session, profile: dict, locale: str) -> User:
    github_id = int(profile["id"])
    user = db.scalars(select(User).where(User.github_id == github_id)).first()
    if user is None:
        user = User(github_id=github_id, locale=locale, trial_used_at=_spent_trial(db, github_id))
        db.add(user)
    _apply_profile(user, profile)
    db.flush()
    cancel_pending_deletion(db, user)
    return user


def _link_github(db: Session, saved: dict, profile: dict) -> User:
    """Attaches a GitHub profile to an account made without one. Its age then decides the trial, as for anyone."""
    user_id, version = saved["l"]
    user = db.get(User, user_id)
    if user is None or user.session_version != version or user.github_id is not None:
        raise ApiError(400, "oauth_state", "The sign-in link expired, start again. / Ссылка входа устарела.")
    github_id = int(profile["id"])
    if db.scalars(select(User.id).where(User.github_id == github_id)).first() is not None:
        raise ApiError(409, "github_taken", "This GitHub account already has its own Kvist Cloud account: sign in "
                       "with it. / У этого GitHub уже есть свой аккаунт Kvist Cloud: войди через него.")
    user.github_id = github_id
    spent = _spent_trial(db, github_id)
    if spent is not None and user.trial_used_at is None:
        user.trial_used_at = spent
    name, email = user.name, user.email
    _apply_profile(user, profile)
    # What the person gave Apple stays when GitHub has nothing to say.
    user.name, user.email = user.name or name, user.email or email
    db.flush()
    return user


def _apply_profile(user: User, profile: dict) -> None:
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
    user = _link_github(db, saved, profile) if saved.get("l") else _upsert_user(db, profile, request_locale(request))
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
def me(request: Request, user: User = Depends(signed_user)) -> dict[str, object]:
    return {
        "id": user.id,
        "login": user.github_login,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "locale": user.locale,
        "is_admin": is_admin(request.app.state.settings, user),
        "is_owner": is_owner(request.app.state.settings, user),
        "ban": ban_view(user),
        "terms_accepted": user.terms_accepted_at is not None,
        # How the person signs in: an account made with Apple may have no GitHub yet (and so no trial).
        "github": user.github_id is not None,
        "apple": user.apple_sub is not None,
        # The field's rotating hints mark a year with Kvist from it.
        "created_at": user.created_at.isoformat(),
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


class TermsIn(InputModel):
    terms: bool
    privacy: bool


@router.post("/api/me/terms")
def accept_terms(
    payload: TermsIn, request: Request, user: User = Depends(signed_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    """Both boxes, ticked by the person: one without the other is not an acceptance."""
    if not (payload.terms and payload.privacy):
        raise ApiError(400, "terms_both", "Tick both boxes. / Отметь обе галочки.")
    if user.terms_accepted_at is None:
        user.terms_accepted_at = utcnow()
        db.commit()
    return me(request, user)


@router.post("/auth/logout")
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.headers.append("set-cookie", _ide_cookie('""', 0))
    return response
