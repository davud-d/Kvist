# -*- coding: utf-8 -*-
"""A short-lived link for a reviewer (a payment provider's check) into a ready test account on the Start plan.

``python -m kvist_cloud.review [--minutes 2] [--env-file PATH]`` prints the link. The link is signed with the
server's session secret, so only someone holding the server's settings can make one, and it stops working by itself.
It is not single-use on purpose: chat apps open a link once to draw a preview, which would otherwise spend it.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import billing, machines
from .auth import request_locale, set_session
from .config import ConfigError, Settings, load_settings, read_env_file
from .db import get_db, lock_account, utcnow
from .errors import ApiError
from .models import User

REVIEW_GITHUB_ID = -1  # never a real GitHub id
REVIEW_LOGIN = "review-account"
REVIEW_PLAN = "start"
REVIEW_PLAN_DAYS = 1
DEFAULT_MINUTES = 2

router = APIRouter()


def _serializer(settings: Settings) -> URLSafeSerializer:
    return URLSafeSerializer(settings.session_secret, salt="review-link")


def make_link(settings: Settings, minutes: float) -> str:
    token = _serializer(settings).dumps({"exp": time.time() + minutes * 60})
    return f"{settings.public_url}/auth/review?{urlencode({'t': token})}"


def _expired() -> ApiError:
    return ApiError(410, "review_link_expired", "This link has expired. / Ссылка устарела.")


def _review_user(db: Session, locale: str) -> User:
    user = db.scalars(select(User).where(User.github_id == REVIEW_GITHUB_ID)).first()
    if user is None:
        user = User(github_id=REVIEW_GITHUB_ID, github_login=REVIEW_LOGIN, name="Review account", locale=locale)
        db.add(user)
        db.flush()
    return user


@router.get("/auth/review", include_in_schema=False)
def review_login(request: Request, background: BackgroundTasks, t: str = "",
                 db: Session = Depends(get_db)) -> RedirectResponse:
    settings: Settings = request.app.state.settings
    try:
        payload = _serializer(settings).loads(t)
        expires = float(payload["exp"])
    except (BadSignature, KeyError, TypeError, ValueError) as error:
        raise _expired() from error
    if time.time() > expires:
        raise _expired()
    user = _review_user(db, request_locale(request))
    lock_account(db, user.id)
    billing.admin_grant_plan(db, user.id, REVIEW_PLAN, REVIEW_PLAN_DAYS, utcnow())
    db.commit()
    background.add_task(machines.ensure_machine_for, request.app, user.id)
    response = RedirectResponse("/dashboard", status_code=302)
    set_session(response, settings, user)
    return response


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=float, default=DEFAULT_MINUTES, help="how long the link works")
    parser.add_argument("--env-file", help="the server's env file (default: KVIST_CLOUD_ENV_FILE or cloud/.env)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings({**read_env_file(Path(args.env_file)), **os.environ} if args.env_file else None)
    except ConfigError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(make_link(settings, args.minutes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
