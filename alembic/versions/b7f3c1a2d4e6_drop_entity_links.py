"""drop entity links (dead feature: no creation path in UI or agent tools)

Revision ID: b7f3c1a2d4e6
Revises: aa624e0c3fc4
Create Date: 2026-10-17 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'b7f3c1a2d4e6'
down_revision: Union[str, None] = 'aa624e0c3fc4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Таблица entity_links всегда была пустой: создать связь было неоткуда
    # (нет UI, нет инструмента агента) — фича удаляется вместе с таблицей.
    op.drop_table('entity_links')


def downgrade() -> None:
    # Восстановление структуры (данные не восстановимы — их и не было).
    import sqlalchemy as sa

    op.create_table(
        'entity_links',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('from_type', sa.Enum(
            'task', 'knowledge', 'document', 'project', 'secret', 'category',
            name='entity_type', native_enum=False, length=32, create_constraint=False,
        ), nullable=False),
        sa.Column('from_id', sa.Uuid(), nullable=False),
        sa.Column('to_type', sa.Enum(
            'task', 'knowledge', 'document', 'project', 'secret', 'category',
            name='entity_type', native_enum=False, length=32, create_constraint=False,
        ), nullable=False),
        sa.Column('to_id', sa.Uuid(), nullable=False),
        sa.Column('relation', sa.Enum(
            'relates_to', 'part_of', 'depends_on', 'documents', 'decided_by', 'refers_to',
            name='relation', native_enum=False, length=32, create_constraint=False,
        ), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('from_type', 'from_id', 'to_type', 'to_id', 'relation',
                            name='uq_entity_links_pair'),
    )
    op.create_index('ix_entity_links_from', 'entity_links', ['from_type', 'from_id'])
    op.create_index('ix_entity_links_to', 'entity_links', ['to_type', 'to_id'])
