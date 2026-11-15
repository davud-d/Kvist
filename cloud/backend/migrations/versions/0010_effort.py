# -*- coding: utf-8 -*-
"""How hard the customer's model thinks (Kvist's /effort level), kept per account

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-15 02:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Added with a default, so the release that is still live keeps working on the migrated database.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("effort", sa.String(8), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("effort")
