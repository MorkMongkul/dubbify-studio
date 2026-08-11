"""add_blur_to_overlays

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-07-25 00:00:00.000000

Adds a "shape" overlay type — a plain color box or a blurred region, used
to cover something in the source video (e.g. a burned-in original-language
subtitle) so a new subtitle overlay reads cleanly on top of it.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd0e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("overlays")}
    if "blur" not in cols:
        op.add_column("overlays", sa.Column("blur", sa.Boolean(), server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("overlays", "blur")
