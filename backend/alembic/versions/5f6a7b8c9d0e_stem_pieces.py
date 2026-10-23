"""stem_pieces

Revision ID: 5f6a7b8c9d0e
Revises: 4e5f6a7b8c9d
Create Date: 2026-10-24 12:00:00.000000

0.3.2: the music & effects and the original voices in pieces (stem_pieces).
A new table: installed desktop apps get it at startup from
Base.metadata.create_all (app/core/database.py init_db).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '5f6a7b8c9d0e'
down_revision: Union[str, Sequence[str], None] = '4e5f6a7b8c9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "stem_pieces" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "stem_pieces",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("stem", sa.String(length=10), nullable=False),
        sa.Column("start_time", sa.Float(), nullable=False),
        sa.Column("end_time", sa.Float(), nullable=False),
        sa.Column("volume_db", sa.Float(), nullable=True),
        sa.Column("fade_in_secs", sa.Float(), nullable=True),
        sa.Column("fade_out_secs", sa.Float(), nullable=True),
        sa.Column("in_export", sa.Boolean(), nullable=True),
    )
    op.create_index("ix_stem_pieces_job_id", "stem_pieces", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_stem_pieces_job_id", table_name="stem_pieces")
    op.drop_table("stem_pieces")
