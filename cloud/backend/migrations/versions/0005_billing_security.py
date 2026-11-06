# -*- coding: utf-8 -*-
"""Server-owned checkout and payment evidence, GitHub age and lifetime trial use."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paddle_events", sa.Column("payload", sa.JSON(), nullable=True))
    op.add_column("subscriptions", sa.Column("provider_updated_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("github_created_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("trial_used_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE users SET trial_used_at = created_at WHERE id IN "
               "(SELECT user_id FROM subscriptions WHERE trial_ends_at IS NOT NULL)")
    # The previous flow trusted browser metadata; it is not payment or ownership evidence.
    op.execute("UPDATE subscriptions SET status = 'past_due' WHERE paddle_subscription_id <> ''")
    op.create_table(
        "billing_checkouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("price_id", sa.String(100), nullable=False),
        sa.Column("transaction_id", sa.String(100), unique=True),
        sa.Column("subscription_id", sa.String(100), unique=True),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("purchase_entry_id", sa.Integer()),
        sa.Column("refund_id", sa.String(100)),
        sa.Column("refunded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_billing_checkouts_user_id", "billing_checkouts", ["user_id"])
    op.create_table(
        "billing_payments",
        sa.Column("transaction_id", sa.String(100), primary_key=True),
        sa.Column("subscription_id", sa.String(100), nullable=False),
        sa.Column("price_id", sa.String(100), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_billing_payments_subscription_id", "billing_payments", ["subscription_id"])


def downgrade() -> None:
    op.drop_column("paddle_events", "payload")
    op.drop_column("subscriptions", "provider_updated_at")
    op.drop_table("billing_payments")
    op.drop_table("billing_checkouts")
    op.drop_column("users", "trial_used_at")
    op.drop_column("users", "github_created_at")
