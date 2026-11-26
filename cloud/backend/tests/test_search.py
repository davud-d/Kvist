# -*- coding: utf-8 -*-
"""The shared web search: the machine's search token, the account's quota, the key that never leaves the server."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
from conftest import make_machine, make_user

from kvist_cloud import search
from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.models import SearchUse

EXA_KEY = "exa-test-key-1234567890"


def _exa(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.exa.ai"
        seen.append({"key": request.headers["x-api-key"], "body": json.loads(request.content)})
        found = {"title": "T", "url": "https://example.org/", "highlights": ["a", "b"]}
        return httpx.Response(200, json={"results": [found]})
    return handler


def _account(client) -> tuple[int, str]:
    with session_scope(client.app.state.session_factory) as db:
        user = make_user(db)
        machine = make_machine(db, user)
        return user.id, search.search_token(client.app.state.settings, machine.id)


def _used(client, user_id: int, ages: list[timedelta]) -> None:
    now = utcnow()
    with session_scope(client.app.state.session_factory) as db:
        db.add_all(SearchUse(user_id=user_id, created_at=now - age) for age in ages)


def _search(client, token: str, **body):
    return client.post("/gateway/search", json={"query": "kvist", **body}, headers={"Authorization": f"Bearer {token}"})


def test_a_search_goes_to_exa_with_the_servers_key_and_counts(client_factory):
    seen: list = []
    client = client_factory(_exa(seen), EXA_API_KEY=EXA_KEY)
    _, token = _account(client)

    answer = _search(client, token, top_k=3, freshness="week")

    assert answer.status_code == 200
    assert answer.json()["results"] == [{"title": "T", "url": "https://example.org/", "snippet": "a b"}]
    assert answer.json()["remaining"] == {"day": search.PER_DAY - 1, "week": search.PER_WEEK - 1}
    assert seen[0]["key"] == EXA_KEY and seen[0]["body"]["numResults"] == 3
    assert "startPublishedDate" in seen[0]["body"]
    assert EXA_KEY not in answer.text


def test_a_made_up_or_missing_token_is_refused(client_factory):
    client = client_factory(EXA_API_KEY=EXA_KEY)
    _, token = _account(client)
    machine_id = token.split(".")[0]
    for bad in ("", f"{machine_id}.0000", f"{int(machine_id) + 1}.{token.split('.')[1]}", "nonsense"):
        assert _search(client, bad).status_code == 403


def test_without_a_shared_key_the_search_is_off_and_tasks_get_no_token(client_factory, settings):
    client = client_factory()
    _, token = _account(client)
    assert _search(client, token).status_code == 503
    assert search.task_environment(settings, 1) == {}


def test_the_day_quota_counts_the_last_24_hours_only(client_factory):
    client = client_factory(_exa([]), EXA_API_KEY=EXA_KEY)
    user_id, token = _account(client)
    _used(client, user_id, [timedelta(hours=25)] * 5 + [timedelta(hours=23)] * (search.PER_DAY - 1))

    assert _search(client, token).status_code == 200
    refused = _search(client, token)

    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "search_quota"
    assert "/web-search-provider" in refused.json()["error"]["message"]


def test_the_week_quota_holds_even_when_each_day_is_under_its_own(client_factory):
    client = client_factory(_exa([]), EXA_API_KEY=EXA_KEY)
    user_id, token = _account(client)
    _used(client, user_id, [timedelta(days=2)] * search.PER_WEEK)

    refused = _search(client, token)

    assert refused.status_code == 429
    assert "190" in refused.json()["error"]["message"]


def test_a_failed_search_does_not_count(client_factory):
    client = client_factory(lambda request: httpx.Response(500), EXA_API_KEY=EXA_KEY)
    user_id, token = _account(client)

    assert _search(client, token).status_code == 502
    with session_scope(client.app.state.session_factory) as db:
        assert search.remaining(db, user_id, utcnow()) == {"day": search.PER_DAY, "week": search.PER_WEEK}


def test_tasks_get_their_machines_token_and_the_servers_url(settings):
    from dataclasses import replace

    env = search.task_environment(replace(settings, exa_api_key=EXA_KEY), 7)

    assert env == {
        "KVIST_CLOUD_SEARCH_URL": f"{settings.public_url}/gateway/search",
        "KVIST_CLOUD_SEARCH_API_KEY": search.search_token(settings, 7),
    }
    assert EXA_KEY not in json.dumps(env)
