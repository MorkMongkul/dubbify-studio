"""fades_and_ducking

Revision ID: 4e5f6a7b8c9d
Revises: 3d4e5f6a7b8c
Create Date: 2026-10-24 09:00:00.000000

0.3.1: fades on voice clips (segments.fade_in_secs/fade_out_secs, NULL = no
fade) and the music dipping under voices (jobs.duck_music, NULL = analysed
before 0.3.1: off). Installed desktop apps get the columns at startup from
init_db (app/core/database.py).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '4e5f6a7b8c9d'
down_revision: Union[str, Sequence[str], None] = '3d4e5f6a7b8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = [("segments", "fade_in_secs", sa.Float()), ("segments", "fade_out_secs", sa.Float()),
           ("jobs", "duck_music", sa.Boolean())]


def upgrade() -> None:
    bind = op.get_bind()
    for table, name, col_type in COLUMNS:
        if name not in {c["name"] for c in sa.inspect(bind).get_columns(table)}:
            op.add_column(table, sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    for table, name, _ in reversed(COLUMNS):
        op.drop_column(table, name)
