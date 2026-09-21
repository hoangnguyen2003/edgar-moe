"""Enforce the append-only registry contract in the database.

Revision ID: 20260921_0002
Revises: 20260806_0001
Create Date: 2026-09-21
"""

from collections.abc import Sequence

from alembic import op

from edgar_moe.forward.immutability import (
    append_only_statements,
    drop_append_only_statements,
)

revision: str = "20260921_0002"
down_revision: str | None = "20260806_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in append_only_statements(op.get_bind().dialect.name):
        op.execute(statement)


def downgrade() -> None:
    for statement in drop_append_only_statements(op.get_bind().dialect.name):
        op.execute(statement)
