"""add_bgm_volume_db_to_jobs

Revision ID: a4b5c6d7e8f9
Revises: f2a3b4c5d6e7
Create Date: 2026-09-01 00:00:00.000000

The BGM & SFX timeline track had no gain control, only mute — this backs a
volume slider for it, mirroring the per-segment volume_db already used for
TTS clips.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a4b5c6d7e8f9'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("jobs")}
    if "bgm_volume_db" not in cols:
        op.add_column("jobs", sa.Column("bgm_volume_db", sa.Float(), server_default="0.0"))


def downgrade() -> None:
    op.drop_column("jobs", "bgm_volume_db")
