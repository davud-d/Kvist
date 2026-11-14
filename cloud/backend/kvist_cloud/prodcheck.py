# -*- coding: utf-8 -*-
"""Refuses a configuration that is fine on a laptop and wrong in production.

``python -m kvist_cloud.prodcheck [--env-file PATH]`` exits 1 on any problem.
It prints names and reasons only, never a value.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from .config import ConfigError, Settings, load_settings, read_env_file
from .secrets import SecretError, Vault


def problems(settings: Settings) -> list[str]:
    found: list[str] = []
    if urlparse(settings.public_url).scheme != "https":
        found.append("PUBLIC_URL must be https:// (session cookies are only marked secure on https)")
    if settings.machine_provider == "local":
        found.append("MACHINE_PROVIDER=local has no tenant isolation; use a real provider")
    if not settings.database_url.startswith("postgresql"):
        found.append("DATABASE_URL must be PostgreSQL (SQLite is for development)")
    if not (settings.github_client_id and settings.github_client_secret):
        found.append("GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are not both set: nobody can sign in")
    if not settings.admin_logins:
        found.append("ADMIN_GITHUB_LOGINS is empty: nobody can open /admin to handle disputes or bans")
    if settings.auth_dev_login or settings.allow_local_endpoints:
        found.append("AUTH_DEV_LOGIN / ALLOW_LOCAL_ENDPOINTS must not be set")
    try:
        keys = Vault(settings.master_key)
    except SecretError as error:
        found.append(str(error))
    else:
        probe = keys.encrypt("probe")
        if keys.decrypt(probe) != "probe":
            found.append("the master key does not round-trip")
    return found


def warnings(settings: Settings) -> list[str]:
    found: list[str] = []
    if not settings.billing_enabled:
        found.append("BILLING_ENABLED is off: nobody can pay")
    if "," in settings.master_key:
        found.append("KVIST_CLOUD_MASTER_KEY lists several keys: a rotation is in progress; drop the old ones after")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", help="the server's env file (default: KVIST_CLOUD_ENV_FILE or cloud/.env)")
    args = parser.parse_args(argv)
    try:
        # Explicit, so a named file is used as the server would use it without touching this process's environment.
        settings = load_settings({**read_env_file(Path(args.env_file)), **os.environ} if args.env_file else None)
    except ConfigError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    for line in warnings(settings):
        print(f"warning: {line}", file=sys.stderr)
    found = problems(settings)
    for line in found:
        print(f"FAIL: {line}", file=sys.stderr)
    if not found:
        print("ok: the configuration is fit for production")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
