"""add_subtitle_path_to_jobs

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-30 12:00:00.000000

Adds the subtitle_path column to jobs. This is set when a job uses the
subtitle pipeline (embedded subtitles detected, or user-uploaded .srt/.ass),
and is used in Stage 2 (Analyze) to route to the subtitle analysis pipeline
instead of ASR. Previously the route used getattr(job, 'subtitle_path', None)
against a column that did not exist, so subtitle jobs always fell through to
the ASR pipeline.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("jobs")}
    if "subtitle_path" not in existing:
        op.add_column(
            "jobs",
            sa.Column("subtitle_path", sa.String(), nullable=True, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("jobs", "subtitle_path")
