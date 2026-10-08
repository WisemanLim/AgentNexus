"""MCP upstream protocol: per-server preference (auto|legacy|2026-07-28) + detected version (D14-②)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-07 01:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('mcp_servers', schema=None) as b:
        b.add_column(sa.Column('protocol', sa.String(length=20), nullable=False, server_default='auto'))
        b.add_column(sa.Column('upstream_protocol', sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('mcp_servers', schema=None) as b:
        b.drop_column('upstream_protocol')
        b.drop_column('protocol')
