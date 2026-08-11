"""add_overlay_templates

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-10 00:00:00.000000

Adds the overlay_templates table — a workspace-global saved snapshot of a
job's overlay layout (logo, boxes, subtitle style) that can be re-applied
to any episode, or auto-applied to new uploads when marked default.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "overlay_templates" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "overlay_templates",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false()),
        sa.Column("items", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("overlay_templates")
