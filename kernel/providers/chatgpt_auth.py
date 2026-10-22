# -*- coding: utf-8 -*-
"""Official Sign in with ChatGPT public-client OAuth and private credential storage."""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from kernel.config.user_config import _update_user_config, load_user_config

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN_ENDPOINT = ISSUER + "/api/accounts/oauth/token"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + PLAN_SCOPE
STORE_KEY = "chatgpt_oauth"
_ATTEMPTS: dict[str, LoginAttempt] = {}
_ATTEMPTS_LOCK = threading.RLock()


class ChatGPTAuthError(RuntimeError):
    """An authentication failure safe to display without exposing credentials."""


def _store(config_path: Path | None = None) -> dict:
    value = load_user_config(config_path).get(STORE_KEY, {})
    if not isinstance(value, dict) or not isinstance(value.get("accounts", {}), dict):
        raise ChatGPTAuthError("ChatGPT credential storage is invalid / Хранилище ChatGPT повреждено.")
    return value


def account_status(config_path: Path | None = None) -> dict:
    store = _store(config_path)
    accounts = store.get("accounts", {})
    active = store.get("active", "")
    return {
        "active": active,
        "accounts": [
            {"id": key, "email": value.get("email", ""),
             "connected": bool(value.get("access_token") and value.get("refresh_token") and PLAN_SCOPE in value.get("scopes", []))}
            for key, value in accounts.items()
        ],
    }


def has_credentials(config_path: Path | None = None) -> bool:
    status = account_status(config_path)
    return any(a["id"] == status["active"] and a["connected"] for a in status["accounts"])


def select_account(account_id: str, config_path: Path | None = None) -> None:
    def update(payload):
        store = payload.get(STORE_KEY, {})
        if account_id not in store.get("accounts", {}):
            raise ChatGPTAuthError("Unknown ChatGPT account / Неизвестный аккаунт ChatGPT.")
        store["active"] = account_id
        return payload
    _update_user_config(update, config_path)


def _host_id(config_path: Path | None) -> str:
    def update(payload):
        store = payload.setdefault(STORE_KEY, {})
        store.setdefault("host_id", "urn:uuid:" + str(uuid.uuid4()))
        return payload
    return _update_user_config(update, config_path)[STORE_KEY]["host_id"]


def _token_request(data: dict) -> dict:
    try:
        response = httpx.post(TOKEN_ENDPOINT, data={**data, "resource": RESOURCE}, timeout=30)
        if response.status_code != 200:
            raise ChatGPTAuthError("ChatGPT authorization expired or was refused. Sign in again / Войдите в ChatGPT снова.")
        value = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise ChatGPTAuthError("Could not contact ChatGPT authorization service / Не удалось связаться с ChatGPT.") from error
    if not isinstance(value, dict):
        raise ChatGPTAuthError("Invalid ChatGPT token response / Некорректный ответ ChatGPT.")
    return value


def _validate_identity(token: str, client_id: str, nonce: str | None = None) -> dict:
    try:
        keys = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json", timeout=15)
        key = keys.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=client_id,
                            issuer=ISSUER, options={"require": ["exp", "iss", "aud", "sub"]})
    except (jwt.PyJWTError, OSError, ValueError) as error:
        raise ChatGPTAuthError("ChatGPT identity verification failed / Не удалось проверить аккаунт ChatGPT.") from error
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise ChatGPTAuthError("ChatGPT identity is missing / Отсутствует идентификатор аккаунта.")
    if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ChatGPTAuthError("ChatGPT sign-in nonce mismatch / Проверка входа ChatGPT не пройдена.")
    return claims


def _token_record(tokens: dict, previous: dict) -> dict:
    scopes = str(tokens.get("scope", " ".join(previous.get("scopes", [])))).split()
    if PLAN_SCOPE not in scopes:
        raise ChatGPTAuthError("Allow ChatGPT plan usage, then sign in again / Разрешите использование подписки и войдите снова.")
    if not all(isinstance(tokens.get(k), str) and tokens[k] for k in ("access_token", "refresh_token")):
        raise ChatGPTAuthError("ChatGPT did not return renewable credentials / ChatGPT не вернул токены для продления доступа.")
    if str(tokens.get("token_type", "")).lower() != "bearer":
        raise ChatGPTAuthError("Unsupported ChatGPT token type / Неподдерживаемый тип токена ChatGPT.")
    try:
        lifetime = int(tokens["expires_in"])
    except (KeyError, TypeError, ValueError) as error:
        raise ChatGPTAuthError("Missing ChatGPT token expiry / Отсутствует срок действия токена.") from error
    if lifetime <= 0:
        raise ChatGPTAuthError("Expired ChatGPT token / Токен ChatGPT истёк.")
    return {**previous, **{k: tokens[k] for k in ("access_token", "refresh_token")},
            "id_token": tokens.get("id_token") or previous.get("id_token", ""),
            "expires_at": time.time() + lifetime, "scopes": scopes}


def access_token(account_id: str | None = None, config_path: Path | None = None, *, force_refresh: bool = False) -> str:
    # update_json_object holds the cross-process lock during rotation and its atomic save.
    store = _store(config_path)
    selected = account_id or store.get("active", "")
    current = store.get("accounts", {}).get(selected, {})
    if (not force_refresh and current.get("access_token") and current.get("refresh_token")
            and PLAN_SCOPE in current.get("scopes", []) and current.get("expires_at", 0) > time.time() + 60):
        return current["access_token"]
    def update(payload):
        record = payload.get(STORE_KEY, {}).get("accounts", {}).get(selected)
        if not record or not record.get("refresh_token") or PLAN_SCOPE not in record.get("scopes", []):
            raise ChatGPTAuthError("Continue with ChatGPT first / Сначала войдите через ChatGPT.")
        if not force_refresh and record.get("expires_at", 0) > time.time() + 60:
            return payload
        tokens = _token_request({"grant_type": "refresh_token", "client_id": record["client_id"],
                                 "refresh_token": record["refresh_token"]})
        if tokens.get("id_token"):
            claims = _validate_identity(tokens["id_token"], record["client_id"])
            if claims["sub"] != record["subject"]:
                raise ChatGPTAuthError("ChatGPT account changed unexpectedly / Аккаунт ChatGPT неожиданно изменился.")
        payload[STORE_KEY]["accounts"][selected] = _token_record(tokens, record)
        return payload
    payload = _update_user_config(update, config_path)
    return payload[STORE_KEY]["accounts"][selected]["access_token"]


def sign_out(config_path: Path | None = None) -> dict:
    confirmed = True
    def update(payload):
        nonlocal confirmed
        store = payload.get(STORE_KEY, {})
        record = store.get("accounts", {}).get(store.get("active", ""))
        if not record:
            return payload
        if record.get("refresh_token"):
            try:
                discovery = httpx.get(ISSUER + "/.well-known/openid-configuration", timeout=15)
                discovery.raise_for_status()
                endpoint = discovery.json()["revocation_endpoint"]
                parsed = urlsplit(endpoint)
                if parsed.scheme != "https" or parsed.netloc != "auth.openai.com":
                    raise ValueError("Invalid revocation endpoint")
                response = httpx.post(endpoint, data={"token": record["refresh_token"],
                    "token_type_hint": "refresh_token", "client_id": record["client_id"]}, timeout=15)
                confirmed = response.status_code == 200
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                confirmed = False
        for key in ("access_token", "refresh_token", "id_token", "expires_at", "scopes"):
            record.pop(key, None)
        return payload
    _update_user_config(update, config_path)
    return {"ok": True, "revocation_confirmed": confirmed, "message": (
        "Signed out / Вы вышли." if confirmed else
        "Signed out locally. Remote revocation was not confirmed; disconnect Kvist in ChatGPT Settings / "
        "Локальный выход выполнен. Отзыв токена не подтверждён; отключите Kvist в настройках ChatGPT.")}


class LoginAttempt:
    def __init__(self, config_path: Path | None = None, account_id: str | None = None):
        self.config_path = config_path
        self.previous = _store(config_path).get("accounts", {}).get(account_id, {}) if account_id else {}
        if account_id and not self.previous:
            raise ChatGPTAuthError("Unknown ChatGPT account / Неизвестный аккаунт ChatGPT.")
        self.state = secrets.token_urlsafe(32)
        self.nonce = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(64)
        self.host_id = _host_id(config_path)
        self.done = threading.Event()
        self.lock = threading.RLock()
        self.error = ""
        self.account_id = ""
        self.committed = False
        self.deadline = time.monotonic() + 180
        attempt = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return  # OAuth callback URLs contain credentials.

            def do_GET(self):
                parsed = urlsplit(self.path)
                params = parse_qs(parsed.query, keep_blank_values=True)
                valid = (parsed.path == "/auth/callback" and
                         self.headers.get("Host") == f"127.0.0.1:{attempt.server.server_port}" and
                         len(params.get("state", [])) == 1 and
                         secrets.compare_digest(params["state"][0], attempt.state))
                if not valid or attempt.done.is_set():
                    self.send_error(400, "Invalid callback")
                    return
                try:
                    attempt.complete(params)
                except (ChatGPTAuthError, RuntimeError) as error:
                    attempt.error = str(error)
                try:
                    self.send_response(200 if not attempt.error else 400)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.end_headers()
                    self.wfile.write(("ChatGPT connected. Return to Kvist. / Вернитесь в Kvist." if not attempt.error
                                      else "ChatGPT sign-in failed. Return to Kvist. / Ошибка входа. Вернитесь в Kvist.").encode())
                finally:
                    attempt.done.set()

        class CallbackServer(HTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(5)
                return connection, address

        self.server = CallbackServer(("127.0.0.1", 0), Handler)
        self.server.timeout = 0.5
        self.redirect_uri = f"http://127.0.0.1:{self.server.server_port}/auth/callback"
        params = {"client_id": self.previous.get("client_id", "dynamic_agent_client"),
                  "ext_agent_host_id": self.host_id, "response_type": "code", "redirect_uri": self.redirect_uri,
                  "scope": SCOPES, "resource": RESOURCE, "state": self.state, "nonce": self.nonce,
                  "code_challenge_method": "S256", "code_challenge": base64.urlsafe_b64encode(
                      hashlib.sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()}
        if not self.previous:
            params["agent_name_hint"] = "Kvist"
        self.url = ISSUER + "/api/accounts/authorize?" + urlencode(params)
        threading.Thread(target=self._listen, daemon=True, name="chatgpt-oauth").start()

    def _listen(self):
        try:
            while not self.done.is_set() and time.monotonic() < self.deadline:
                self.server.handle_request()
            if not self.done.is_set():
                self.error = "ChatGPT sign-in timed out / Время ожидания входа ChatGPT истекло."
                self.done.set()
        finally:
            self.server.server_close()

    def complete(self, params: dict):
        if params.get("error"):
            raise ChatGPTAuthError("ChatGPT sign-in was declined / Вход ChatGPT отклонён.")
        if any(len(params.get(k, [])) > 1 for k in ("code", "client_id")):
            raise ChatGPTAuthError("Invalid ChatGPT callback / Некорректный ответ входа.")
        client_id = params.get("client_id", [self.previous.get("client_id", "")])[0]
        if not client_id or client_id == "dynamic_agent_client":
            raise ChatGPTAuthError("ChatGPT registration incomplete / Регистрация ChatGPT не завершена.")
        if self.previous and client_id != self.previous["client_id"]:
            raise ChatGPTAuthError("ChatGPT registration mismatch / Регистрация ChatGPT не совпадает.")
        code = params.get("code", [""])[0]
        if not code:
            raise ChatGPTAuthError("Missing ChatGPT authorization code / Отсутствует код входа.")
        tokens = _token_request({"grant_type": "authorization_code", "client_id": client_id, "code": code,
                                 "code_verifier": self.verifier, "redirect_uri": self.redirect_uri})
        claims = _validate_identity(tokens.get("id_token", ""), client_id, self.nonce)
        if self.previous and claims["sub"] != self.previous["subject"]:
            raise ChatGPTAuthError("ChatGPT account mismatch / Аккаунт ChatGPT не совпадает.")
        record = _token_record(tokens, {"client_id": client_id, "subject": claims["sub"],
                                        "email": claims.get("email", ""), "issuer": ISSUER})
        self.account_id = client_id + ":" + claims["sub"]
        def update(payload):
            store = payload.setdefault(STORE_KEY, {})
            store.setdefault("accounts", {})[self.account_id] = record
            store["active"] = self.account_id
            return payload
        with self.lock:
            if self.done.is_set() or time.monotonic() >= self.deadline:
                raise ChatGPTAuthError("ChatGPT sign-in cancelled or expired / Вход отменён или истёк.")
            _update_user_config(update, self.config_path)
            self.committed = True

    def cancel(self):
        with self.lock:
            if self.done.is_set() or self.committed:
                return
            self.error = "ChatGPT sign-in cancelled / Вход ChatGPT отменён."
            self.done.set()

    def result(self):
        return {"ok": True, "status": "pending" if not self.done.is_set() else ("error" if self.error else "connected"),
                "error": self.error, "account": self.account_id}


def start_login(config_path: Path | None = None, account_id: str | None = None) -> tuple[str, LoginAttempt]:
    with _ATTEMPTS_LOCK:
        for attempt in _ATTEMPTS.values():
            if not attempt.done.is_set():
                raise ChatGPTAuthError("ChatGPT sign-in is already open / Вход ChatGPT уже открыт.")
        _ATTEMPTS.clear()
        attempt_id = secrets.token_urlsafe(24)
        attempt = LoginAttempt(config_path, account_id)
        _ATTEMPTS[attempt_id] = attempt
        return attempt_id, attempt


def login(config_path: Path | None = None, *, account_id: str | None = None) -> None:
    _id, attempt = start_login(config_path, account_id)
    try:
        if not webbrowser.open(attempt.url):
            raise ChatGPTAuthError("Could not open browser. Use the web frontend to sign in / Не удалось открыть браузер.")
        attempt.done.wait(185)
        if attempt.error or not attempt.done.is_set():
            raise ChatGPTAuthError(attempt.error or "ChatGPT sign-in timed out / Время входа истекло.")
    finally:
        if not attempt.done.is_set():
            attempt.cancel()


def login_status(attempt_id: str, *, cancel: bool = False) -> dict:
    with _ATTEMPTS_LOCK:
        attempt = _ATTEMPTS.get(attempt_id)
        if attempt is None:
            raise ChatGPTAuthError("Unknown sign-in attempt / Попытка входа не найдена.")
        if cancel and not attempt.done.is_set():
            attempt.cancel()
        return attempt.result()
