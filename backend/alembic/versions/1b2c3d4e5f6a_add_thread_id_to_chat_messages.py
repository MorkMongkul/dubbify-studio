"""add_thread_id_to_chat_messages

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
Create Date: 2026-10-21 17:30:00.000000

A project now has several Assistant chats (chat_threads, created by
create_all); each message belongs to one. Messages saved before threads keep
NULL until the project's chats are first listed, which puts them into one
chat (chat_agent.adopt_unthreaded). Installed desktop apps get the column at
startup from init_db (app/core/database.py).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '1b2c3d4e5f6a'
down_revision: Union[str, Sequence[str], None] = '0a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("chat_messages")}
    if "thread_id" not in cols:
        op.add_column("chat_messages", sa.Column("thread_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "thread_id")
