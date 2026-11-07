# -*- coding: utf-8 -*-
"""Sign in with ChatGPT: encrypted tokens on the user, and the short-lived login attempts

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-07 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("chatgpt_enc", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("chatgpt_state", sa.String(12), nullable=False, server_default=""))
        batch.add_column(sa.Column("chatgpt_email", sa.String(320), nullable=False, server_default=""))
        batch.add_column(sa.Column("chatgpt_checked_at", sa.DateTime(), nullable=True))
    op.create_table(
        "chatgpt_logins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("verifier_enc", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("redirect_to", sa.String(200), nullable=False, server_default="/onboarding"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chatgpt_logins")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("chatgpt_checked_at")
        batch.drop_column("chatgpt_email")
        batch.drop_column("chatgpt_state")
        batch.drop_column("chatgpt_enc")
