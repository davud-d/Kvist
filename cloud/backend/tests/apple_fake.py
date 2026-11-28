# -*- coding: utf-8 -*-
"""Sign in with Apple contract fake: Apple's published keys, its token and revoke endpoints, tokens signed like its.

Used by test_apple.py and by the phone page's browser tests (cloud/mobile/tests/server.py). Never reaches Apple.
"""
from __future__ import annotations

import base64
import json
import time
from urllib.parse import parse_qs

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

ISSUER = "https://appleid.apple.com"
BUNDLE = "site.kvist.cloud"
SERVICES = "site.kvist.cloud.web"
APPLE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
# The developer account's key (the .p8) that signs our client secret.
EC_KEY = ec.generate_private_key(ec.SECP256R1())


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def p8_pem() -> bytes:
    return EC_KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())


def jwk(key: rsa.RSAPrivateKey, kid: str) -> dict:
    numbers = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
            "n": b64(numbers.n.to_bytes(256, "big")), "e": b64(numbers.e.to_bytes(3, "big"))}


def id_token(aud: str, nonce: str, sub: str = "001234.apple-user", key=APPLE_KEY, kid: str = "k1", **extra) -> str:
    header = b64(json.dumps({"alg": "RS256", "kid": kid}).encode())
    claims = {"iss": ISSUER, "aud": aud, "sub": sub, "exp": int(time.time()) + 600, "iat": int(time.time()),
              "nonce": nonce, "email": "abc@privaterelay.appleid.com", **extra}
    body = b64(json.dumps(claims).encode())
    signature = key.sign(f"{header}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{body}.{b64(signature)}"


class FakeApple:
    """Apple's side, plus GitHub's two calls so one handler serves a test that connects GitHub afterwards."""

    github = {"id": 4242, "login": "octocat", "name": "", "avatar_url": "a", "created_at": "2020-01-01T00:00:00Z"}

    def __init__(self) -> None:
        self.token_forms: list[dict] = []
        self.revoked: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "appleid.apple.com":
            if request.url.path == "/auth/keys":
                return httpx.Response(200, json={"keys": [jwk(APPLE_KEY, "k1")]})
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if request.url.path == "/auth/token":
                self.token_forms.append(form)
                return httpx.Response(200, json={"refresh_token": "r.apple-refresh", "access_token": "a"})
            if request.url.path == "/auth/revoke":
                self.revoked.append(form)
                return httpx.Response(200)
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_test_token"})
        if request.url.path == "/user":
            return httpx.Response(200, json=self.github)
        return httpx.Response(404)
