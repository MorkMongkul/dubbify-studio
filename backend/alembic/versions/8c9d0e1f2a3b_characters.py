"""characters

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
Create Date: 2026-10-25 10:00:00.000000

Characters recognised by voice and face (0.5.0): each line's prints
(line_prints), the two guesses for a line Movelo isn't sure of
(segments.who_check), whether the user picked the speaker
(segments.who_confirmed), the machine's own Khmer (segments.khmer_machine),
a speaker's face thumbnail, the project's upgrade marker and its "Not the
same" pairs, and the RECOGNIZING job status. Installed desktop apps get the
table from create_all and the columns from init_db's add_missing_columns.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '8c9d0e1f2a3b'
down_revision: Union[str, Sequence[str], None] = '7b8c9d0e1f2a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add(inspector, table: str, column: sa.Column) -> None:
    if column.name not in {c["name"] for c in inspector.get_columns(table)}:
        op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _add(inspector, "segments", sa.Column("who_check", sa.Text(), nullable=True))
    _add(inspector, "segments", sa.Column("who_confirmed", sa.Boolean(), nullable=True))
    _add(inspector, "segments", sa.Column("khmer_machine", sa.Text(), nullable=True))
    _add(inspector, "speakers", sa.Column("face_crop_path", sa.String(), nullable=True))
    _add(inspector, "projects", sa.Column("characters_version", sa.Integer(), nullable=True))
    _add(inspector, "projects", sa.Column("character_dismissals", sa.Text(), nullable=True))
    if "line_prints" not in set(inspector.get_table_names()):
        op.create_table(
            "line_prints",
            sa.Column("segment_id", sa.String(), sa.ForeignKey("segments.id"), primary_key=True),
            sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
            sa.Column("voice_print", sa.LargeBinary(), nullable=True),
            sa.Column("face_print", sa.LargeBinary(), nullable=True),
            sa.Column("speaking_score", sa.Float(), nullable=True),
            sa.Column("face_crop_path", sa.String(), nullable=True),
            sa.Column("model_version", sa.String(), nullable=False, server_default=""),
            sa.Column("made_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_line_prints_job_id", "line_prints", ["job_id"])

    if bind.dialect.name == "postgresql":
        # Job.status stores the enum's names; skip when already there (safe to run twice)
        enum_range = bind.execute(sa.text("SELECT enum_range(NULL::jobstatus)")).scalar() or ""
        if "RECOGNIZING" not in enum_range:
            op.execute("ALTER TYPE jobstatus ADD VALUE 'RECOGNIZING' AFTER 'TRANSCRIBING'")


def downgrade() -> None:
    op.drop_index("ix_line_prints_job_id", table_name="line_prints")
    op.drop_table("line_prints")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("character_dismissals")
        batch.drop_column("characters_version")
    with op.batch_alter_table("speakers") as batch:
        batch.drop_column("face_crop_path")
    with op.batch_alter_table("segments") as batch:
        batch.drop_column("khmer_machine")
        batch.drop_column("who_confirmed")
        batch.drop_column("who_check")
    # PostgreSQL cannot remove an enum value: RECOGNIZING just becomes unused.
