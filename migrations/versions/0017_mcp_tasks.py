"""MCP Tasks extension (io.modelcontextprotocol/tasks, SEP-2663) — gateway-run long tool calls (D15-①)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-07 04:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'mcp_tasks',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('server_id', sa.String(length=64), nullable=False),
        sa.Column('principal_sub', sa.String(length=255), nullable=False),
        sa.Column('tool', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('status_message', sa.Text(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error', sa.JSON(), nullable=True),
        sa.Column('ttl_ms', sa.Integer(), nullable=True),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mcp_tasks', schema=None) as b:
        b.create_index('ix_mcp_tasks_principal_sub', ['principal_sub'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('mcp_tasks', schema=None) as b:
        b.drop_index('ix_mcp_tasks_principal_sub')
    op.drop_table('mcp_tasks')
