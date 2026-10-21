"""add_line_ref_to_chat_messages

Revision ID: 0a1b2c3d4e5f
Revises: f9a0b1c2d3e4
Create Date: 2026-10-21 16:40:00.000000

A message sent while the user's text cursor was in a transcript line keeps
that line (a snapshot) in chat_messages.line_ref, so the Assistant knows
what "this line" means on every replay. Installed desktop apps get the column
at startup from init_db (app/core/database.py).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0a1b2c3d4e5f'
down_revision: Union[str, Sequence[str], None] = 'f9a0b1c2d3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("chat_messages")}
    if "line_ref" not in cols:
        op.add_column("chat_messages", sa.Column("line_ref", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "line_ref")
