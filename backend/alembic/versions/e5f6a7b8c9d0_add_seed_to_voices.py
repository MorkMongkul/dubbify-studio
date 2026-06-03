"""add_seed_to_voices

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-30 18:00:00.000000

Adds a fixed `seed` to voices so a Voice Design profile produces a consistent
voice identity across every line (VoxCPM2 otherwise samples a new voice each
generation). -1 = not frozen; a concrete value locks the voice.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("voices")}
    if "seed" not in cols:
        op.add_column("voices", sa.Column("seed", sa.Integer(), server_default="-1"))


def downgrade() -> None:
    op.drop_column("voices", "seed")
