# -*- coding: utf-8 -*-
"""projects: a named folder on the person's machine that tasks (sessions) work in

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06 15:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("folder", sa.String(length=60), nullable=False),
        sa.Column("folder_key", sa.String(length=60), nullable=False),
        sa.Column("repo_url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "folder_key"),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])
    op.add_column("tasks", sa.Column("project_id", sa.Integer(), nullable=True))
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_column("tasks", "project_id")
    op.drop_index("ix_projects_user_id", table_name="projects")
    op.drop_table("projects")
