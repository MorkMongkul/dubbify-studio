"""single-voice dub projects (0.5.7): kind, voice, rate, auto export

Revision ID: a1b2c3d4e5f7
Revises: 8c9d0e1f2a3b
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f7"
down_revision = "8c9d0e1f2a3b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("dub_mode", sa.String(20), nullable=True))
    op.add_column("projects", sa.Column("voice_name", sa.String(64), nullable=True))
    op.add_column("projects", sa.Column("voice_rate", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("auto_export", sa.Boolean(), nullable=True))


def downgrade() -> None:
    for name in ("auto_export", "voice_rate", "voice_name", "dub_mode"):
        op.drop_column("projects", name)
