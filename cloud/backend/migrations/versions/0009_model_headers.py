# -*- coding: utf-8 -*-
"""Extra HTTP headers for the customer's own model endpoint (the "other" provider)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-14 15:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Added with defaults, so the release that is still live keeps working on the migrated database.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("model_headers_enc", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("model_header_names", sa.String(600), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("model_header_names")
        batch.drop_column("model_headers_enc")
