# -*- coding: utf-8 -*-
"""Settings, read from the environment and nowhere else.

Secrets are fields with ``repr=False`` so an exception or a log line that prints the settings
object never carries them.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

CLOUD_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = CLOUD_ROOT.parent
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    database_url: str
    public_url: str
    session_secret: str = field(repr=False)
    master_key: str = field(repr=False)
    github_client_id: str = ""
    github_client_secret: str = field(default="", repr=False)
    auth_dev_login: bool = False
    billing_enabled: bool = False
    paddle_webhook_secret: str = field(default="", repr=False)
    paddle_api_key: str = field(default="", repr=False)
    paddle_client_token: str = ""
    paddle_environment: str = "sandbox"
    paddle_price_start: str = ""
    paddle_price_pro: str = ""
    paddle_price_max: str = ""
    paddle_price_hours: str = ""
    machine_provider: str = "local"
    runner_command: tuple[str, ...] = ()
    kvist_command: tuple[str, ...] = ()
    heartbeat_seconds: float = 15.0
    heartbeat_max_gap_seconds: float = 45.0
    machine_idle_sleep_seconds: float = 900.0
    provider_outage_seconds: float = 300.0
    hours_grace_seconds: float = 300.0
    admin_logins: frozenset[str] = frozenset()
    # Dev only: lets a model endpoint or a repository live on this computer.
    allow_local_endpoints: bool = False
    frontend_dist: Path = CLOUD_ROOT / "frontend" / "dist"

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def gateway_url(self) -> str:
        parsed = urlparse(self.public_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}/gateway/ws"


def _flag(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from error
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


def _argv(env: Mapping[str, str], name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """A command line: a JSON array, or a shell-quoted string."""
    raw = env.get(name, "").strip()
    if not raw:
        return default
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ConfigError(f"{name} is not a valid JSON array") from error
        if not all(isinstance(item, str) for item in parsed):
            raise ConfigError(f"{name} must be an array of strings")
        return tuple(parsed)
    return tuple(shlex.split(raw))


def default_kvist_command() -> tuple[str, ...]:
    """The repository's own core, run from its virtual environment when there is one."""
    repo_python = REPO_ROOT / ".venv" / "bin" / "python"
    python = str(repo_python) if repo_python.exists() else sys.executable
    return (python, "-m", "kernel")


def default_runner_command() -> tuple[str, ...]:
    return (sys.executable, "-m", "kvist_runner")


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    data_dir = Path(env.get("KVIST_CLOUD_DATA_DIR", "") or CLOUD_ROOT / ".cloud-data").resolve()
    public_url = (env.get("PUBLIC_URL", "") or "http://127.0.0.1:8740").rstrip("/")
    session_secret = env.get("KVIST_CLOUD_SESSION_SECRET", "")
    master_key = env.get("KVIST_CLOUD_MASTER_KEY", "")
    if len(session_secret) < 32:
        raise ConfigError("KVIST_CLOUD_SESSION_SECRET must be set (at least 32 characters)")
    if not master_key:
        raise ConfigError("KVIST_CLOUD_MASTER_KEY must be set (a Fernet key)")
    auth_dev_login = _flag(env, "AUTH_DEV_LOGIN")
    host = urlparse(public_url).hostname or ""
    if auth_dev_login and host not in LOOPBACK_HOSTS:
        raise ConfigError("AUTH_DEV_LOGIN is only allowed when PUBLIC_URL is a loopback address")
    billing_enabled = _flag(env, "BILLING_ENABLED")
    if billing_enabled and not env.get("PADDLE_WEBHOOK_SECRET", ""):
        raise ConfigError("BILLING_ENABLED needs PADDLE_WEBHOOK_SECRET")
    allow_local = _flag(env, "ALLOW_LOCAL_ENDPOINTS")
    if allow_local and host not in LOOPBACK_HOSTS:
        raise ConfigError("ALLOW_LOCAL_ENDPOINTS is only allowed when PUBLIC_URL is a loopback address")
    admins = frozenset(
        login.strip().lower() for login in env.get("ADMIN_GITHUB_LOGINS", "").split(",") if login.strip()
    )
    return Settings(
        data_dir=data_dir,
        database_url=env.get("DATABASE_URL", "") or f"sqlite:///{data_dir / 'cloud.db'}",
        public_url=public_url,
        session_secret=session_secret,
        master_key=master_key,
        github_client_id=env.get("GITHUB_CLIENT_ID", ""),
        github_client_secret=env.get("GITHUB_CLIENT_SECRET", ""),
        auth_dev_login=auth_dev_login,
        billing_enabled=billing_enabled,
        paddle_webhook_secret=env.get("PADDLE_WEBHOOK_SECRET", ""),
        paddle_api_key=env.get("PADDLE_API_KEY", ""),
        paddle_client_token=env.get("PADDLE_CLIENT_TOKEN", ""),
        paddle_environment=env.get("PADDLE_ENVIRONMENT", "sandbox"),
        paddle_price_start=env.get("PADDLE_PRICE_START", ""),
        paddle_price_pro=env.get("PADDLE_PRICE_PRO", ""),
        paddle_price_max=env.get("PADDLE_PRICE_MAX", ""),
        paddle_price_hours=env.get("PADDLE_PRICE_HOURS", ""),
        machine_provider=env.get("MACHINE_PROVIDER", "local"),
        runner_command=_argv(env, "RUNNER_COMMAND", default_runner_command()),
        kvist_command=_argv(env, "KVIST_COMMAND", default_kvist_command()),
        heartbeat_seconds=_number(env, "HEARTBEAT_SECONDS", 15.0),
        heartbeat_max_gap_seconds=_number(env, "HEARTBEAT_MAX_GAP_SECONDS", 45.0),
        machine_idle_sleep_seconds=_number(env, "MACHINE_IDLE_SLEEP_SECONDS", 900.0),
        provider_outage_seconds=_number(env, "PROVIDER_OUTAGE_SECONDS", 300.0),
        hours_grace_seconds=_number(env, "HOURS_GRACE_SECONDS", 300.0),
        admin_logins=admins,
        allow_local_endpoints=allow_local,
        frontend_dist=Path(env.get("FRONTEND_DIST", "") or CLOUD_ROOT / "frontend" / "dist"),
    )
