"""chat & editor depth: message feedback/branches, session source, dry-run test runs, code-only draft, per-step usage, host session events

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('messages', schema=None) as b:
        b.add_column(sa.Column('feedback', sa.JSON(), nullable=True))
        b.add_column(sa.Column('branch_of', sa.String(length=40), nullable=True))
    with op.batch_alter_table('sessions', schema=None) as b:
        b.add_column(sa.Column('source', sa.String(length=10), nullable=False, server_default='chat'))
    with op.batch_alter_table('runs', schema=None) as b:
        b.add_column(sa.Column('dry_run', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('agent_profiles', schema=None) as b:
        b.add_column(sa.Column('draft_code', sa.Text(), nullable=True))
    with op.batch_alter_table('usage_records', schema=None) as b:
        b.add_column(sa.Column('step_id', sa.String(length=40), nullable=True))
        b.create_index('ix_usage_records_step_id', ['step_id'])
    with op.batch_alter_table('mcp_sessions', schema=None) as b:
        b.add_column(sa.Column('events', sa.JSON(), nullable=True))
        b.add_column(sa.Column('reconnects', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('mcp_sessions', schema=None) as b:
        b.drop_column('reconnects')
        b.drop_column('events')
    with op.batch_alter_table('usage_records', schema=None) as b:
        b.drop_index('ix_usage_records_step_id')
        b.drop_column('step_id')
    with op.batch_alter_table('agent_profiles', schema=None) as b:
        b.drop_column('draft_code')
    with op.batch_alter_table('runs', schema=None) as b:
        b.drop_column('dry_run')
    with op.batch_alter_table('sessions', schema=None) as b:
        b.drop_column('source')
    with op.batch_alter_table('messages', schema=None) as b:
        b.drop_column('branch_of')
        b.drop_column('feedback')
