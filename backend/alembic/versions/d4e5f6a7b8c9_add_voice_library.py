"""add_voice_library

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-30 16:00:00.000000

Adds the workspace-global Voice library (voices table) for the Voice Creator,
plus a nullable voice_id FK on speakers and segments so a saved voice can be
assigned at the speaker level (default) or overridden per segment.

`mode` is stored as a plain VARCHAR (validated by the VoiceMode enum in the
schema layer) to avoid the native-enum/VARCHAR mismatch seen with jobs.status.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "voices" not in inspector.get_table_names():
        op.create_table(
            "voices",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("mode", sa.String(length=20), server_default="design"),
            sa.Column("description", sa.Text(), server_default=""),
            sa.Column("reference_audio_path", sa.String(), server_default=""),
            sa.Column("reference_transcript", sa.Text(), server_default=""),
            sa.Column("cfg_value", sa.Float(), server_default="2.0"),
            sa.Column("inference_timesteps", sa.Integer(), server_default="10"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )

    speaker_cols = {c["name"] for c in inspector.get_columns("speakers")}
    if "voice_id" not in speaker_cols:
        op.add_column("speakers", sa.Column("voice_id", sa.String(), nullable=True))
        op.create_foreign_key(
            "fk_speakers_voice_id", "speakers", "voices", ["voice_id"], ["id"]
        )

    segment_cols = {c["name"] for c in inspector.get_columns("segments")}
    if "voice_id" not in segment_cols:
        op.add_column("segments", sa.Column("voice_id", sa.String(), nullable=True))
        op.create_foreign_key(
            "fk_segments_voice_id", "segments", "voices", ["voice_id"], ["id"]
        )


def downgrade() -> None:
    op.drop_constraint("fk_segments_voice_id", "segments", type_="foreignkey")
    op.drop_column("segments", "voice_id")
    op.drop_constraint("fk_speakers_voice_id", "speakers", type_="foreignkey")
    op.drop_column("speakers", "voice_id")
    op.drop_table("voices")
