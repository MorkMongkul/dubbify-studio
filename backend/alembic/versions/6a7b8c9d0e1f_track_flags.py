"""track_flags

Revision ID: 6a7b8c9d0e1f
Revises: 5f6a7b8c9d0e
Create Date: 2026-10-24 12:00:00.000000

Each voice track's switches — lock, hide, mute — like CapCut's track headers
(jobs.track_flags, JSON). Installed desktop apps get the column at startup from
init_db's add_missing_columns.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '6a7b8c9d0e1f'
down_revision: Union[str, Sequence[str], None] = '5f6a7b8c9d0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "track_flags" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("jobs")}:
        op.add_column("jobs", sa.Column("track_flags", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "track_flags")
