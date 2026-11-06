# -*- coding: utf-8 -*-
"""Paddle is gone, Platega is the payment provider

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-06 18:00:00.000000

Paddle was only ever run against its sandbox, so its checkout, payment and webhook-id tables hold
nothing worth carrying over; they are replaced by a single server-owned ``billing_checkouts``.
Subscriptions keep their rows and lose only the Paddle identifiers.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("billing_payments")
    op.drop_table("billing_checkouts")
    op.drop_table("paddle_events")
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_index("ix_subscriptions_paddle_subscription_id")
        batch.drop_column("paddle_subscription_id")
        batch.drop_column("paddle_customer_id")
        batch.drop_column("provider_updated_at")
    op.create_table(
        "billing_checkouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column("transaction_id", sa.String(36), unique=True),
        sa.Column("pay_url", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("subscription_row_id", sa.Integer()),
        sa.Column("ledger_entry_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime()),
    )
    op.create_index("ix_billing_checkouts_user_id", "billing_checkouts", ["user_id"])


def downgrade() -> None:
    raise RuntimeError("0006 replaced Paddle's tables and cannot be reversed")
