"""add_level_gain_db_to_segments

Revision ID: 2c3d4e5f6a7b
Revises: 1b2c3d4e5f6a
Create Date: 2026-10-23 16:00:00.000000

The gain that gave a voice the loudness of the original line it replaces,
decided once when the voice is made (voice_level.level_new_voice) and re-used
on every later render. NULL for voices made before 0.2.8: they keep sounding
as they did. Installed desktop apps get the column at startup from init_db
(app/core/database.py).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '2c3d4e5f6a7b'
down_revision: Union[str, Sequence[str], None] = '1b2c3d4e5f6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("segments")}
    if "level_gain_db" not in cols:
        op.add_column("segments", sa.Column("level_gain_db", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("segments", "level_gain_db")
