"""add_lane_index_to_segments

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-15 00:00:00.000000

Decouples timeline rows from speaker identity: segments now have a
`lane_index` independent of `speaker_id`, so clips can be dragged to any
row regardless of who's speaking. Backfills existing segments so each
job's speakers keep the same per-speaker row order the UI already showed
(alphabetical by speaker label, unassigned segments last) — nothing visibly
jumps around for existing jobs after this migration.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("segments")}
    if "lane_index" not in cols:
        op.add_column("segments", sa.Column("lane_index", sa.Integer(), nullable=True))

    jobs = bind.execute(sa.text("SELECT DISTINCT job_id FROM segments")).fetchall()
    for (job_id,) in jobs:
        speaker_rows = bind.execute(sa.text("""
            SELECT DISTINCT sp.id, sp.label
            FROM speakers sp
            JOIN segments seg ON seg.speaker_id = sp.id
            WHERE seg.job_id = :job_id
            ORDER BY sp.label
        """), {"job_id": job_id}).fetchall()

        lane = 0
        for sp_id, _label in speaker_rows:
            bind.execute(
                sa.text("UPDATE segments SET lane_index = :lane WHERE job_id = :job_id AND speaker_id = :sp_id"),
                {"lane": lane, "job_id": job_id, "sp_id": sp_id},
            )
            lane += 1
        bind.execute(
            sa.text("UPDATE segments SET lane_index = :lane WHERE job_id = :job_id AND speaker_id IS NULL"),
            {"lane": lane, "job_id": job_id},
        )


def downgrade() -> None:
    op.drop_column("segments", "lane_index")
