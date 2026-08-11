"""add_logo_path_to_projects

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-21 00:00:00.000000

Adds a per-project watermark/logo path, reused across every export from
that project (Export options: logo overlay + subtitle burn-in).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("projects")}
    if "logo_path" not in cols:
        op.add_column("projects", sa.Column("logo_path", sa.String(), server_default=""))


def downgrade() -> None:
    op.drop_column("projects", "logo_path")
