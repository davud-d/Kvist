# -*- coding: utf-8 -*-
"""Sign in with Apple: an account may belong to an Apple user, with or without GitHub

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-28 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("apple_sub", sa.String(64), nullable=True))
        batch.add_column(sa.Column("apple_token_enc", sa.Text(), nullable=False, server_default=""))
        batch.create_unique_constraint("uq_users_apple_sub", ["apple_sub"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_apple_sub", type_="unique")
        batch.drop_column("apple_token_enc")
        batch.drop_column("apple_sub")
