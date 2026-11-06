"""add diary entries and snoozes

Revision ID: 3f9a7c2d1b4e
Revises: fcbb7ca1bdf3
Create Date: 2026-11-06 19:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3f9a7c2d1b4e'
down_revision: Union[str, None] = 'fcbb7ca1bdf3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'diary_entries',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_diary_entries_user_date',
        'diary_entries',
        ['telegram_user_id', 'entry_date', 'created_at'],
    )
    op.create_table(
        'diary_snoozes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
        sa.Column('entry_date', sa.Date(), nullable=False),
        sa.Column('remind_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_diary_snoozes_user_due',
        'diary_snoozes',
        ['telegram_user_id', 'sent_at', 'remind_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_diary_snoozes_user_due', table_name='diary_snoozes')
    op.drop_table('diary_snoozes')
    op.drop_index('ix_diary_entries_user_date', table_name='diary_entries')
    op.drop_table('diary_entries')
