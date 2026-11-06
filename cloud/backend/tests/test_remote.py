# -*- coding: utf-8 -*-
"""The Remote plan: the customer's own machine dials us, a year is paid at once, no hours are counted."""
from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import MASTER_KEY, dev_login, make_subscription, make_task
from platega_fake import PRICES, open_checkout, paid_client, pay_and_notify
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from kvist_cloud import ledger, plans
from kvist_cloud.db import session_scope, utcnow
from kvist_cloud.gateway import start_message
from kvist_cloud.models import LedgerEntry, Machine, MachineStatus, Subscription, TaskStatus, User
from kvist_cloud.secrets import Vault


def remote_client(client_factory, **overrides):
    client = client_factory(AUTH_DEV_LOGIN="true", **overrides)
    dev_login(client)
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        user.model_provider = "deepseek"
        user.model_key_enc = Vault(MASTER_KEY).encrypt("sk-remote-test-0123456789")
        make_subscription(db, user, "remote")
    return client


def remote_machine(db) -> Machine:
    return db.scalar(select(Machine).where(Machine.provider == "remote"))


def connect(client) -> dict:
    response = client.post("/api/machines/remote/connect")
    assert response.status_code == 200, response.text
    return response.json()


def test_the_plan_is_a_year_with_no_machine_of_ours_and_no_hours():
    remote = plans.get_plan("remote")
    assert (remote.price_usd, remote.period_days, remote.own_machine, remote.metered) == (48, 365, True, False)
    assert plans.get_plan("max").metered and plans.get_plan("max").period_days == 30
    # It is sold beside the table of machines, not inside it.
    assert "remote" not in [row["id"] for row in plans.public_catalog()]
    assert plans.remote_offer()["price_usd"] == 48


def test_paying_gives_a_year_and_no_hours(client_factory, engine):
    client, platega = paid_client(client_factory)
    pay_and_notify(client, platega, "remote")
    with session_scope(client.app.state.session_factory) as db:
        row = db.scalar(select(Subscription))
        assert (row.plan, row.status) == ("remote", "active")
        assert row.current_period_end - row.current_period_start == timedelta(days=365)
        assert db.scalar(select(func.coalesce(func.sum(LedgerEntry.seconds), 0))) == 0
    view = client.get("/api/billing/subscription").json()
    assert (view["plan"], view["own_machine"], view["unmetered"], view["balance_seconds"]) == ("remote", True, True, 0)
    request = [r for r in platega.requests if r.method == "POST"][-1]
    assert b"4000.0" in request.content
    assert PRICES["remote"] == "4000.00"


def test_hour_packs_are_not_sold_to_a_plan_with_no_hours(client_factory):
    client, platega = paid_client(client_factory)
    pay_and_notify(client, platega, "remote")
    calls = len(platega.requests)
    assert client.post("/api/billing/checkout", json={"plan": "hours"}).status_code == 409
    assert client.post("/api/billing/checkout", json={"plan": "start"}).status_code == 409
    assert len(platega.requests) == calls


def test_connect_needs_the_remote_plan(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.post("/api/machines/remote/connect").status_code == 409
    with session_scope(client.app.state.session_factory) as db:
        make_subscription(db, db.get(User, 1), "start")
    assert client.post("/api/machines/remote/connect").status_code == 409


def test_connect_hands_over_what_the_runner_needs_and_keeps_only_a_hash(client_factory, engine):
    client = remote_client(client_factory)
    answer = connect(client)
    config = answer["runner_config"]
    assert set(config) == {"machine_id", "gateway_url", "token"}
    assert config["gateway_url"] == "ws://127.0.0.1:8740/gateway/ws" and len(config["token"]) >= 40
    with session_scope(client.app.state.session_factory) as db:
        machine = remote_machine(db)
        assert (machine.provider, machine.vcpu, machine.user_id) == ("remote", 0, 1)
        assert machine.token_hash != config["token"] and config["token"] not in str(machine.__dict__)
    assert answer["machine"]["display_status"] == "offline"
    # The token is in no listing.
    assert config["token"] not in client.get("/api/machines").text


def test_connecting_again_makes_a_new_token_and_kills_the_old_one(client_factory):
    client = remote_client(client_factory)
    first = connect(client)["runner_config"]
    with client.websocket_connect("/gateway/ws", headers={"Authorization": f"Bearer {first['token']}"}) as socket:
        socket.send_json({"type": "hello", "tasks": []})
        second = connect(client)["runner_config"]
        assert second["token"] != first["token"] and second["machine_id"] == first["machine_id"]
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()  # the old connection was dropped
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            "/gateway/ws", headers={"Authorization": f"Bearer {first['token']}"}):
        pass
    with client.websocket_connect("/gateway/ws", headers={"Authorization": f"Bearer {second['token']}"}) as socket:
        socket.send_json({"type": "hello", "tasks": []})
    with session_scope(client.app.state.session_factory) as db:
        assert db.scalar(select(func.count()).select_from(Machine).where(Machine.provider == "remote")) == 1


def test_one_person_cannot_take_anothers_machine_row(client_factory):
    client = remote_client(client_factory)
    token = connect(client)["runner_config"]["token"]
    other = client_factory(AUTH_DEV_LOGIN="true")
    with pytest.raises(WebSocketDisconnect), other.websocket_connect(
            "/gateway/ws", headers={"Authorization": "Bearer " + token[::-1]}):
        pass


def test_a_task_waits_for_the_machine_and_never_makes_one_of_ours(client_factory, fake_provider):
    client = remote_client(client_factory)
    calls = list(fake_provider.calls)  # a trial's machine may have been made at sign-in, before the plan
    response = client.post("/api/tasks", json={"goal": "do it"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "no_machine"
    assert client.post("/api/machines").status_code == 409
    connect(client)
    assert client.post("/api/tasks", json={"goal": "do it"}).status_code == 201
    assert fake_provider.calls == calls


def test_nothing_is_billed_and_nothing_runs_out(client_factory):
    client = remote_client(client_factory)
    connect(client)
    now = 1_800_000_000.0
    with session_scope(client.app.state.session_factory) as db:
        machine = remote_machine(db)
        task = make_task(db, db.get(User, 1), machine, status=TaskStatus.RUNNING)
        machine.last_beat_second, machine.last_beat_running = int(now) - 15, [task.id]
        result = ledger.record_heartbeat(db, machine, [task.id], now, 45.0)
        assert (result.billed_seconds, result.exhausted) == (0, False)
        assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.seconds < 0)) == 0
        assert ledger.has_hours(db, 1)


def test_the_runner_is_told_no_cpu_or_memory_cap(client_factory):
    client = remote_client(client_factory)
    connect(client)
    with session_scope(client.app.state.session_factory) as db:
        task = make_task(db, db.get(User, 1), remote_machine(db), status=TaskStatus.STARTING)
        message = start_message(client.app, db, task)
    assert message is not None
    assert (message["limits"]["vcpu"], message["limits"]["memory_gb"]) == (0, 0)
    assert message["limits"]["command_seconds"] == 360 * 60


def test_the_machine_shows_offline_until_its_runner_connects(client_factory):
    client = remote_client(client_factory)
    config = connect(client)["runner_config"]
    assert [row["display_status"] for row in client.get("/api/machines").json()] == ["offline"]
    with client.websocket_connect("/gateway/ws", headers={"Authorization": f"Bearer {config['token']}"}) as socket:
        socket.send_json({"type": "hello", "tasks": []})
        socket.send_json({"type": "heartbeat", "tasks": []})
        listed = client.get("/api/machines").json()
        assert [row["id"] for row in listed] == [config["machine_id"]]  # a leftover VM is not shown


def test_a_machine_that_is_off_is_not_given_up_on(client_factory):
    client = remote_client(client_factory)
    connect(client)
    with session_scope(client.app.state.session_factory) as db:
        machine = remote_machine(db)
        machine.status = MachineStatus.WAKING
        machine.last_active_at = utcnow() - timedelta(hours=1)
    from kvist_cloud import machines

    client.portal.call(machines.sweep_once, client.app)
    with session_scope(client.app.state.session_factory) as db:
        assert remote_machine(db).status != MachineStatus.ERROR


def test_checkout_for_remote_uses_the_servers_year_price(client_factory):
    client, platega = paid_client(client_factory)
    transaction_id = open_checkout(client, "remote")
    body = platega.transactions[transaction_id]["request"]
    assert body["paymentDetails"] == {"amount": 4000.0, "currency": "RUB"}
    assert body["description"] == "Kvist Cloud: Remote plan, 365 days"
