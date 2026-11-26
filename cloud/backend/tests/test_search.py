# -*- coding: utf-8 -*-
"""The shared web search: the machine's search token, the account's quota, the key that never leaves the server."""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
from conftest import dev_login, make_machine, make_user
from sqlalchemy import select

from kvist_cloud import plans, search
from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.models import Notification, SearchUse, User

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
    assert answer.json()["remaining"] == {"day": search.PER_DAY - 1, "week": search.PER_WEEK - 1, "bought": 0}
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


def test_the_servers_daily_cap_holds_for_all_accounts_together(client_factory):
    client = client_factory(_exa([]), EXA_API_KEY=EXA_KEY, SEARCH_DAILY_CAP="3")
    user_id, token = _account(client)
    with session_scope(client.app.state.session_factory) as db:
        other = make_user(db, login="other", github_id=2).id
    _used(client, other, [timedelta(hours=23), timedelta(hours=1)])
    _used(client, user_id, [timedelta(hours=30)])

    assert _search(client, token).status_code == 200
    refused = _search(client, token)

    assert refused.status_code == 429
    assert "all accounts together" in refused.json()["error"]["message"]
    assert refused.json()["error"]["frees_at"].startswith((utcnow() + timedelta(hours=1)).strftime("%Y-%m-%dT%H"))


def test_a_failed_search_does_not_count(client_factory):
    client = client_factory(lambda request: httpx.Response(500), EXA_API_KEY=EXA_KEY)
    user_id, token = _account(client)

    assert _search(client, token).status_code == 502
    with session_scope(client.app.state.session_factory) as db:
        assert search.usage(db, user_id, utcnow()) == {"day": search.PER_DAY, "week": search.PER_WEEK, "bought": 0}


def _credits(client, user_id: int, value: int | None = None) -> int:
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, user_id)
        if value is not None:
            user.search_credits = value
        return user.search_credits


def test_bought_searches_are_spent_only_past_the_free_quota_and_never_eat_it(client_factory):
    client = client_factory(_exa([]), EXA_API_KEY=EXA_KEY)
    user_id, token = _account(client)
    _credits(client, user_id, 2)
    assert _search(client, token).status_code == 200
    assert _credits(client, user_id) == 2

    _used(client, user_id, [timedelta(hours=1)] * search.PER_DAY)
    answer = _search(client, token)

    assert answer.status_code == 200
    assert answer.json()["remaining"]["bought"] == 1
    with session_scope(client.app.state.session_factory) as db:
        # The paid search left the free count where it was.
        assert search.usage(db, user_id, utcnow())["day"] == 0
        assert db.scalars(select(SearchUse).where(SearchUse.paid.is_(True))).one()


def test_a_used_up_quota_tells_the_agent_where_to_buy_and_rings_the_bell_once_a_day(client_factory):
    client = client_factory(_exa([]), EXA_API_KEY=EXA_KEY)
    user_id, token = _account(client)
    _used(client, user_id, [timedelta(hours=1)] * search.PER_DAY)

    refusals = [_search(client, token) for _ in range(3)]

    error = refusals[0].json()["error"]
    assert all(answer.status_code == 429 for answer in refusals)
    assert error["buy_url"] == "http://127.0.0.1:8740/dashboard"
    assert f"{plans.SEARCH_PACK_SEARCHES} more searches" in error["message"] and "/dashboard" in error["message"]
    with session_scope(client.app.state.session_factory) as db:
        notices = list(db.scalars(select(Notification).where(Notification.kind == "search_quota")))
    assert len(notices) == 1 and notices[0].user_id == user_id


def test_a_failed_bought_search_is_given_back(client_factory):
    client = client_factory(lambda request: httpx.Response(500), EXA_API_KEY=EXA_KEY)
    user_id, token = _account(client)
    _credits(client, user_id, 1)
    _used(client, user_id, [timedelta(hours=1)] * search.PER_DAY)

    assert _search(client, token).status_code == 502
    assert _credits(client, user_id) == 1


def test_bought_searches_do_not_lift_the_servers_cap(client_factory):
    client = client_factory(_exa([]), EXA_API_KEY=EXA_KEY, SEARCH_DAILY_CAP="2")
    user_id, token = _account(client)
    _credits(client, user_id, 10)
    _used(client, user_id, [timedelta(hours=1)] * 2)

    refused = _search(client, token)

    assert refused.status_code == 429 and "all accounts together" in refused.json()["error"]["message"]
    assert _credits(client, user_id) == 10


def test_the_dashboard_sees_its_searches_and_the_pack(client_factory):
    client = client_factory(EXA_API_KEY=EXA_KEY, AUTH_DEV_LOGIN="true")
    dev_login(client)
    answer = client.get("/api/search/usage").json()
    assert answer == {"enabled": True, "per_day": search.PER_DAY, "per_week": search.PER_WEEK,
                      "day": search.PER_DAY, "week": search.PER_WEEK, "bought": 0,
                      "pack": {"searches": plans.SEARCH_PACK_SEARCHES, "price_usd": plans.SEARCH_PACK_PRICE_USD}}


def test_tasks_get_their_machines_token_and_the_servers_url(settings):
    from dataclasses import replace

    env = search.task_environment(replace(settings, exa_api_key=EXA_KEY), 7)

    assert env == {
        "KVIST_CLOUD_SEARCH_URL": f"{settings.public_url}/gateway/search",
        "KVIST_CLOUD_SEARCH_API_KEY": search.search_token(settings, 7),
    }
    assert EXA_KEY not in json.dumps(env)
