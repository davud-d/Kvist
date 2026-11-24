# -*- coding: utf-8 -*-
"""Settings, read from the environment and nowhere else.

Secrets are fields with ``repr=False`` so an exception or a log line that prints the settings
object never carries them.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

CLOUD_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = CLOUD_ROOT.parent
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
ENV_FILE_NAME = ".env"
# Platega's PaymentMethodInt: 2 SBP QR, 3 ERIP, 11 card acquiring, 12 international, 13 crypto, 14 Sberpay.
PLATEGA_METHODS = frozenset({2, 3, 11, 12, 13, 14})
PLATEGA_DEFAULT_METHODS = (2, 11, 12, 13)
DEFAULT_WHEELS_DIR = Path("/opt/kvist-cloud/wheels")
PLATEGA_PRICE_KEYS = ("start", "pro", "max", "hours", "remote")
# What the phone's own wallets (Google Pay, Apple Pay) can buy, and the currencies whose amounts have two decimals.
WALLET_KINDS = ("start", "pro", "max", "hours")
WALLET_CURRENCIES = frozenset({"USD", "EUR", "GBP"})


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WalletSettings:
    """Payments made with the phone's own wallet; the processor is Stripe. None in Settings means switched off."""

    stripe_secret_key: str = field(repr=False)
    stripe_publishable_key: str
    webhook_secret: str = field(repr=False)
    base_url: str = "https://api.stripe.com"
    currency: str = "USD"
    # What each purchase costs in ``currency``; the server alone decides amounts.
    prices: Mapping[str, Decimal] = field(default_factory=dict)
    # The country of the account that receives the money (the sheets show it and the card networks ask for it).
    country: str = "US"
    merchant_name: str = "Kvist Cloud"
    google_environment: str = "TEST"
    google_merchant_id: str = ""
    # The Merchant ID made in the Apple developer account, e.g. merchant.site.kvist.cloud. Empty: no Apple Pay.
    apple_merchant_id: str = ""


@dataclass(frozen=True, slots=True)
class FreedomPaySettings:
    """Freedom Pay's hosted payment page (Merchant API, docs.freedompay.kz). None in Settings means switched off."""

    merchant_id: str
    secret: str = field(repr=False)
    base_url: str = "https://api.freedompay.kg"
    currency: str = "KGS"
    # What each purchase costs in ``currency``; the server alone decides amounts.
    prices: Mapping[str, Decimal] = field(default_factory=dict)
    # pg_testing_mode=1: Freedom Pay's test cards work and nothing is charged.
    testing: bool = False


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
    platega_merchant_id: str = field(default="", repr=False)
    platega_secret: str = field(default="", repr=False)
    platega_base_url: str = "https://app.platega.io"
    platega_currency: str = "RUB"
    # The ways a person may pay, in the order the picker shows them; the browser can only choose among these.
    platega_payment_methods: tuple[int, ...] = PLATEGA_DEFAULT_METHODS
    # What each purchase costs in ``platega_currency``; the server alone decides amounts.
    platega_prices: Mapping[str, Decimal] = field(default_factory=dict)
    wallet: WalletSettings | None = None
    freedompay: FreedomPaySettings | None = None
    machine_provider: str = "local"
    runner_command: tuple[str, ...] = ()
    kvist_command: tuple[str, ...] = ()
    heartbeat_seconds: float = 15.0
    heartbeat_max_gap_seconds: float = 45.0
    machine_idle_sleep_seconds: float = 900.0
    # Where the wheels of Kvist and the runner are (the ones customers' VMs are built from), and how long a machine
    # must be left alone before it is brought up to date in place (updates.py). No directory: no in-place updates.
    wheels_dir: Path | None = None
    update_idle_seconds: float = 1800.0
    provider_outage_seconds: float = 300.0
    hours_grace_seconds: float = 300.0
    # "Sign in with ChatGPT" is off until OpenAI agrees to a hosted agent: many customers' plans used from one
    # server address is what their abuse protection looks for, and it is the customers' accounts that get banned.
    chatgpt_signin: bool = False
    # How often the saved ChatGPT sign-ins are used once, so an expired one is noticed before a task needs it.
    chatgpt_check_seconds: float = 86400.0
    admin_logins: frozenset[str] = frozenset()
    # Dev only: lets a model endpoint or a repository live on this computer.
    allow_local_endpoints: bool = False
    frontend_dist: Path = CLOUD_ROOT / "frontend" / "dist"

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def dev_billing(self) -> bool:
        return (not self.billing_enabled and self.auth_dev_login
                and urlparse(self.public_url).hostname in LOOPBACK_HOSTS)

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


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines of a .env file; a missing file is not an error, a malformed line is."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.removeprefix("export ").partition("=")
        name, value = name.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ConfigError(f"{path.name} line {number} is not NAME=value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def _environment() -> dict[str, str]:
    """The process environment over the .env file: a real variable always wins."""
    path = Path(os.environ.get("KVIST_CLOUD_ENV_FILE", "") or CLOUD_ROOT / ENV_FILE_NAME)
    return {**read_env_file(path), **os.environ}


def _platega(env: Mapping[str, str], enabled: bool) -> tuple[str, str, str, str, tuple[int, ...], dict[str, Decimal]]:
    merchant, secret = env.get("PLATEGA_MERCHANT_ID", "").strip(), env.get("PLATEGA_SECRET", "").strip()
    base_url = (env.get("PLATEGA_BASE_URL", "") or "https://app.platega.io").rstrip("/")
    currency = (env.get("PLATEGA_CURRENCY", "") or "RUB").strip().upper()
    raw_methods = env.get("PLATEGA_PAYMENT_METHODS", "").strip()
    try:
        methods = tuple(dict.fromkeys(int(item) for item in raw_methods.split(",") if item.strip()))
    except ValueError as error:
        raise ConfigError("PLATEGA_PAYMENT_METHODS must be comma-separated numbers") from error
    methods = methods or PLATEGA_DEFAULT_METHODS
    if not set(methods) <= PLATEGA_METHODS:
        raise ConfigError(f"PLATEGA_PAYMENT_METHODS must be among {sorted(PLATEGA_METHODS)}")
    if not base_url.startswith("https://"):
        raise ConfigError("PLATEGA_BASE_URL must be https")
    prices: dict[str, Decimal] = {}
    for key in PLATEGA_PRICE_KEYS:
        raw = env.get(f"PLATEGA_PRICE_{key.upper()}", "").strip()
        if not raw:
            continue
        try:
            amount = Decimal(raw)
        except InvalidOperation as error:
            raise ConfigError(f"PLATEGA_PRICE_{key.upper()} must be a number, got {raw!r}") from error
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise ConfigError(f"PLATEGA_PRICE_{key.upper()} must be positive with at most 2 decimals")
        prices[key] = amount.quantize(Decimal("0.01"))
    if enabled:
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", merchant):
            raise ConfigError("BILLING_ENABLED needs PLATEGA_MERCHANT_ID (a UUID)")
        if not secret:
            raise ConfigError("BILLING_ENABLED needs PLATEGA_SECRET")
        missing = [f"PLATEGA_PRICE_{key.upper()}" for key in PLATEGA_PRICE_KEYS if key not in prices]
        if missing:
            raise ConfigError("BILLING_ENABLED needs " + ", ".join(missing))
    return merchant, secret, base_url, currency, methods, prices


def _wallet(env: Mapping[str, str]) -> WalletSettings | None:
    if not _flag(env, "WALLET_ENABLED"):
        return None
    secret = env.get("STRIPE_SECRET_KEY", "").strip()
    publishable = env.get("STRIPE_PUBLISHABLE_KEY", "").strip()
    webhook = env.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not re.fullmatch(r"(sk|rk)_(test|live)_\w+", secret):
        raise ConfigError("WALLET_ENABLED needs STRIPE_SECRET_KEY (sk_… or rk_…)")
    if not re.fullmatch(r"pk_(test|live)_\w+", publishable):
        raise ConfigError("WALLET_ENABLED needs STRIPE_PUBLISHABLE_KEY (pk_…)")
    if not webhook.startswith("whsec_"):
        raise ConfigError("WALLET_ENABLED needs STRIPE_WEBHOOK_SECRET (whsec_…)")
    # A live key and a test key of the same account are one mistake away from each other: refuse the mix.
    if (secret.split("_")[1], publishable.split("_")[1]) not in {("test", "test"), ("live", "live")}:
        raise ConfigError("STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY must both be test or both be live keys")
    base_url = (env.get("STRIPE_BASE_URL", "") or "https://api.stripe.com").rstrip("/")
    if not base_url.startswith("https://"):
        raise ConfigError("STRIPE_BASE_URL must be https")
    currency = (env.get("WALLET_CURRENCY", "") or "USD").strip().upper()
    if currency not in WALLET_CURRENCIES:
        raise ConfigError(f"WALLET_CURRENCY must be one of {sorted(WALLET_CURRENCIES)}")
    from . import plans

    prices: dict[str, Decimal] = {}
    for key in WALLET_KINDS:
        default = plans.HOUR_PACK_PRICE_USD if key == "hours" else plans.get_plan(key).price_usd
        raw = env.get(f"WALLET_PRICE_{key.upper()}", "").strip() or str(default)
        try:
            amount = Decimal(raw)
        except InvalidOperation as error:
            raise ConfigError(f"WALLET_PRICE_{key.upper()} must be a number, got {raw!r}") from error
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise ConfigError(f"WALLET_PRICE_{key.upper()} must be positive with at most 2 decimals")
        prices[key] = amount.quantize(Decimal("0.01"))
    environment = (env.get("GOOGLE_PAY_ENVIRONMENT", "") or "TEST").strip().upper()
    if environment not in {"TEST", "PRODUCTION"}:
        raise ConfigError("GOOGLE_PAY_ENVIRONMENT must be TEST or PRODUCTION")
    google_merchant = env.get("GOOGLE_PAY_MERCHANT_ID", "").strip()
    if environment == "PRODUCTION" and not google_merchant:
        raise ConfigError("GOOGLE_PAY_ENVIRONMENT=PRODUCTION needs GOOGLE_PAY_MERCHANT_ID")
    country = (env.get("WALLET_COUNTRY", "") or "US").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ConfigError("WALLET_COUNTRY must be a two-letter country code")
    return WalletSettings(
        stripe_secret_key=secret, stripe_publishable_key=publishable, webhook_secret=webhook, base_url=base_url,
        currency=currency, prices=prices, country=country,
        merchant_name=(env.get("WALLET_MERCHANT_NAME", "") or "Kvist Cloud").strip()[:60],
        google_environment=environment, google_merchant_id=google_merchant,
        apple_merchant_id=env.get("APPLE_PAY_MERCHANT_ID", "").strip(),
    )


def _freedompay(env: Mapping[str, str]) -> FreedomPaySettings | None:
    if not _flag(env, "FREEDOMPAY_ENABLED"):
        return None
    merchant, secret = env.get("FREEDOMPAY_MERCHANT_ID", "").strip(), env.get("FREEDOMPAY_SECRET", "").strip()
    if not merchant.isdigit():
        raise ConfigError("FREEDOMPAY_ENABLED needs FREEDOMPAY_MERCHANT_ID (a number)")
    if not secret:
        raise ConfigError("FREEDOMPAY_ENABLED needs FREEDOMPAY_SECRET (the payment secret key)")
    base_url = (env.get("FREEDOMPAY_BASE_URL", "") or "https://api.freedompay.kg").rstrip("/")
    if not base_url.startswith("https://"):
        raise ConfigError("FREEDOMPAY_BASE_URL must be https")
    currency = (env.get("FREEDOMPAY_CURRENCY", "") or "KGS").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ConfigError("FREEDOMPAY_CURRENCY must be an ISO 4217 code")
    prices: dict[str, Decimal] = {}
    for key in WALLET_KINDS:
        raw = env.get(f"FREEDOMPAY_PRICE_{key.upper()}", "").strip()
        if not raw:
            raise ConfigError(f"FREEDOMPAY_ENABLED needs FREEDOMPAY_PRICE_{key.upper()}")
        try:
            amount = Decimal(raw)
        except InvalidOperation as error:
            raise ConfigError(f"FREEDOMPAY_PRICE_{key.upper()} must be a number, got {raw!r}") from error
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise ConfigError(f"FREEDOMPAY_PRICE_{key.upper()} must be positive with at most 2 decimals")
        prices[key] = amount.quantize(Decimal("0.01"))
    return FreedomPaySettings(merchant_id=merchant, secret=secret, base_url=base_url, currency=currency,
                              prices=prices, testing=_flag(env, "FREEDOMPAY_TESTING"))


def default_kvist_command() -> tuple[str, ...]:
    """The repository's own core, run from its virtual environment when there is one."""
    repo_python = REPO_ROOT / ".venv" / "bin" / "python"
    python = str(repo_python) if repo_python.exists() else sys.executable
    return (python, "-m", "kernel")


def default_runner_command() -> tuple[str, ...]:
    return (sys.executable, "-m", "kvist_runner")


def _wheels_dir(env: Mapping[str, str]) -> Path | None:
    """The same directory the VM host builds its disk image from (``deploy/systemd/kvist-vm@.service``)."""
    if env.get("WHEELS_DIR"):
        return Path(env["WHEELS_DIR"])
    return DEFAULT_WHEELS_DIR if DEFAULT_WHEELS_DIR.is_dir() else None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = _environment() if env is None else env
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
    merchant, secret, platega_url, currency, methods, prices = _platega(env, billing_enabled)
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
        platega_merchant_id=merchant,
        platega_secret=secret,
        platega_base_url=platega_url,
        platega_currency=currency,
        platega_payment_methods=methods,
        platega_prices=prices,
        wallet=_wallet(env),
        freedompay=_freedompay(env),
        machine_provider=env.get("MACHINE_PROVIDER", "local"),
        runner_command=_argv(env, "RUNNER_COMMAND", default_runner_command()),
        kvist_command=_argv(env, "KVIST_COMMAND", default_kvist_command()),
        heartbeat_seconds=_number(env, "HEARTBEAT_SECONDS", 15.0),
        heartbeat_max_gap_seconds=_number(env, "HEARTBEAT_MAX_GAP_SECONDS", 45.0),
        machine_idle_sleep_seconds=_number(env, "MACHINE_IDLE_SLEEP_SECONDS", 900.0),
        wheels_dir=_wheels_dir(env),
        update_idle_seconds=_number(env, "UPDATE_IDLE_SECONDS", 1800.0),
        provider_outage_seconds=_number(env, "PROVIDER_OUTAGE_SECONDS", 300.0),
        hours_grace_seconds=_number(env, "HOURS_GRACE_SECONDS", 300.0),
        chatgpt_signin=_flag(env, "CHATGPT_SIGNIN"),
        chatgpt_check_seconds=_number(env, "CHATGPT_CHECK_SECONDS", 86400.0),
        admin_logins=admins,
        allow_local_endpoints=allow_local,
        frontend_dist=Path(env.get("FRONTEND_DIST", "") or CLOUD_ROOT / "frontend" / "dist"),
    )
