"""add_title_to_jobs

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-09-06 00:00:00.000000

Every uploaded video is stored on disk as a generic source.mp4 regardless of
its original filename, so the Sessions panel had nothing distinctive to show
once a project holds more than one session — this backs a user-facing label
per session, falling back to the filename when blank.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c6d7e8f9a0b1'
down_revision: Union[str, Sequence[str], None] = 'b5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("jobs")}
    if "title" not in cols:
        op.add_column("jobs", sa.Column("title", sa.String(255), server_default=""))


def downgrade() -> None:
    op.drop_column("jobs", "title")
