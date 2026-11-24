# -*- coding: utf-8 -*-
"""Signing in from the phone apps.

GitHub's page (and Google's behind "sign in with Google") will not run inside an app's web view, so the apps open it in
the system browser's own sign-in session (ASWebAuthenticationSession on iOS, a Custom Tab on Android). That browser has
its own cookies: it finishes the usual GitHub sign-in there, and the app's web view still knows nothing. The handover:

1. the app opens ``/auth/app/start?nonce=N`` in the system browser (N is random, made by the app);
2. not signed in yet: on to GitHub and back to this same address; signed in: a short-lived signed code, bound to N, is
   handed to the app by redirecting to ``kvistcloud://signed-in?code=C``;
3. the app loads ``/auth/app/exchange?code=C&nonce=N`` in its own web view: the code must be valid, fresh and made for
   N, and then the web view gets the session cookie. An app that only saw the code (another app may register the same
   scheme) lacks N and gets nothing.
"""
from __future__ import annotations

import re
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature
from sqlalchemy.orm import Session

from .auth import _serializer, ban_active, optional_user, safe_next, set_session
from .config import Settings
from .db import get_db
from .errors import ApiError
from .models import User

router = APIRouter()
APP_SCHEME = "kvistcloud"
CODE_MAX_AGE = 120
NONCE = re.compile(r"[A-Za-z0-9_-]{32,128}")


def _nonce(raw: str) -> str:
    if not NONCE.fullmatch(raw):
        raise ApiError(400, "bad_nonce", "Not a sign-in request from the app. / Это не запрос входа из приложения.")
    return raw


@router.get("/auth/app/start")
def start(request: Request, nonce: str = "", user: User | None = Depends(optional_user)) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    nonce = _nonce(nonce)
    if user is None or ban_active(user):
        back = f"/auth/app/start?{urlencode({'nonce': nonce})}"
        return RedirectResponse(f"/auth/github/login?{urlencode({'next': back})}", status_code=302)
    code = _serializer(settings, "appcode").dumps({"u": user.id, "v": user.session_version, "n": nonce})
    return RedirectResponse(f"{APP_SCHEME}://signed-in?{urlencode({'code': code})}", status_code=302)


@router.get("/auth/app/exchange")
def exchange(request: Request, code: str = "", nonce: str = "", next: str = "/m/",
             db: Session = Depends(get_db)) -> Response:
    settings: Settings = request.app.state.settings
    nonce = _nonce(nonce)
    try:
        data = _serializer(settings, "appcode").loads(code, max_age=CODE_MAX_AGE)
    except BadSignature as error:
        raise ApiError(400, "bad_code", "The sign-in expired, start again. / Вход устарел, начни заново.") from error
    user = db.get(User, data.get("u"))
    if (user is None or user.session_version != data.get("v") or ban_active(user)
            or not secrets.compare_digest(str(data.get("n", "")), nonce)):
        raise ApiError(400, "bad_code", "The sign-in expired, start again. / Вход устарел, начни заново.")
    response = RedirectResponse(safe_next(next, "/m/"), status_code=302)
    set_session(response, settings, user)
    return response

