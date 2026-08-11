"""add_outline_color_to_overlays

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-23 00:00:00.000000

Subtitle style needs fill and outline (stroke) controllable independently —
outline_color was previously hardcoded to black in the renderer.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, Sequence[str], None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("overlays")}
    if "outline_color" not in cols:
        op.add_column("overlays", sa.Column("outline_color", sa.String(20), server_default="black"))


def downgrade() -> None:
    op.drop_column("overlays", "outline_color")
