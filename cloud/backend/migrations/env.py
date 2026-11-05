# -*- coding: utf-8 -*-
"""Alembic environment. The URL comes from the caller (db.upgrade) or DATABASE_URL."""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine

from kvist_cloud import models  # noqa: F401  registers the tables
from kvist_cloud.db import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("no database URL: pass one to db.upgrade() or set DATABASE_URL")
    return url


def run_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
