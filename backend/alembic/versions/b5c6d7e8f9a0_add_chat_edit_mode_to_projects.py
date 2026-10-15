"""add_chat_edit_mode_to_projects

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-09-05 00:00:00.000000

Backs the Assistant chat's auto/manual edit-approval toggle. The two new
chat tables (chat_messages, chat_proposed_edits) need no migration — they're
brand new, so init_db()'s create_all picks them up automatically; only a
column added to an EXISTING table (projects) needs one.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'b5c6d7e8f9a0'
down_revision: Union[str, Sequence[str], None] = 'a4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("projects")}
    if "chat_edit_mode" not in cols:
        op.add_column("projects", sa.Column("chat_edit_mode", sa.String(10), server_default="manual"))


def downgrade() -> None:
    op.drop_column("projects", "chat_edit_mode")
