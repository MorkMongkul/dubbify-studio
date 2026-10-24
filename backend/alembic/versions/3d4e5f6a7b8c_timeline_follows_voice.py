"""timeline_follows_voice

Revision ID: 3d4e5f6a7b8c
Revises: 2c3d4e5f6a7b
Create Date: 2026-10-23 20:00:00.000000

0.3.0: a clip on the timeline is as long as the voice it plays. Segments get
the actor's original span (source_start/source_end), the part of the voice
they play (voice_offset, fit_rate) and the text the voice was made from
(voice_text); jobs get the timeline rules they follow (timeline_version, NULL
= converted at startup by timeline.convert_old_jobs) and the video's
frame_rate. Installed desktop apps get the columns at startup from init_db
(app/core/database.py).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '3d4e5f6a7b8c'
down_revision: Union[str, Sequence[str], None] = '2c3d4e5f6a7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEGMENT_COLUMNS = [
    ("source_start", sa.Float()), ("source_end", sa.Float()), ("voice_offset", sa.Float()),
    ("fit_rate", sa.Float()), ("voice_text", sa.Text()),
]
JOB_COLUMNS = [("timeline_version", sa.Integer()), ("frame_rate", sa.Float())]


def upgrade() -> None:
    bind = op.get_bind()
    for table, columns in (("segments", SEGMENT_COLUMNS), ("jobs", JOB_COLUMNS)):
        existing = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        for name, col_type in columns:
            if name not in existing:
                op.add_column(table, sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    for name, _ in JOB_COLUMNS:
        op.drop_column("jobs", name)
    for name, _ in SEGMENT_COLUMNS:
        op.drop_column("segments", name)
