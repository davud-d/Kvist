# -*- coding: utf-8 -*-
"""Web search for Kvist on customers' machines, with the cloud's one shared Exa key.

The key never leaves this server: a task gets a search token of its machine (``task_environment``) and Kvist sends
its queries here. The token is an HMAC of the machine id, not the machine's own gateway token, so the agent running
in the VM, which can read its environment, cannot pose as the runner with it.

Each account may make ``PER_DAY`` searches in any 24 hours and ``PER_WEEK`` in any 7 days. A customer who added an
Exa, Tavily, ... key of their own in Kvist searches with it directly and never reaches this endpoint.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import app_http_client
from .config import Settings
from .db import lock_account, session_scope, utcnow
from .errors import ApiError
from .models import Machine, MachineStatus, SearchUse

log = logging.getLogger("kvist_cloud.search")
router = APIRouter()

PER_DAY = 25
PER_WEEK = 190
DAY = timedelta(days=1)
WEEK = timedelta(days=7)
EXA_URL = "https://api.exa.ai/search"
FRESHNESS_DAYS = {"day": 1, "week": 7, "month": 30}
MAX_RESULTS = 10


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
    return hmac.new(settings.session_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


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


def _used_since(db: Session, user_id: int, since: datetime) -> list[datetime]:
    query = select(SearchUse.created_at).where(SearchUse.user_id == user_id, SearchUse.created_at > since)
    return sorted(db.scalars(query))


def _quota_error(frees_at: datetime, limit: int, window: str) -> ApiError:
    return ApiError(
        429,
        "search_quota",
        f"The shared web search quota is used up ({limit} searches per {window}); the next search frees up at "
        f"{frees_at.strftime('%Y-%m-%d %H:%M')} UTC. A search key of your own (Exa, Tavily, Brave, ...) in Kvist's "
        f"/web-search-provider has no such limit.",
        frees_at=frees_at.isoformat(),
    )


def _server_cap_error(db: Session, now: datetime, cap: int) -> ApiError | None:
    """Everyone's searches in the last 24 hours against the server's cap (what the shared Exa balance can bear)."""
    # Counted without a server-wide lock: parallel searches of different accounts may pass the cap by a few,
    # which costs cents, while one lock would make every search on the server wait in a single line.
    latest = list(db.scalars(
        select(SearchUse.created_at).where(SearchUse.created_at > now - DAY)
        .order_by(SearchUse.created_at.desc()).limit(cap)
    ))
    if len(latest) < cap:
        return None
    frees_at = latest[-1] + DAY
    return ApiError(
        429,
        "search_quota",
        f"The shared web search of Kvist Cloud has reached its daily limit for all accounts together; it frees up "
        f"at {frees_at.strftime('%Y-%m-%d %H:%M')} UTC. A search key of your own (Exa, Tavily, Brave, ...) in "
        f"Kvist's /web-search-provider has no such limit.",
        frees_at=frees_at.isoformat(),
    )


def reserve(db: Session, user_id: int, now: datetime, server_cap: int) -> SearchUse:
    """Count one search against the account, or refuse with the moment the next one becomes possible."""
    lock_account(db, user_id)
    refused = _server_cap_error(db, now, server_cap)
    if refused is not None:
        raise refused
    week = _used_since(db, user_id, now - WEEK)
    if len(week) >= PER_WEEK:
        raise _quota_error(week[-PER_WEEK] + WEEK, PER_WEEK, "week")
    day = [moment for moment in week if moment > now - DAY]
    if len(day) >= PER_DAY:
        raise _quota_error(day[-PER_DAY] + DAY, PER_DAY, "day")
    use = SearchUse(user_id=user_id, created_at=now)
    db.add(use)
    db.flush()
    return use


def remaining(db: Session, user_id: int, now: datetime) -> dict[str, int]:
    week = _used_since(db, user_id, now - WEEK)
    day = [moment for moment in week if moment > now - DAY]
    return {"day": max(0, PER_DAY - len(day)), "week": max(0, PER_WEEK - len(week))}


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
    """A search that brought nothing back does not count."""
    with session_scope(app.state.session_factory) as db:
        use = db.get(SearchUse, use_id)
        if use is not None:
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
        use_id = reserve(db, user_id, now, settings.search_daily_cap).id
    try:
        results = await _ask_exa(app, _exa_payload(body, now))
    except ApiError:
        _release(app, use_id)
        raise
    with session_scope(app.state.session_factory) as db:
        left = remaining(db, user_id, now)
    return {"results": results, "remaining": left}
