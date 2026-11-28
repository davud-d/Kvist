# -*- coding: utf-8 -*-
"""An account can be deleted by its owner: erased 90 days after the request, the row a tombstone for payment records

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-28 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("github_id", existing_type=sa.BigInteger(), nullable=True)
        batch.add_column(sa.Column("delete_requested_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("trial_github_id", sa.BigInteger(), nullable=True))
        batch.create_index("ix_users_trial_github_id", ["trial_github_id"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_trial_github_id")
        batch.drop_column("trial_github_id")
        batch.drop_column("deleted_at")
        batch.drop_column("delete_requested_at")
        batch.alter_column("github_id", existing_type=sa.BigInteger(), nullable=False)
