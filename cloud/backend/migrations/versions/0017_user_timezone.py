# -*- coding: utf-8 -*-
"""The person's own time zone, so Kvist knows their hour and not the server's

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-26 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Empty by default: the release that is still live never writes it.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("timezone", sa.String(64), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("timezone")
