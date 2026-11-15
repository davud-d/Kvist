# -*- coding: utf-8 -*-
"""A name the person gives a task

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-15 03:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Added with a default, so the release that is still live keeps working on the migrated database.
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("title", sa.String(120), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("title")
