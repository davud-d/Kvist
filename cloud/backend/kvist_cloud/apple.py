# -*- coding: utf-8 -*-
"""Sign in with Apple, behind APPLE_SIGNIN_ENABLED.

Two ways in, one account per Apple user (the token's ``sub``):

- the iOS app's own sheet (App Review Guideline 4.8 asks for it next to GitHub): the page asks the server for a nonce,
  the app shows Apple's sheet with its hash and hands back the identity token and authorization code, and the page
  posts them to ``/api/auth/apple`` (an /api/ path, so a cross-site form cannot sign a browser in);
- the web (``/auth/apple/login``), when a Services ID is configured: Apple's page posts the result back to
  ``/auth/apple/callback``. That post is cross-site, so the state cookie is SameSite=None.

Both check the identity token against Apple's published keys, then trade the authorization code for a refresh token,
kept encrypted, so that deleting the account can revoke it (Apple requires that, account.py calls ``revoke``).

An account made with Apple alone has no GitHub profile, hence no trial (billing.trial_eligible) until a GitHub account
older than a month is connected (auth.py, ``link``).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import parse_qsl, urlencode

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from itsdangerous import BadSignature
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import _serializer, app_http_client, cancel_pending_deletion, request_locale, safe_next, set_session
from .config import AppleSettings, Settings
from .db import get_db
from .errors import ApiError
from .models import User
from .validation import InputModel

log = logging.getLogger("kvist_cloud.apple")
router = APIRouter()

ISSUER = "https://appleid.apple.com"
KEYS_URL = f"{ISSUER}/auth/keys"
AUTHORIZE_URL = f"{ISSUER}/auth/authorize"
TOKEN_URL = f"{ISSUER}/auth/token"
REVOKE_URL = f"{ISSUER}/auth/revoke"
NONCE_MAX_AGE = 600
STATE_COOKIE = "kc_apple"
KEYS_TTL = 3600
LEEWAY = 60


class AppleError(Exception):
    """The token is not one Apple made for us, now."""


def _failed() -> ApiError:
    return ApiError(400, "apple_failed", "Apple did not confirm the sign-in. / Apple не подтвердил вход.")


def _settings(request: Request) -> AppleSettings:
    apple = request.app.state.settings.apple
    if apple is None:
        raise ApiError(503, "apple_not_configured",
                       "Sign in with Apple is not configured. / Вход через Apple не настроен.")
    return apple


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# -- Apple's keys and its tokens --------------------------------------------------------------------------------------

async def _keys(app: FastAPI, refresh: bool) -> dict[str, dict]:
    cached = getattr(app.state, "apple_keys", None)
    if cached and not refresh and time.time() - cached[0] < KEYS_TTL:
        return cached[1]
    async with app_http_client(app) as client:
        try:
            response = await client.get(KEYS_URL)
        except httpx.HTTPError as error:
            raise ApiError(502, "apple_unreachable", "Apple is not answering. / Apple не отвечает.") from error
    if response.status_code != 200:
        raise ApiError(502, "apple_unreachable", "Apple is not answering. / Apple не отвечает.")
    keys = {key["kid"]: key for key in response.json().get("keys", []) if key.get("kty") == "RSA"}
    app.state.apple_keys = (time.time(), keys)
    return keys


def _rsa_key(jwk: dict) -> rsa.RSAPublicKey:
    numbers = rsa.RSAPublicNumbers(int.from_bytes(_b64(jwk["e"]), "big"), int.from_bytes(_b64(jwk["n"]), "big"))
    return numbers.public_key()


def check_claims(claims: dict, audiences: set[str], nonce: str, now: float) -> None:
    if claims.get("iss") != ISSUER:
        raise AppleError("issuer")
    if claims.get("aud") not in audiences:
        raise AppleError("audience")
    if not isinstance(claims.get("exp"), int | float) or claims["exp"] < now - LEEWAY:
        raise AppleError("expired")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise AppleError("subject")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise AppleError("nonce")


async def verify(app: FastAPI, token: str, audiences: set[str], nonce: str) -> dict:
    """The claims of an identity token signed by Apple for one of ``audiences`` and this nonce."""
    try:
        head_b64, body_b64, signature_b64 = token.split(".")
        header = json.loads(_b64(head_b64))
        claims = json.loads(_b64(body_b64))
        signature = _b64(signature_b64)
    except (ValueError, TypeError) as error:
        raise AppleError("shape") from error
    if header.get("alg") != "RS256" or not isinstance(claims, dict):
        raise AppleError("algorithm")
    keys = await _keys(app, refresh=False)
    if header.get("kid") not in keys:
        # Apple rotates its keys: one we have not seen yet is worth one fresh look.
        keys = await _keys(app, refresh=True)
    jwk = keys.get(header.get("kid"))
    if jwk is None:
        raise AppleError("key")
    try:
        _rsa_key(jwk).verify(signature, f"{head_b64}.{body_b64}".encode("ascii"), padding.PKCS1v15(),
                             hashes.SHA256())
    except InvalidSignature as error:
        raise AppleError("signature") from error
    check_claims(claims, audiences, nonce, time.time())
    return claims


def client_secret(apple: AppleSettings, client_id: str, now: float | None = None) -> str:
    """The ES256 JWT Apple takes as the client secret, signed with the developer account's key."""
    now = int(now or time.time())
    header = {"alg": "ES256", "kid": apple.key_id}
    payload = {"iss": apple.team_id, "iat": now, "exp": now + 300, "aud": ISSUER, "sub": client_id}
    signing_input = f"{_b64e(json.dumps(header).encode())}.{_b64e(json.dumps(payload).encode())}"
    key = serialization.load_pem_private_key(apple.private_key.encode("utf-8"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ApiError(500, "apple_key", "The Apple key is not an EC key. / Ключ Apple не EC.")
    r, s = decode_dss_signature(key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    return f"{signing_input}.{_b64e(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


async def _refresh_token(app: FastAPI, apple: AppleSettings, code: str, client_id: str,
                         redirect_uri: str | None) -> str:
    """The refresh token for an authorization code; "" when Apple will not give one (the sign-in still stands)."""
    form = {"client_id": client_id, "client_secret": client_secret(apple, client_id), "code": code,
            "grant_type": "authorization_code", **({"redirect_uri": redirect_uri} if redirect_uri else {})}
    async with app_http_client(app) as client:
        try:
            response = await client.post(TOKEN_URL, data=form)
        except httpx.HTTPError as error:
            log.warning("Apple's token endpoint is unreachable: %s", error)
            return ""
    if response.status_code != 200:
        log.warning("Apple refused the authorization code: %s", response.status_code)
        return ""
    return str(response.json().get("refresh_token", ""))


async def revoke(app: FastAPI, sealed: str) -> None:
    """Revokes a stored refresh token (account deletion). Failures are logged: the deletion goes on regardless."""
    apple = app.state.settings.apple
    if apple is None or not sealed:
        return
    stored = json.loads(app.state.vault.decrypt(sealed))
    form = {"client_id": stored["c"], "client_secret": client_secret(apple, stored["c"]), "token": stored["t"],
            "token_type_hint": "refresh_token"}
    async with app_http_client(app) as client:
        try:
            response = await client.post(REVOKE_URL, data=form)
        except httpx.HTTPError as error:
            log.error("Apple token not revoked, Apple unreachable: %s", error)
            return
    if response.status_code != 200:
        log.error("Apple token not revoked: %s", response.status_code)


# -- the account ----------------------------------------------------------------------------------------------------

def _upsert(request: Request, db: Session, claims: dict, name: str) -> User:
    user = db.scalars(select(User).where(User.apple_sub == claims["sub"])).first()
    if user is None:
        # Apple tells the name once, on the first sign-in; the email may be a private relay address.
        user = User(apple_sub=claims["sub"], github_login="", name=name.strip()[:200],
                    email=str(claims.get("email") or "")[:320], locale=request_locale(request))
        db.add(user)
        db.flush()
    cancel_pending_deletion(db, user)
    return user


async def _signed_in(request: Request, db: Session, user: User, apple: AppleSettings, code: str, client_id: str,
                     redirect_uri: str | None) -> None:
    token = await _refresh_token(request.app, apple, code, client_id, redirect_uri) if code else ""
    if token:
        user.apple_token_enc = request.app.state.vault.encrypt(json.dumps({"c": client_id, "t": token}))
    hook = request.app.state.on_signed_in
    if hook is not None:
        await hook(request, db, user, "")
    db.commit()


# -- the iOS app's sheet ----------------------------------------------------------------------------------------------

@router.get("/api/auth/config")
def auth_config(request: Request) -> dict[str, bool]:
    """Which ways to sign in the page should offer, before anyone is signed in."""
    apple = request.app.state.settings.apple
    return {"apple_app": apple is not None, "apple_web": apple is not None and bool(apple.services_id)}


@router.post("/api/auth/apple/nonce")
def native_nonce(request: Request) -> dict[str, str]:
    """A signed, short-lived nonce: the app puts its SHA-256 into Apple's request, so a token cannot be replayed."""
    _settings(request)
    raw = secrets.token_urlsafe(24)
    return {"nonce": _serializer(request.app.state.settings, "apple-nonce").dumps(raw)}


class NativeIn(InputModel):
    identity_token: str = Field(min_length=20, max_length=8000)
    authorization_code: str = Field(default="", max_length=2000)
    nonce: str = Field(min_length=10, max_length=300)
    name: str = Field(default="", max_length=200)


@router.post("/api/auth/apple")
async def native_sign_in(payload: NativeIn, request: Request, db: Session = Depends(get_db)) -> Response:
    apple = _settings(request)
    settings: Settings = request.app.state.settings
    try:
        _serializer(settings, "apple-nonce").loads(payload.nonce, max_age=NONCE_MAX_AGE)
        claims = await verify(request.app, payload.identity_token, {apple.bundle_id},
                              hashlib.sha256(payload.nonce.encode("utf-8")).hexdigest())
    except (BadSignature, AppleError) as error:
        log.info("Apple sign-in refused: %s", error)
        raise _failed() from error
    user = _upsert(request, db, claims, payload.name)
    await _signed_in(request, db, user, apple, payload.authorization_code, apple.bundle_id, None)
    response = JSONResponse({"ok": True})
    set_session(response, settings, user)
    return response


# -- the web ----------------------------------------------------------------------------------------------------------

def _callback_url(settings: Settings) -> str:
    return f"{settings.public_url}/auth/apple/callback"


@router.get("/auth/apple/login")
def web_login(request: Request, next: str | None = None) -> RedirectResponse:
    apple = _settings(request)
    if not apple.services_id:
        raise ApiError(503, "apple_not_configured",
                       "Sign in with Apple is not configured. / Вход через Apple не настроен.")
    settings: Settings = request.app.state.settings
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    query = urlencode({"client_id": apple.services_id, "redirect_uri": _callback_url(settings),
                       "response_type": "code id_token", "response_mode": "form_post", "scope": "name email",
                       "state": state, "nonce": nonce})
    response = RedirectResponse(f"{AUTHORIZE_URL}?{query}", status_code=302)
    # Apple posts back from its own site: a Lax cookie would not come with that post.
    response.set_cookie(STATE_COOKIE, _serializer(settings, "apple-state").dumps(
        {"s": state, "n": nonce, "next": safe_next(next)}), max_age=NONCE_MAX_AGE, httponly=True,
        samesite="none", secure=True, path="/auth/apple")
    return response


@router.post("/auth/apple/callback")
async def web_callback(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    apple = _settings(request)
    settings: Settings = request.app.state.settings
    form = dict(parse_qsl((await request.body()).decode("utf-8", "replace")))
    try:
        saved = _serializer(settings, "apple-state").loads(request.cookies.get(STATE_COOKIE, ""),
                                                            max_age=NONCE_MAX_AGE)
        if not secrets.compare_digest(str(saved.get("s", "")), form.get("state", "")):
            raise AppleError("state")
        claims = await verify(request.app, form.get("id_token", ""), {apple.services_id}, str(saved["n"]))
    except (BadSignature, AppleError) as error:
        log.info("Apple web sign-in refused: %s", error)
        raise ApiError(400, "oauth_state", "The sign-in link expired, start again. / Ссылка входа устарела.") \
            from error
    try:
        name_parts = json.loads(form.get("user", "") or "{}").get("name") or {}
    except ValueError:
        name_parts = {}
    name = " ".join(str(name_parts.get(part, "")) for part in ("firstName", "lastName")).strip()
    user = _upsert(request, db, claims, name)
    await _signed_in(request, db, user, apple, form.get("code", ""), apple.services_id, _callback_url(settings))
    response = RedirectResponse(safe_next(saved.get("next")), status_code=303)
    response.delete_cookie(STATE_COOKIE, path="/auth/apple", samesite="none", secure=True)
    set_session(response, settings, user)
    return response
