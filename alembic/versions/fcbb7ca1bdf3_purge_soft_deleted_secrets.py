"""purge soft deleted secrets

Revision ID: fcbb7ca1bdf3
Revises: b7f3c1a2d4e6
Create Date: 2026-10-18 22:21:17.163084

Удаление секретов стало жёстким (строка стирается вместе с шифротекстом).
Строки, удалённые мягко до этого изменения, остались в БД и недостижимы из
приложения — чистим их, чтобы значение не жило в базе и бэкапах.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = 'fcbb7ca1bdf3'
down_revision: Union[str, None] = 'b7f3c1a2d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM secrets WHERE deleted_at IS NOT NULL")


def downgrade() -> None:
    # Данные не восстанавливаются: мягко удалённые секреты стираются намеренно.
    pass
