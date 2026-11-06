# -*- coding: utf-8 -*-
"""a session's own id for its address: opaque, unique, not the row number

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06 16:00:00.000000
"""
from __future__ import annotations

import secrets

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("public_id", sa.String(length=24), nullable=True))
    connection = op.get_bind()
    tasks = sa.table("tasks", sa.column("id", sa.Integer), sa.column("public_id", sa.String))
    for (task_id,) in connection.execute(sa.select(tasks.c.id)).fetchall():
        connection.execute(tasks.update().where(tasks.c.id == task_id).values(public_id=secrets.token_urlsafe(9)))
    op.create_index("ix_tasks_public_id", "tasks", ["public_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tasks_public_id", table_name="tasks")
    op.drop_column("tasks", "public_id")
