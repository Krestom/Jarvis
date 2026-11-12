"""remove diary, projects, categories, chat log and unused task fields

Revision ID: 9c4f1e2a7b6d
Revises: 3f9a7c2d1b4e
Create Date: 2026-11-12 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "9c4f1e2a7b6d"
down_revision: Union[str, None] = "3f9a7c2d1b4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Дневник
    op.drop_index("ix_diary_snoozes_user_due", table_name="diary_snoozes")
    op.drop_table("diary_snoozes")
    op.drop_index("ix_diary_entries_user_date", table_name="diary_entries")
    op.drop_table("diary_entries")

    # Журнал чата (экспорт диалога)
    op.drop_index("ix_chat_messages_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_user_time", table_name="chat_messages")
    op.drop_table("chat_messages")

    # Задачи: категории, проекты, ручные группы, приоритет
    op.drop_index("ix_tasks_category", table_name="tasks")
    op.drop_index("ix_tasks_project", table_name="tasks")
    op.drop_index("ix_tasks_status_bucket", table_name="tasks")
    op.drop_column("tasks", "category_id")
    op.drop_column("tasks", "project_id")
    op.drop_column("tasks", "bucket")
    op.drop_column("tasks", "priority")
    op.create_index("ix_tasks_status", "tasks", ["status"], unique=False)

    # Знания
    op.drop_index("ix_knowledge_category", table_name="knowledge_items")
    op.drop_index("ix_knowledge_project", table_name="knowledge_items")
    op.drop_column("knowledge_items", "category_id")
    op.drop_column("knowledge_items", "project_id")

    # Документы
    op.drop_index("ix_documents_category", table_name="documents")
    op.drop_index("ix_documents_project", table_name="documents")
    op.drop_column("documents", "category_id")
    op.drop_column("documents", "project_id")

    # Секреты
    op.drop_column("secrets", "project_id")
    op.drop_column("secrets", "category_id")

    # Проекты и категории
    op.drop_index("ix_projects_category", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_categories_parent", table_name="categories")
    op.drop_table("categories")


def downgrade() -> None:
    # Восстанавливаем только схему; удалённые данные не возвращаются.
    op.create_table(
        "categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["categories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_categories_parent", "categories", ["parent_id"], unique=False)

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_projects_category", "projects", ["category_id"], unique=False)

    # Задачи
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.add_column("tasks", sa.Column("priority", sa.String(length=32), nullable=False, server_default="normal"))
    op.add_column("tasks", sa.Column("bucket", sa.String(length=32), nullable=True))
    op.add_column("tasks", sa.Column("category_id", sa.Uuid(), nullable=True))
    op.add_column("tasks", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(None, "tasks", "categories", ["category_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key(None, "tasks", "projects", ["project_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_tasks_category", "tasks", ["category_id"], unique=False)
    op.create_index("ix_tasks_project", "tasks", ["project_id"], unique=False)
    op.create_index("ix_tasks_status_bucket", "tasks", ["status", "bucket"], unique=False)

    # Знания
    op.add_column("knowledge_items", sa.Column("category_id", sa.Uuid(), nullable=True))
    op.add_column("knowledge_items", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        None, "knowledge_items", "categories", ["category_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        None, "knowledge_items", "projects", ["project_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_knowledge_category", "knowledge_items", ["category_id"], unique=False)
    op.create_index("ix_knowledge_project", "knowledge_items", ["project_id"], unique=False)

    # Документы
    op.add_column("documents", sa.Column("category_id", sa.Uuid(), nullable=True))
    op.add_column("documents", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        None, "documents", "categories", ["category_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        None, "documents", "projects", ["project_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_documents_category", "documents", ["category_id"], unique=False)
    op.create_index("ix_documents_project", "documents", ["project_id"], unique=False)

    # Секреты
    op.add_column("secrets", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.add_column("secrets", sa.Column("category_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        None, "secrets", "categories", ["category_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        None, "secrets", "projects", ["project_id"], ["id"], ondelete="SET NULL"
    )

    # Журнал чата
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_messages_user_time", "chat_messages", ["telegram_user_id", "created_at"]
    )
    op.create_index("ix_chat_messages_created", "chat_messages", ["created_at"])

    # Дневник
    op.create_table(
        "diary_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_diary_entries_user_date",
        "diary_entries",
        ["telegram_user_id", "entry_date", "created_at"],
    )
    op.create_table(
        "diary_snoozes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_diary_snoozes_user_due",
        "diary_snoozes",
        ["telegram_user_id", "sent_at", "remind_at"],
    )
