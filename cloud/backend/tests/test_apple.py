# -*- coding: utf-8 -*-
"""Sign in with Apple against a fake Apple: its keys, the token and revoke endpoints; GitHub connected afterwards."""
from __future__ import annotations

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from apple_fake import BUNDLE, EC_KEY, OTHER_KEY, SERVICES, FakeApple, id_token, p8_pem
from conftest import accept_terms
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from kvist_cloud import apple
from kvist_cloud.config import ConfigError, load_settings
from kvist_cloud.db import session_scope
from kvist_cloud.models import User


@pytest.fixture
def apple_env(tmp_path):
    key_file = tmp_path / "AuthKey.p8"
    key_file.write_bytes(p8_pem())
    return {"APPLE_SIGNIN_ENABLED": "true", "APPLE_TEAM_ID": "TEAM123456", "APPLE_KEY_ID": "KEY1234567",
            "APPLE_PRIVATE_KEY_FILE": str(key_file), "APPLE_SERVICES_ID": SERVICES,
            "GITHUB_CLIENT_ID": "client-id", "GITHUB_CLIENT_SECRET": "client-secret-value"}


def native(client, **override):
    nonce = client.post("/api/auth/apple/nonce").json()["nonce"]
    body = {"identity_token": id_token(BUNDLE, hashlib.sha256(nonce.encode()).hexdigest()),
            "authorization_code": "c.apple-code", "nonce": nonce, "name": "Ada Lovelace"} | override
    return client.post("/api/auth/apple", json=body)


def test_the_app_sheet_signs_in_a_new_apple_account_without_a_trial(client_factory, apple_env):
    fake = FakeApple()
    client = client_factory(fake, **apple_env)
    assert client.get("/api/auth/config").json() == {"apple_app": True, "apple_web": True}
    assert native(client).status_code == 200
    accept_terms(client)
    me = client.get("/api/me").json()
    assert (me["name"], me["apple"], me["github"], me["login"]) == ("Ada Lovelace", True, False, "")
    eligibility = client.get("/api/billing/eligibility").json()
    assert eligibility["trial_eligible"] is False and eligibility["github_linked"] is False
    # The code was traded with the app's bundle id as the client, signed with the team's key.
    assert fake.token_forms[0]["client_id"] == BUNDLE and fake.token_forms[0]["code"] == "c.apple-code"
    # Signing in again finds the same account; Apple tells the name only once, so it is kept.
    client.cookies.clear()
    assert native(client, name="").status_code == 200
    assert client.get("/api/me").json()["id"] == me["id"]


@pytest.mark.parametrize("bad", ["audience", "nonce", "signature", "unknown key", "expired", "issuer", "replay"])
def test_a_token_that_is_not_apples_for_us_now_is_refused(client_factory, apple_env, bad):
    client = client_factory(FakeApple(), **apple_env)
    nonce = client.post("/api/auth/apple/nonce").json()["nonce"]
    hashed = hashlib.sha256(nonce.encode()).hexdigest()
    token = {
        "audience": lambda: id_token(SERVICES, hashed),
        "nonce": lambda: id_token(BUNDLE, hashlib.sha256(b"other").hexdigest()),
        "signature": lambda: id_token(BUNDLE, hashed, key=OTHER_KEY),
        "unknown key": lambda: id_token(BUNDLE, hashed, kid="k9"),
        "expired": lambda: id_token(BUNDLE, hashed, exp=int(time.time()) - 3600),
        "issuer": lambda: id_token(BUNDLE, hashed, iss="https://evil.example"),
        "replay": lambda: id_token(BUNDLE, hashlib.sha256(b"not-signed-by-us").hexdigest()),
    }[bad]()
    sent_nonce = "not-signed-by-us" if bad == "replay" else nonce
    response = client.post("/api/auth/apple", json={"identity_token": token, "nonce": sent_nonce})
    assert response.status_code == 400
    assert client.get("/api/me").status_code == 401
    with session_scope(client.app.state.session_factory) as db:
        assert db.query(User).count() == 0


def test_connecting_an_old_github_account_opens_the_trial(client_factory, apple_env):
    client = client_factory(FakeApple(), **apple_env)
    native(client)
    accept_terms(client)
    start = client.get("/auth/github/login?link=1&next=/m/", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    done = client.get(f"/auth/github/callback?code=good&state={state}", follow_redirects=False)
    assert done.status_code == 302 and done.headers["location"] == "/m/"
    me = client.get("/api/me").json()
    assert (me["github"], me["apple"], me["login"], me["name"]) == (True, True, "octocat", "Ada Lovelace")
    assert client.get("/api/billing/eligibility").json()["trial_eligible"] is True


def test_a_young_github_account_does_not_open_the_trial(client_factory, apple_env):
    fake = FakeApple()
    fake.github = FakeApple.github | {"created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    client = client_factory(fake, **apple_env)
    native(client)
    accept_terms(client)
    start = client.get("/auth/github/login?link=1", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get(f"/auth/github/callback?code=good&state={state}", follow_redirects=False)
    assert client.get("/api/me").json()["github"] is True
    assert client.get("/api/billing/eligibility").json()["trial_eligible"] is False


def test_a_github_account_with_its_own_kvist_account_cannot_be_connected(client_factory, apple_env):
    client = client_factory(FakeApple(), **apple_env)
    start = client.get("/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.get(f"/auth/github/callback?code=good&state={state}", follow_redirects=False)
    client.cookies.clear()
    native(client)
    start = client.get("/auth/github/login?link=1", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    refused = client.get(f"/auth/github/callback?code=good&state={state}", follow_redirects=False)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "github_taken"
    assert client.get("/api/me").json()["github"] is False


def test_linking_needs_a_signed_in_account_without_github(client_factory, apple_env):
    client = client_factory(FakeApple(), **apple_env)
    assert client.get("/auth/github/login?link=1", follow_redirects=False).status_code == 401


def test_deleting_the_account_revokes_apples_token(client_factory, apple_env):
    fake = FakeApple()
    client = client_factory(fake, **apple_env)
    native(client)
    assert client.post("/api/me/delete", json={"confirm": True}).status_code == 200
    assert len(fake.revoked) == 1
    assert fake.revoked[0]["token"] == "r.apple-refresh" and fake.revoked[0]["client_id"] == BUNDLE
    # Coming back within the grace period with Apple cancels the deletion, like GitHub does.
    assert native(client, name="").status_code == 200
    with session_scope(client.app.state.session_factory) as db:
        assert db.query(User).one().delete_requested_at is None


def test_the_web_flow_round_trip(client_factory, apple_env):
    fake = FakeApple()
    client = client_factory(fake, **apple_env)
    start = client.get("/auth/apple/login?next=/m/", follow_redirects=False)
    query = parse_qs(urlparse(start.headers["location"]).query)
    assert query["client_id"] == [SERVICES] and query["response_mode"] == ["form_post"]
    assert "SameSite=none" in start.headers["set-cookie"] and "Secure" in start.headers["set-cookie"]
    # The test client is plain http and keeps Secure cookies to itself: the cookie is handed over by hand.
    state_cookie = start.cookies[apple.STATE_COOKIE]
    form = {"state": query["state"][0], "code": "c.web", "id_token": id_token(SERVICES, query["nonce"][0]),
            "user": json.dumps({"name": {"firstName": "Ada", "lastName": "L"}})}
    done = client.post("/auth/apple/callback", data=form, cookies={apple.STATE_COOKIE: state_cookie},
                       follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/m/"
    assert client.get("/api/me").json()["name"] == "Ada L"
    assert fake.token_forms[0]["redirect_uri"].endswith("/auth/apple/callback")
    wrong = client.post("/auth/apple/callback", data=form | {"state": "other"},
                        cookies={apple.STATE_COOKIE: state_cookie}, follow_redirects=False)
    assert wrong.status_code == 400


def test_the_client_secret_is_a_valid_es256_token(apple_env):
    settings = load_settings({"KVIST_CLOUD_SESSION_SECRET": "s" * 40, "KVIST_CLOUD_MASTER_KEY": "k", **apple_env})
    token = apple.client_secret(settings.apple, BUNDLE, now=1_000)
    head, body, signature = token.split(".")
    raw = base64.urlsafe_b64decode(signature + "==")
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    EC_KEY.public_key().verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    assert claims == {"iss": "TEAM123456", "iat": 1000, "exp": 1300, "aud": apple.ISSUER, "sub": BUNDLE}


def test_off_unless_configured(client_factory):
    client = client_factory()
    assert client.get("/api/auth/config").json() == {"apple_app": False, "apple_web": False}
    assert client.post("/api/auth/apple/nonce").status_code == 503


def test_without_github_a_plan_is_bought_straight_away_only_the_trial_waits(client_factory, apple_env):
    from platega_fake import PLATEGA, PlategaFake, open_checkout

    apple_fake, platega = FakeApple(), PlategaFake()
    client = client_factory(lambda request: (platega if request.url.host == "app.platega.io" else apple_fake)(request),
                            **apple_env, **PLATEGA)
    native(client)
    accept_terms(client)
    trial = client.post("/api/billing/checkout", json={"plan": "trial"})
    assert trial.status_code == 403 and trial.json()["error"]["code"] == "trial_ineligible"
    transaction_id = open_checkout(client, "start")
    body = platega.transactions[transaction_id]["request"]
    # Platega wants an email: Apple's relay address is the one this account has.
    assert body["metadata"]["user_email"] == "abc@privaterelay.appleid.com"


@pytest.mark.parametrize("broken", ["APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_PRIVATE_KEY_FILE"])
def test_a_half_configured_apple_refuses_to_start(apple_env, broken):
    env = {"KVIST_CLOUD_SESSION_SECRET": "s" * 40, "KVIST_CLOUD_MASTER_KEY": "k", **apple_env, broken: "bad"}
    with pytest.raises(ConfigError):
        load_settings(env)
