"""add_stems_ready_to_jobstatus

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-28 12:00:00.000000

Adds STEMS_READY to the PostgreSQL jobstatus enum.
This is the "paused" state after demucs separation completes —
the pipeline waits for the user to click Analyze before continuing.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        result = bind.execute(sa.text("SELECT enum_range(NULL::jobstatus)"))
        enum_range_str = result.scalar() or ""

        if "STEMS_READY" not in enum_range_str:
            op.execute("ALTER TYPE jobstatus ADD VALUE 'STEMS_READY' AFTER 'SEPARATING'")


def downgrade() -> None:
    pass  # PostgreSQL does not support removing enum values
