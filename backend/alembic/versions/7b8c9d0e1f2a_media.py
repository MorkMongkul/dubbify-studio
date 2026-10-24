"""media

Revision ID: 7b8c9d0e1f2a
Revises: 6a7b8c9d0e1f
Create Date: 2026-10-24 17:00:00.000000

The Media tab (0.4.0): a project's media library (media_assets), each
episode's media clips (media_clips), media templates (media_templates), and
each media track's switches (jobs.media_track_flags). Installed desktop apps
get the tables from create_all and the column from init_db's add_missing_columns.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '7b8c9d0e1f2a'
down_revision: Union[str, Sequence[str], None] = '6a7b8c9d0e1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "media_track_flags" not in {c["name"] for c in inspector.get_columns("jobs")}:
        op.add_column("jobs", sa.Column("media_track_flags", sa.Text(), nullable=True))
    if "media_assets" not in tables:
        op.create_table(
            "media_assets",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("path", sa.String(), nullable=False),
            sa.Column("preview_path", sa.String()),
            sa.Column("thumb_path", sa.String()),
            sa.Column("audio_path", sa.String(), nullable=True),
            sa.Column("width", sa.Integer()),
            sa.Column("height", sa.Integer()),
            sa.Column("duration_secs", sa.Float(), nullable=True),
            sa.Column("has_alpha", sa.Boolean()),
            sa.Column("alpha_kept", sa.Boolean()),
            sa.Column("has_audio", sa.Boolean()),
            sa.Column("last_box", sa.Text(), nullable=True),
            sa.Column("status", sa.String(12)),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_media_assets_project_id", "media_assets", ["project_id"])
    if "media_clips" not in tables:
        op.create_table(
            "media_clips",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
            sa.Column("asset_id", sa.String(), sa.ForeignKey("media_assets.id"), nullable=False),
            sa.Column("lane_index", sa.Integer()),
            sa.Column("start_time", sa.Float(), nullable=False),
            sa.Column("end_time", sa.Float(), nullable=False),
            sa.Column("source_in", sa.Float()),
            sa.Column("x", sa.Float()),
            sa.Column("y", sa.Float()),
            sa.Column("width", sa.Float()),
            sa.Column("height", sa.Float()),
            sa.Column("volume_db", sa.Float()),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_media_clips_job_id", "media_clips", ["job_id"])
        op.create_index("ix_media_clips_asset_id", "media_clips", ["asset_id"])
    if "media_templates" not in tables:
        op.create_table(
            "media_templates",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("items", sa.Text()),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_media_templates_project_id", "media_templates", ["project_id"])


def downgrade() -> None:
    op.drop_table("media_clips")
    op.drop_table("media_templates")
    op.drop_table("media_assets")
    op.drop_column("jobs", "media_track_flags")
