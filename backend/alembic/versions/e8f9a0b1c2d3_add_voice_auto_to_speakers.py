"""add_voice_auto_to_speakers

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-10-18 14:00:00.000000

A speaker nobody gave a voice gets a built-in voice the first time it is
voiced (app/services/auto_voice.py). voice_auto marks such a picked voice, so
it doesn't count as the user's choice. Installed desktop apps get the column
at startup from init_db (app/core/database.py).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'e8f9a0b1c2d3'
down_revision: Union[str, Sequence[str], None] = 'd7e8f9a0b1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("speakers")}
    if "voice_auto" not in cols:
        op.add_column("speakers", sa.Column("voice_auto", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("speakers", "voice_auto")
