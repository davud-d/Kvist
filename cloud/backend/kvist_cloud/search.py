# -*- coding: utf-8 -*-
"""Web search for Kvist on customers' machines, with the cloud's one shared Exa key.

The key never leaves this server: a task gets a search token of its machine (``task_environment``) and Kvist sends
its queries here. The token is an HMAC of the machine id, not the machine's own gateway token, so the agent running
in the VM, which can read its environment, cannot pose as the runner with it.

Each account may make ``PER_DAY`` free searches in any 24 hours and ``PER_WEEK`` in any 7 days; past that it spends
searches it bought (``plans.SEARCH_PACK_SEARCHES`` a pack, billing.py). All accounts together stop at the server's
``search_daily_cap``, which no purchase lifts: it is what the shared Exa balance can bear. A customer who added an
Exa, Tavily, ... key of their own in Kvist searches with it directly and never reaches this endpoint.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import plans
from .auth import app_http_client, current_user
from .config import Settings
from .db import get_db, lock_account, session_scope, utcnow
from .errors import ApiError
from .models import Machine, MachineStatus, Notification, SearchUse, User
from .notifications import notify

log = logging.getLogger("kvist_cloud.search")
router = APIRouter()

PER_DAY = 25
PER_WEEK = 190
DAY = timedelta(days=1)
WEEK = timedelta(days=7)
EXA_URL = "https://api.exa.ai/search"
FRESHNESS_DAYS = {"day": 1, "week": 7, "month": 30}
MAX_RESULTS = 10
QUOTA_NOTICE = "search_quota"
OWN_KEY_HINT = "A search key of the user's own (Exa, Tavily, Brave, ...) in Kvist's /web-search-provider has no limit."


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=MAX_RESULTS)
    freshness: str = "any"


def search_token(settings: Settings, machine_id: int) -> str:
    return f"{machine_id}.{_signature(settings, machine_id)}"


def task_environment(settings: Settings, machine_id: int) -> dict[str, str]:
    """What Kvist needs to search through this server; nothing when no shared key is set."""
    if not settings.exa_api_key:
        return {}
    return {
        "KVIST_CLOUD_SEARCH_URL": f"{settings.public_url}/gateway/search",
        # Named *_API_KEY so the runner scrubs it from everything the task prints.
        "KVIST_CLOUD_SEARCH_API_KEY": search_token(settings, machine_id),
    }


def _signature(settings: Settings, machine_id: int) -> str:
    message = f"kvist-search:{machine_id}".encode()
    return hmac.new(settings.session_secret.encode(), message, hashlib.sha256).hexdigest()


def _user_for_token(db: Session, settings: Settings, token: str) -> int | None:
    machine_part, _, signature = token.partition(".")
    if not machine_part.isdigit() or not signature:
        return None
    machine_id = int(machine_part)
    if not hmac.compare_digest(signature, _signature(settings, machine_id)):
        return None
    machine = db.get(Machine, machine_id)
    if machine is None or machine.status == MachineStatus.DELETED:
        return None
    return machine.user_id


def _free_used_since(db: Session, user_id: int, since: datetime) -> list[datetime]:
    query = select(SearchUse.created_at).where(
        SearchUse.user_id == user_id, SearchUse.created_at > since, SearchUse.paid.is_(False))
    return sorted(db.scalars(query))


def _free_frees_at(db: Session, user_id: int, now: datetime) -> datetime | None:
    """None while a free search is left; otherwise the moment the next one is."""
    week = _free_used_since(db, user_id, now - WEEK)
    day = [moment for moment in week if moment > now - DAY]
    moments = []
    if len(week) >= PER_WEEK:
        moments.append(week[-PER_WEEK] + WEEK)
    if len(day) >= PER_DAY:
        moments.append(day[-PER_DAY] + DAY)
    return max(moments) if moments else None


def _server_frees_at(db: Session, now: datetime, cap: int) -> datetime | None:
    """Everyone's searches in the last 24 hours against the server's cap; None while it is not reached."""
    # Counted without a server-wide lock: parallel searches of different accounts may pass the cap by a few,
    # which costs cents, while one lock would make every search on the server wait in a single line.
    latest = list(db.scalars(
        select(SearchUse.created_at).where(SearchUse.created_at > now - DAY)
        .order_by(SearchUse.created_at.desc()).limit(cap)
    ))
    return latest[-1] + DAY if len(latest) >= cap else None


def _at(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def _server_cap_error(frees_at: datetime) -> ApiError:
    return ApiError(
        429, QUOTA_NOTICE,
        f"Kvist Cloud's shared web search has reached its daily limit for all accounts together; it frees up at "
        f"{_at(frees_at)} UTC. {OWN_KEY_HINT}",
        frees_at=frees_at.isoformat(),
    )


def _quota_error(settings: Settings, frees_at: datetime) -> ApiError:
    return ApiError(
        429, QUOTA_NOTICE,
        f"The free web searches of this account are used up ({PER_DAY} a day, {PER_WEEK} a week); the next one "
        f"frees up at {_at(frees_at)} UTC. The user can buy {plans.SEARCH_PACK_SEARCHES} more searches for "
        f"${plans.SEARCH_PACK_PRICE_USD:.2f} at {settings.public_url}/dashboard (they do not expire). {OWN_KEY_HINT}",
        frees_at=frees_at.isoformat(),
        buy_url=f"{settings.public_url}/dashboard",
    )


def _notify_once_a_day(db: Session, user_id: int, now: datetime, frees_at: datetime) -> None:
    """The bell's "searches ran out, buy more", not once per refused search of a busy agent."""
    recent = db.scalar(select(Notification.id).where(
        Notification.user_id == user_id, Notification.kind == QUOTA_NOTICE, Notification.created_at > now - DAY))
    if recent is None:
        notify(db, user_id, QUOTA_NOTICE, until=frees_at.isoformat())


def reserve(db: Session, settings: Settings, user_id: int, now: datetime) -> SearchUse | ApiError:
    """Count one search: free first, then a bought one. Otherwise the refusal, returned so the bell's notice that
    goes with it is committed rather than rolled back with a raised error."""
    lock_account(db, user_id)
    server_frees_at = _server_frees_at(db, now, settings.search_daily_cap)
    if server_frees_at is not None:
        return _server_cap_error(server_frees_at)
    frees_at = _free_frees_at(db, user_id, now)
    paid = frees_at is not None
    if paid:
        user = db.get(User, user_id)
        if user.search_credits <= 0:
            _notify_once_a_day(db, user_id, now, frees_at)
            return _quota_error(settings, frees_at)
        user.search_credits -= 1
    use = SearchUse(user_id=user_id, created_at=now, paid=paid)
    db.add(use)
    db.flush()
    return use


def usage(db: Session, user_id: int, now: datetime) -> dict[str, int]:
    week = _free_used_since(db, user_id, now - WEEK)
    day = [moment for moment in week if moment > now - DAY]
    return {"day": max(0, PER_DAY - len(day)), "week": max(0, PER_WEEK - len(week)),
            "bought": db.get(User, user_id).search_credits}


def grant(db: Session, user_id: int, searches: int) -> None:
    lock_account(db, user_id)
    db.get(User, user_id).search_credits += searches


def take_back(db: Session, user_id: int, searches: int) -> None:
    """A refunded pack: what is left of it goes; searches already made with it are not owed back."""
    lock_account(db, user_id)
    user = db.get(User, user_id)
    user.search_credits = max(0, user.search_credits - searches)


def _exa_payload(body: SearchRequest, now: datetime) -> dict:
    payload: dict = {"query": body.query, "numResults": body.top_k, "contents": {"highlights": True}}
    days = FRESHNESS_DAYS.get(body.freshness)
    if days is not None:
        since = now - timedelta(days=days)
        payload["startPublishedDate"] = since.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return payload


def _results(raw: object) -> list[dict[str, str]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
        raise ApiError(502, "search_failed", "Exa returned an answer in an unknown format.")
    mapped = []
    for item in raw["results"]:
        if not isinstance(item, dict):
            continue
        highlights = item.get("highlights")
        snippet = " ".join(str(part) for part in highlights) if isinstance(highlights, list) else ""
        mapped.append({
            "title": str(item.get("title") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "snippet": snippet.strip(),
        })
    return mapped


async def _ask_exa(app: FastAPI, payload: dict) -> list[dict[str, str]]:
    headers = {"x-api-key": app.state.settings.exa_api_key, "Accept": "application/json"}
    try:
        async with app_http_client(app) as client:
            response = await client.post(EXA_URL, json=payload, headers=headers, timeout=20.0)
    except httpx.HTTPError as error:
        log.warning("Exa did not answer: %s", type(error).__name__)
        raise ApiError(502, "search_failed", "The search service did not answer; try again.") from error
    if response.status_code != 200:
        log.warning("Exa answered %s", response.status_code)
        raise ApiError(502, "search_failed", f"The search service answered {response.status_code}; try again.")
    try:
        return _results(response.json())
    except ValueError as error:
        raise ApiError(502, "search_failed", "Exa returned an answer that is not JSON.") from error


def _release(app: FastAPI, use_id: int) -> None:
    """A search that brought nothing back does not count, and a bought one is given back."""
    with session_scope(app.state.session_factory) as db:
        use = db.get(SearchUse, use_id)
        if use is None:
            return
        if use.paid:
            grant(db, use.user_id, 1)
        db.delete(use)


@router.post("/gateway/search")
async def search(body: SearchRequest, request: Request) -> dict:
    app = request.app
    settings: Settings = app.state.settings
    if not settings.exa_api_key:
        raise ApiError(503, "search_off", "Shared web search is not set up on this server.")
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    now = utcnow()
    # Counted before Exa is asked and in a transaction of its own, so parallel searches cannot pass the limit.
    with session_scope(app.state.session_factory) as db:
        user_id = _user_for_token(db, settings, token)
        if user_id is None:
            raise ApiError(403, "forbidden", "Not allowed.")
        reserved = reserve(db, settings, user_id, now)
        use_id = reserved.id if isinstance(reserved, SearchUse) else None
    if isinstance(reserved, ApiError):
        raise reserved
    try:
        results = await _ask_exa(app, _exa_payload(body, now))
    except ApiError:
        _release(app, use_id)
        raise
    with session_scope(app.state.session_factory) as db:
        left = usage(db, user_id, now)
    return {"results": results, "remaining": left}


@router.get("/api/search/usage")
def my_usage(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """The dashboard's search card: free searches left, bought ones, and the pack on sale."""
    return {
        "enabled": bool(request.app.state.settings.exa_api_key),
        "per_day": PER_DAY,
        "per_week": PER_WEEK,
        **usage(db, user.id, utcnow()),
        "pack": {"searches": plans.SEARCH_PACK_SEARCHES, "price_usd": plans.SEARCH_PACK_PRICE_USD},
    }
