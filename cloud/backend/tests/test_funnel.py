# -*- coding: utf-8 -*-
"""Funnel steps, first-party only, and the admin's conversion and dispute endpoints."""
from __future__ import annotations

import httpx
import pytest
from conftest import dev_login, make_task, settle
from sqlalchemy import select

from kvist_cloud import funnel, ledger
from kvist_cloud.db import make_session_factory, session_scope
from kvist_cloud.models import Dispute, FunnelEvent, Machine, TaskStatus, User


@pytest.fixture
def dist(tmp_path):
    folder = tmp_path / "dist"
    folder.mkdir()
    for name in ("pricing.en", "pricing.ru", "app.en", "app.ru"):
        (folder / f"{name}.html").write_text("<!doctype html>")
    return folder


def steps(engine) -> list[tuple[str, int | None]]:
    with session_scope(make_session_factory(engine)) as db:
        return [(row.step, row.user_id) for row in db.scalars(select(FunnelEvent).order_by(FunnelEvent.id))]


def test_the_whole_funnel_is_recorded_without_any_tracker(client_factory, engine, dist):
    client = client_factory(lambda request: httpx.Response(200, json={"is_available": True}),
                            AUTH_DEV_LOGIN="true", FRONTEND_DIST=str(dist), ADMIN_GITHUB_LOGINS="dev")
    client.get("/")
    client.get("/")  # a reload is not a second person
    visitor = client.cookies.get("kc_visitor")
    assert visitor
    dev_login(client)
    settle(client)
    client.post("/api/billing/checkout-opened")
    client.post("/api/billing/dev/skip-checkout")
    client.post("/api/model-key", json={"provider": "deepseek", "key": "sk-funnel-0123456789abcdef"})
    client.post("/api/tasks", json={"goal": "explain"})
    assert [step for step, _ in steps(engine)] == [
        "pricing_view", "github_login", "checkout_open", "card_added", "key_valid", "first_task_started"]
    rows = client.get("/api/admin/funnel").json()["steps"]
    # The anonymous pricing view and the signed-in steps are the same person.
    assert [(row["step"], row["people"]) for row in rows] == [
        ("pricing_view", 1), ("github_login", 1), ("checkout_open", 1), ("card_added", 1), ("key_valid", 1),
        ("first_task_started", 1), ("first_task_done", 0)]
    assert rows[1]["from_previous"] == 1.0 and rows[0]["from_previous"] is None


def test_conversion_counts_people_not_events(db):
    for visitor in ("a", "b", "c", "d"):
        funnel.record_step(db, "pricing_view", visitor_id=visitor)
    user = User(github_id=5, github_login="x")
    db.add(user)
    db.flush()
    funnel.record_step(db, "github_login", user_id=user.id, visitor_id="b")
    funnel.record_step(db, "github_login", user_id=user.id, visitor_id="b")
    db.flush()
    rows = {row["step"]: row for row in funnel.conversion(db)}
    assert (rows["pricing_view"]["people"], rows["github_login"]["people"]) == (4, 1)
    assert rows["github_login"]["from_previous"] == 0.25
    assert rows["checkout_open"]["from_previous"] == 0.0


def test_unknown_step_is_refused(db):
    with pytest.raises(ValueError):
        funnel.record_step(db, "clicked_something")


def test_admin_only(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.get("/api/admin/funnel").status_code == 403
    assert client.get("/api/admin/disputes").status_code == 403


def test_admin_refund_is_a_new_ledger_row(client_factory, engine):
    client = client_factory(AUTH_DEV_LOGIN="true", ADMIN_GITHUB_LOGINS="dev")
    dev_login(client)
    settle(client)
    client.post("/api/billing/dev/skip-checkout")
    with session_scope(make_session_factory(engine)) as db:
        user = db.scalars(select(User)).one()
        machine = db.scalars(select(Machine)).one()
        task = make_task(db, user, machine, status=TaskStatus.RUNNING)
        ledger.record_heartbeat(db, machine, [task.id], 1_800_000_000, 45)
        ledger.record_heartbeat(db, machine, [task.id], 1_800_000_040, 45)
        task_id = task.id
    client.post("/api/disputes", json={"task_id": task_id, "message": "it looped"})
    [open_dispute] = client.get("/api/admin/disputes").json()
    assert (open_dispute["charged_seconds"], open_dispute["message"]) == (40, "it looped")
    assert client.post(f"/api/admin/disputes/{open_dispute['id']}/refund",
                       json={"minutes": 1, "note": "model loop"}).json()["ok"]
    assert client.get("/api/admin/disputes").json() == []
    view = client.get("/api/ledger").json()
    assert view["subscription"]["balance_seconds"] == 5 * 3600 - 40 + 60
    refund = next(row for row in view["feed"] if row["kind"] == "refund")
    assert refund["reason"].startswith("dispute #") and refund["seconds"] == 60
    with session_scope(make_session_factory(engine)) as db:
        assert db.scalars(select(Dispute)).one().status == "refunded"
