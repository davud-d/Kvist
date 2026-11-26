# -*- coding: utf-8 -*-
"""Web searches made with the cloud's shared key, counted per account, and bought searches

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-26 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "search_uses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("paid", sa.Boolean(), nullable=False, server_default="0"),
    )
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("search_credits", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("search_credits")
    op.drop_table("search_uses")
