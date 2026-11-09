# -*- coding: utf-8 -*-
"""Admin panel: rights and bans on users, suspended machines, the bell, the audit trail

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-10 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("ban_until", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("ban_permanent", sa.Boolean(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("ban_reason", sa.String(300), nullable=False, server_default=""))
    with op.batch_alter_table("machines") as batch:
        batch.add_column(sa.Column("suspended", sa.Boolean(), nullable=False, server_default="0"))
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("admin_actions")
    op.drop_table("notifications")
    with op.batch_alter_table("machines") as batch:
        batch.drop_column("suspended")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("ban_reason")
        batch.drop_column("ban_permanent")
        batch.drop_column("ban_until")
        batch.drop_column("is_admin")
