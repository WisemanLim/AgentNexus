"""a2a task events: state transitions and send/sse/push/poll event log (SCR-G06)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07 21:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('a2a_tasks', schema=None) as b:
        b.add_column(sa.Column('events', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('a2a_tasks', schema=None) as b:
        b.drop_column('events')
