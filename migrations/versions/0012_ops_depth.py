"""ops depth: session cutoff & claims, approval delegation, args-schema mode, audited log unmask, settings history, sampling policy

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-07 20:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as b:
        b.add_column(sa.Column('tokens_not_before', sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column('claims', sa.JSON(), nullable=True))
    with op.batch_alter_table('approvals', schema=None) as b:
        b.add_column(sa.Column('delegated_to', sa.String(length=255), nullable=True))
    with op.batch_alter_table('tool_rules', schema=None) as b:
        b.add_column(sa.Column('args_mode', sa.String(length=10), nullable=False, server_default='strict'))
    with op.batch_alter_table('log_entries', schema=None) as b:
        b.add_column(sa.Column('raw_enc', sa.Text(), nullable=True))
    with op.batch_alter_table('mcp_servers', schema=None) as b:
        b.add_column(sa.Column('sampling_policy', sa.String(length=10), nullable=False, server_default='ask'))
    op.create_table(
        'setting_history',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('key', sa.String(length=120), nullable=False),
        sa.Column('value', sa.JSON(), nullable=True),
        sa.Column('changed_by', sa.String(length=255), nullable=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_setting_history_key', 'setting_history', ['key'])


def downgrade() -> None:
    op.drop_index('ix_setting_history_key', table_name='setting_history')
    op.drop_table('setting_history')
    with op.batch_alter_table('mcp_servers', schema=None) as b:
        b.drop_column('sampling_policy')
    with op.batch_alter_table('log_entries', schema=None) as b:
        b.drop_column('raw_enc')
    with op.batch_alter_table('tool_rules', schema=None) as b:
        b.drop_column('args_mode')
    with op.batch_alter_table('approvals', schema=None) as b:
        b.drop_column('delegated_to')
    with op.batch_alter_table('users', schema=None) as b:
        b.drop_column('claims')
        b.drop_column('tokens_not_before')
