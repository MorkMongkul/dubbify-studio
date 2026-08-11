"""add_background_color_to_overlays

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-24 00:00:00.000000

Adds a fill color behind the subtitle text box (a solid/semi-transparent
band, distinct from the text's own fill/outline colors).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("overlays")}
    if "background_color" not in cols:
        op.add_column("overlays", sa.Column("background_color", sa.String(20), server_default=""))


def downgrade() -> None:
    op.drop_column("overlays", "background_color")
