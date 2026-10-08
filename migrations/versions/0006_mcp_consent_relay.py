"""mcp consent payload/result and relay table

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06 09:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payload', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('result', sa.JSON(), nullable=True))
    op.create_table(
        'mcp_relays',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('server_id', sa.String(length=64), nullable=False),
        sa.Column('method', sa.String(length=64), nullable=False),
        sa.Column('params', sa.JSON(), nullable=False),
        sa.Column('response', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mcp_relays', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mcp_relays_session_id'), ['session_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('mcp_relays', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mcp_relays_session_id'))
    op.drop_table('mcp_relays')
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.drop_column('result')
        batch_op.drop_column('payload')
