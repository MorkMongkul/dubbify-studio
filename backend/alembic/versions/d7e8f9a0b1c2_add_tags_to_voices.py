"""add_tags_to_voices

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-10-18 09:00:00.000000

Tags on voices: who a voice sounds like (gender, age_group) plus personality
and role tags, so the Voices tab and the voice pickers can sort voices into
sections by age and gender (app/services/voice_tags.py). Installed desktop
apps never run alembic; they get the same columns at startup from init_db
(app/core/database.py, VOICE_TAG_COLUMNS).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, Sequence[str], None] = 'c6d7e8f9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ("gender", "age_group", "personality", "roles")


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("voices")}
    for name, col_type in zip(_COLUMNS, (sa.String(10), sa.String(10), sa.JSON(), sa.JSON())):
        if name not in cols:
            op.add_column("voices", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("voices", name)
