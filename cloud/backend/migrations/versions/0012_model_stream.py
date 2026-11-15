# -*- coding: utf-8 -*-
"""Whether the customer's own model endpoint is asked to stream its answers (on by default)

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-15 04:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Added with a default, so the release that is still live keeps working on the migrated database.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("model_stream", sa.Boolean(), nullable=False, server_default="1"))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("model_stream")
