# -*- coding: utf-8 -*-
"""Which release of Kvist and the runner a machine runs, so an idle one can be brought up to date

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-24 14:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Empty: the machine runs what its disk image came with, which is not a numbered release.
    with op.batch_alter_table("machines") as batch:
        batch.add_column(sa.Column("runner_release", sa.String(32), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("machines") as batch:
        batch.drop_column("runner_release")
