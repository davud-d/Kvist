# -*- coding: utf-8 -*-
"""The reviewer's link: a ready test account on the Start plan, for a few minutes."""
from __future__ import annotations

import time
from urllib.parse import urlparse

from kvist_cloud import review
from kvist_cloud.models import SubscriptionStatus


def path_of(link: str) -> str:
    parts = urlparse(link)
    return f"{parts.path}?{parts.query}"


def test_link_signs_into_a_test_account_on_the_start_plan(client_factory):
    client = client_factory()
    link = review.make_link(client.app.state.settings, 2)
    response = client.get(path_of(link), follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/dashboard"
    me = client.get("/api/me").json()
    assert (me["login"], me["terms_accepted"], me["is_admin"]) == (review.REVIEW_LOGIN, False, False)
    # Like any new account: the two boxes first, then the plan is there.
    assert client.post("/api/me/terms", json={"terms": True, "privacy": True}).status_code == 200
    subscription = client.get("/api/billing/subscription").json()
    assert (subscription["plan"], subscription["status"]) == ("start", SubscriptionStatus.ACTIVE)


def test_link_works_twice_so_a_chat_preview_does_not_spend_it(client_factory):
    client = client_factory()
    path = path_of(review.make_link(client.app.state.settings, 2))
    for _ in range(2):
        assert client.get(path, follow_redirects=False).status_code == 302
    assert client.get("/api/me").json()["login"] == review.REVIEW_LOGIN


def test_link_stops_working_after_its_time(client_factory, monkeypatch):
    client = client_factory()
    path = path_of(review.make_link(client.app.state.settings, 2))
    now = time.time()
    monkeypatch.setattr(review.time, "time", lambda: now + 121)
    refused = client.get(path, follow_redirects=False)
    assert refused.status_code == 410 and refused.json()["error"]["code"] == "review_link_expired"
    assert client.get("/api/me").status_code == 401


def test_forged_or_missing_token_is_refused(client_factory):
    client = client_factory()
    for query in ("", "?t=", "?t=garbage", "?t=" + review.make_link(client.app.state.settings, 2).split("t=")[1][:-3]):
        assert client.get(f"/auth/review{query}", follow_redirects=False).status_code == 410
    assert client.get("/api/me").status_code == 401


def test_a_link_made_with_another_secret_is_refused(client_factory):
    client = client_factory()
    other = client_factory(KVIST_CLOUD_SESSION_SECRET="another-secret-another-secret-another-secret-0123")
    path = path_of(review.make_link(other.app.state.settings, 2))
    assert client.get(path, follow_redirects=False).status_code == 410
