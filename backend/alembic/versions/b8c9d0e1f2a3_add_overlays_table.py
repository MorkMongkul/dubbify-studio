"""add_overlays_table

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-22 00:00:00.000000

Adds the overlays table — draggable/resizable image watermarks and the
burned-in subtitle box, positioned as fractions of the video's own
dimensions so the same values map onto both the live editor canvas and the
full-resolution ffmpeg export.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "overlays" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "overlays",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("type", sa.String(20), server_default="image"),
        sa.Column("media_path", sa.String(), server_default=""),
        sa.Column("x", sa.Float(), server_default="0.7"),
        sa.Column("y", sa.Float(), server_default="0.05"),
        sa.Column("width", sa.Float(), server_default="0.2"),
        sa.Column("height", sa.Float(), server_default="0.15"),
        sa.Column("opacity", sa.Float(), server_default="1.0"),
        sa.Column("z_index", sa.Integer(), server_default="0"),
        sa.Column("start_time", sa.Float(), nullable=True),
        sa.Column("end_time", sa.Float(), nullable=True),
        sa.Column("font_size", sa.Integer(), server_default="42"),
        sa.Column("color", sa.String(20), server_default="white"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("overlays")
