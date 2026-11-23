# -*- coding: utf-8 -*-
"""Whether the text waiting for a task's next run is a slash command for Kvist rather than a message

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-23 20:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A default of false: the release that is still live never writes it, and its rows stay messages.
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("pending_command", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("pending_command")
