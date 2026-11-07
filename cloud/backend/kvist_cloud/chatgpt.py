# -*- coding: utf-8 -*-
"""Sign in with ChatGPT, on the server: the customer's ChatGPT plan pays for their model.

The customer is sent to OpenAI and comes back to ``/auth/chatgpt/callback`` on this server, so no port on
their computer is involved. The attempt behind that callback lives three minutes, is bound to the signed-in
customer and works once. The tokens are stored encrypted (they have to be sent back to OpenAI, so a hash
would be useless); only the attempt's ``state`` is kept as a hash.

A refresh token is replaced whenever it is used, so exactly one place renews it: ``fresh_access_token``. A
machine only ever gets the short-lived access token. Once a day ``check_forever`` renews every saved
sign-in, so one that OpenAI no longer accepts is marked ``needs_login`` before a task depends on it.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from kernel.providers.chatgpt_endpoints import (
    AGENT_CLIENT_ID,
    AUTHORIZE_ENDPOINT,
    ISSUER,
    PLAN_SCOPE,
    RESOURCE,
    SCOPES,
    TOKEN_ENDPOINT,
)
from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .auth import app_http_client, current_user, http_client, optional_user, safe_next
from .config import Settings
from .db import get_db, session_scope, utcnow
from .models import ChatGPTLogin, User
from .secrets import Vault, remember
from .validation import InputModel

log = logging.getLogger("kvist_cloud.chatgpt")

LOGIN_SECONDS = 180
# A token with less left than this is renewed before it is handed out.
RENEW_BEFORE_SECONDS = 600
FIRST_CHECK_SECONDS = 60.0
CONNECTED, NEEDS_LOGIN = "connected", "needs_login"
PROVIDER_ID = "chatgpt"

router = APIRouter()
_locks: dict[int, asyncio.Lock] = {}


class ChatGPTError(Exception):
    """``code``: denied, expired, refused (OpenAI said no: sign in again), unreachable (try later), no_plan,
    identity, not_connected, needs_login."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# -- what OpenAI sends back ------------------------------------------------------------------


def _claims(id_token: str) -> dict:
    """The identity token's claims, read without checking its signature.

    It came straight from OpenAI's token endpoint over TLS, which OpenID Connect Core (3.1.3.7) accepts in
    place of the signature, and it is only used to learn who signed in.
    """
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise ChatGPTError("identity") from None
    if not isinstance(claims, dict):
        raise ChatGPTError("identity")
    return claims


def _check_identity(claims: dict, client_id: str, nonce: str | None = None) -> None:
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    try:
        expired = float(claims.get("exp", 0)) < time.time()
    except (TypeError, ValueError):
        expired = True
    subject = claims.get("sub")
    anonymous = not isinstance(subject, str) or not subject
    if claims.get("iss") != ISSUER or client_id not in audiences or expired or anonymous:
        raise ChatGPTError("identity")
    if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ChatGPTError("identity")


def _record(tokens: dict, previous: dict) -> dict:
    scopes = str(tokens.get("scope", " ".join(previous.get("scopes", [])))).split()
    if PLAN_SCOPE not in scopes:
        raise ChatGPTError("no_plan")
    renewable = all(isinstance(tokens.get(key), str) and tokens[key] for key in ("access_token", "refresh_token"))
    try:
        lifetime = int(tokens["expires_in"])
    except (KeyError, TypeError, ValueError):
        lifetime = 0
    if not renewable or lifetime <= 0 or str(tokens.get("token_type", "")).lower() != "bearer":
        raise ChatGPTError("refused")
    return {
        **previous,
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "expires_at": time.time() + lifetime,
        "scopes": scopes,
    }


async def _token_request(client: httpx.AsyncClient, data: dict) -> dict:
    try:
        response = await client.post(TOKEN_ENDPOINT, data={**data, "resource": RESOURCE})
    except httpx.HTTPError:
        raise ChatGPTError("unreachable") from None
    if response.status_code >= 500 or response.status_code == 429:
        # OpenAI having trouble says nothing about whether the sign-in is still good.
        raise ChatGPTError("unreachable")
    try:
        value = response.json() if response.status_code == 200 else None
    except ValueError:
        value = None
    if not isinstance(value, dict):
        raise ChatGPTError("refused")
    return value


# -- storage ---------------------------------------------------------------------------------


def _load(vault: Vault, user: User) -> dict | None:
    if not user.chatgpt_enc:
        return None
    record = json.loads(vault.decrypt(user.chatgpt_enc))
    remember(record["access_token"])
    remember(record["refresh_token"])
    return record


def _save(vault: Vault, user: User, record: dict) -> None:
    user.chatgpt_enc = vault.encrypt(json.dumps(record))
    user.chatgpt_state = CONNECTED
    user.chatgpt_email = str(record.get("email", ""))
    user.chatgpt_checked_at = utcnow()


def summary(user: User) -> dict[str, object]:
    return {
        "state": user.chatgpt_state,
        "email": user.chatgpt_email,
        "checked_at": user.chatgpt_checked_at.isoformat() if user.chatgpt_checked_at else None,
    }


def _hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def _redirect_uri(settings: Settings) -> str:
    return f"{settings.public_url}/auth/chatgpt/callback"


# -- signing in ------------------------------------------------------------------------------


def begin_login(db: Session, vault: Vault, settings: Settings, user: User, redirect_to: str) -> str:
    """Opens an attempt and returns OpenAI's address for the customer's browser."""
    now = utcnow()
    db.execute(delete(ChatGPTLogin).where((ChatGPTLogin.user_id == user.id) | (ChatGPTLogin.expires_at < now)))
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    db.add(ChatGPTLogin(
        user_id=user.id, state_hash=_hash(state), verifier_enc=vault.encrypt(verifier), nonce=nonce,
        redirect_to=redirect_to, expires_at=now + timedelta(seconds=LOGIN_SECONDS),
    ))
    previous = _load(vault, user)
    db.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        # OpenAI answers a returning customer with the client it already made for them.
        "client_id": previous["client_id"] if previous else AGENT_CLIENT_ID,
        "ext_agent_host_id": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"kvist-cloud/user/{user.id}")),
        "response_type": "code", "redirect_uri": _redirect_uri(settings), "scope": SCOPES, "resource": RESOURCE,
        "state": state, "nonce": nonce, "code_challenge_method": "S256", "code_challenge": challenge,
        "agent_name_hint": "Kvist",
    }
    return AUTHORIZE_ENDPOINT + "?" + urlencode(params)


async def complete_login(
    db: Session, vault: Vault, client: httpx.AsyncClient, settings: Settings, user: User, params: dict[str, str]
) -> tuple[str, str]:
    """The callback's work. Returns where to send the customer and ``"ok"`` or the reason it did not work."""
    row = db.scalars(select(ChatGPTLogin).where(ChatGPTLogin.state_hash == _hash(params.get("state", "")))).first()
    if row is None or row.user_id != user.id or row.expires_at < utcnow():
        return "/onboarding", "expired"
    verifier, nonce, target = vault.decrypt(row.verifier_enc), row.nonce, row.redirect_to
    # Used once, whatever happens next.
    db.delete(row)
    db.commit()
    try:
        if params.get("error"):
            raise ChatGPTError("denied")
        code = params.get("code", "")
        previous = _load(vault, user) or {}
        client_id = params.get("client_id") or previous.get("client_id", "")
        if not code or not client_id or client_id == AGENT_CLIENT_ID:
            raise ChatGPTError("identity")
        tokens = await _token_request(client, {
            "grant_type": "authorization_code", "client_id": client_id, "code": code,
            "code_verifier": verifier, "redirect_uri": _redirect_uri(settings),
        })
        claims = _claims(str(tokens.get("id_token", "")))
        _check_identity(claims, client_id, nonce)
        who = {"client_id": client_id, "subject": claims["sub"], "email": str(claims.get("email", ""))}
        record = _record(tokens, who)
    except ChatGPTError as error:
        return target, error.code
    _save(vault, user, record)
    # One model per account, as with a key: the plan replaces whatever was chosen before.
    user.model_provider = PROVIDER_ID
    user.model_key_enc = user.model_key_last4 = user.model_base_url = user.model_name = ""
    db.commit()
    return target, "ok"


# -- keeping it good -------------------------------------------------------------------------


async def fresh_access_token(app: FastAPI, user_id: int, *, force: bool = False) -> str:
    return (await fresh_record(app, user_id, force=force))["access_token"]


async def lent_access(app: FastAPI, user_id: int) -> dict[str, object]:
    """What a machine is given: the short-lived access token and when it ends, never the refresh token."""
    record = await fresh_record(app, user_id)
    return {
        "access_token": record["access_token"], "expires_at": record["expires_at"],
        "scopes": record["scopes"], "email": record.get("email", ""),
    }


async def fresh_record(app: FastAPI, user_id: int, *, force: bool = False) -> dict:
    """The saved sign-in with an access token that has time left, renewed here when it is running out (or ``force``).

    The one place that uses the refresh token. Renewal runs outside any database transaction (it is a call to
    another company), under a per-customer lock; if another worker renewed in the meantime the new token is used.
    """
    async with _locks.setdefault(user_id, asyncio.Lock()):
        vault: Vault = app.state.vault
        with session_scope(app.state.session_factory) as db:
            user = db.get(User, user_id)
            if user is None or not user.chatgpt_enc:
                raise ChatGPTError("not_connected")
            if user.chatgpt_state != CONNECTED:
                raise ChatGPTError("needs_login")
            record = _load(vault, user)
        if not force and record["expires_at"] > time.time() + RENEW_BEFORE_SECONDS:
            return record
        try:
            async with app_http_client(app) as client:
                tokens = await _token_request(client, {
                    "grant_type": "refresh_token", "client_id": record["client_id"],
                    "refresh_token": record["refresh_token"],
                })
            if tokens.get("id_token"):
                claims = _claims(tokens["id_token"])
                _check_identity(claims, record["client_id"])
                if claims["sub"] != record["subject"]:
                    raise ChatGPTError("identity")
            renewed = _record(tokens, record)
        except ChatGPTError as error:
            if error.code == "unreachable":
                raise
            with session_scope(app.state.session_factory) as db:
                user = db.get(User, user_id)
                current = _load(vault, user)
                if current["refresh_token"] != record["refresh_token"]:
                    return current
                user.chatgpt_state = NEEDS_LOGIN
                user.chatgpt_checked_at = utcnow()
            raise
        with session_scope(app.state.session_factory) as db:
            _save(vault, db.get(User, user_id), renewed)
        return renewed


async def check_all(app: FastAPI) -> None:
    """Uses every saved sign-in once, so an expired one is found now and not in the middle of a task."""
    with session_scope(app.state.session_factory) as db:
        ids = list(db.scalars(select(User.id).where(User.chatgpt_state == CONNECTED)))
    for user_id in ids:
        try:
            await fresh_access_token(app, user_id, force=True)
        except ChatGPTError as error:
            log.warning("ChatGPT sign-in of user %s: %s", user_id, error.code)


async def check_forever(app: FastAPI) -> None:
    delay = FIRST_CHECK_SECONDS
    while True:
        await asyncio.sleep(delay)
        delay = app.state.settings.chatgpt_check_seconds
        try:
            await check_all(app)
        except Exception:  # noqa: BLE001 - the loop must survive one bad pass; the error is logged
            log.exception("ChatGPT sign-in check failed")


async def _revoke(client: httpx.AsyncClient, record: dict) -> bool:
    """Tells OpenAI the refresh token is no longer wanted. False when that could not be confirmed."""
    try:
        discovery = await client.get(ISSUER + "/.well-known/openid-configuration")
        endpoint = discovery.json()["revocation_endpoint"]
        if urlsplit(endpoint).scheme != "https" or urlsplit(endpoint).netloc != urlsplit(ISSUER).netloc:
            return False
        response = await client.post(endpoint, data={
            "token": record["refresh_token"], "token_type_hint": "refresh_token", "client_id": record["client_id"],
        })
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return False
    return response.status_code == 200


# -- API -------------------------------------------------------------------------------------


class StartIn(InputModel):
    next: str = Field(default="/onboarding", max_length=200)


@router.post("/api/chatgpt/start")
def start(
    payload: StartIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, str]:
    url = begin_login(
        db, request.app.state.vault, request.app.state.settings, user, safe_next(payload.next, "/onboarding")
    )
    return {"url": url}


@router.get("/auth/chatgpt/callback")
async def callback(
    request: Request, user: User | None = Depends(optional_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    if user is None:
        return RedirectResponse("/", status_code=302)
    async with http_client(request) as client:
        target, result = await complete_login(
            db, request.app.state.vault, client, request.app.state.settings, user, dict(request.query_params)
        )
    separator = "&" if "?" in target else "?"
    response = RedirectResponse(f"{target}{separator}chatgpt={result}", status_code=302)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/chatgpt")
def status(user: User = Depends(current_user)) -> dict[str, object]:
    return summary(user)


@router.delete("/api/chatgpt")
async def disconnect(
    request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    record = _load(request.app.state.vault, user)
    revoked = False
    if record:
        async with http_client(request) as client:
            revoked = await _revoke(client, record)
    user.chatgpt_enc = ""
    user.chatgpt_state = ""
    user.chatgpt_email = ""
    user.chatgpt_checked_at = None
    if user.model_provider == PROVIDER_ID:
        user.model_provider = ""
    db.commit()
    return {**summary(user), "revoked": revoked}
