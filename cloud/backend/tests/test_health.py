# -*- coding: utf-8 -*-
"""/healthz: up when the database answers, 503 when it does not, and nothing else in the body."""
from __future__ import annotations

from sqlalchemy.exc import OperationalError


def test_healthz_is_ok_without_a_session(client_factory):
    client = client_factory()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_healthz_is_503_when_the_database_is_gone(client_factory):
    client = client_factory()

    def broken():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    client.app.state.session_factory = broken
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "down"}
