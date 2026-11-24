# -*- coding: utf-8 -*-
"""The first message of a task when it carries photos: it waits here for the machine, the rest of the task's text stays small

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-25 02:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Empty by default: the release that is still live never writes it, and its rows start from the goal as before.
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("start_envelope", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("start_envelope")
