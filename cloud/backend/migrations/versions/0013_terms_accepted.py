# -*- coding: utf-8 -*-
"""When a user accepted the terms of service and the privacy policy

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-21 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable: the release that is still live never writes it and keeps working on the migrated database.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("terms_accepted_at")
