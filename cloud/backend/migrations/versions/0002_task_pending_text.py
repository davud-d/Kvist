# -*- coding: utf-8 -*-
"""a task is a session: the message waiting to be delivered to the machine

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06 13:10:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("pending_text", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("tasks", "pending_text")
