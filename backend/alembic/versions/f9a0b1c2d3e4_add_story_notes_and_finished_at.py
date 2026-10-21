"""add_story_notes_and_finished_at

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-10-21 16:20:00.000000

Story notes (app/services/story_notes.py) are one JSON document per project:
the user's intro plus what the AI learned from the episodes the user marked
finished (jobs.finished_at). Installed desktop apps get both columns at
startup from init_db (app/core/database.py).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f9a0b1c2d3e4'
down_revision: Union[str, Sequence[str], None] = 'e8f9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    project_cols = {c["name"] for c in sa.inspect(bind).get_columns("projects")}
    if "story_notes" not in project_cols:
        op.add_column("projects", sa.Column("story_notes", sa.JSON(), nullable=True))
    job_cols = {c["name"] for c in sa.inspect(bind).get_columns("jobs")}
    if "finished_at" not in job_cols:
        op.add_column("jobs", sa.Column("finished_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "finished_at")
    op.drop_column("projects", "story_notes")
