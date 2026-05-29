"""add_separating_to_jobstatus

Revision ID: a1b2c3d4e5f6
Revises: 31e7dad3f86a
Create Date: 2026-05-28 11:30:00.000000

Adds the SEPARATING value to the PostgreSQL jobstatus enum.
This is needed for the Demucs source-separation pipeline stage
that runs between audio extraction and speaker diarization.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '31e7dad3f86a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Check current values — avoid duplicate-value errors on re-run
        result = bind.execute(sa.text("SELECT enum_range(NULL::jobstatus)"))
        enum_range_str = result.scalar() or ""

        if "SEPARATING" not in enum_range_str:
            # ADD VALUE supports AFTER in PostgreSQL 9.1+
            # On Neon (PG 16) this is safe inside a transaction
            op.execute("ALTER TYPE jobstatus ADD VALUE 'SEPARATING' AFTER 'EXTRACTING'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    # Downgrade is a no-op — the value just becomes unused.
    pass
