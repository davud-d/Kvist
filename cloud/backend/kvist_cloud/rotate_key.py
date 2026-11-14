# -*- coding: utf-8 -*-
"""Re-encrypts every stored secret under the first master key.

Rotation: put the new key in front of the old one in KVIST_CLOUD_MASTER_KEY (``new,old``), run this, restart the
server, and only then drop the old key. ``python -m kvist_cloud.rotate_key [--env-file PATH] [--dry-run]``.

All rows change in one transaction, or none do: a secret that no listed key can open aborts the run untouched.
It prints counts and row ids, never a secret.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import ConfigError, load_settings, read_env_file
from .db import make_engine, make_session_factory, session_scope
from .models import ChatGPTLogin, User

# (model, column) of every value that goes through secrets.Vault.
ENCRYPTED_COLUMNS = (
    (User, "github_token_enc"),
    (User, "model_key_enc"),
    (User, "chatgpt_enc"),
    (ChatGPTLogin, "verifier_enc"),
)


@dataclass(slots=True)
class Outcome:
    rotated: int = 0
    already_current: int = 0
    unreadable: list[str] = field(default_factory=list)


def _fernets(master_key: str) -> tuple[Fernet, MultiFernet]:
    keys = [Fernet(part.strip().encode()) for part in master_key.split(",") if part.strip()]
    if len(keys) < 2:
        raise ConfigError("KVIST_CLOUD_MASTER_KEY must list the new key first and the old one after it (new,old)")
    return keys[0], MultiFernet(keys)


def rotate(db: Session, master_key: str, dry_run: bool) -> Outcome:
    primary, every_key = _fernets(master_key)
    outcome = Outcome()
    for model, column in ENCRYPTED_COLUMNS:
        for row in db.scalars(select(model)):
            token = getattr(row, column)
            if not token:
                continue
            label = f"{model.__tablename__}.{column} id={row.id}"
            try:
                primary.decrypt(token.encode("ascii"))
                outcome.already_current += 1
                continue
            except InvalidToken:
                pass
            try:
                fresh = every_key.rotate(token.encode("ascii")).decode("ascii")
            except InvalidToken:
                outcome.unreadable.append(label)
                continue
            outcome.rotated += 1
            if not dry_run:
                setattr(row, column, fresh)
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", help="the server's env file (default: KVIST_CLOUD_ENV_FILE or cloud/.env)")
    parser.add_argument("--dry-run", action="store_true", help="count what would change, change nothing")
    args = parser.parse_args(argv)
    try:
        settings = load_settings({**read_env_file(Path(args.env_file)), **os.environ} if args.env_file else None)
        engine = make_engine(settings.database_url)
        with session_scope(make_session_factory(engine)) as db:
            outcome = rotate(db, settings.master_key, args.dry_run)
            if outcome.unreadable:
                db.rollback()
    except ConfigError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if outcome.unreadable:
        print("FAIL: no listed key opens these; nothing was changed:", file=sys.stderr)
        for label in outcome.unreadable:
            print(f"  {label}", file=sys.stderr)
        return 1
    verb = "would be re-encrypted" if args.dry_run else "re-encrypted"
    print(f"ok: {outcome.rotated} secrets {verb}, {outcome.already_current} were already on the first key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
