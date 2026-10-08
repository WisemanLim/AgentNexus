"""approval time extension count (D14-③, WCAG 2.2.1 Timing Adjustable)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-07 02:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('approvals', schema=None) as b:
        b.add_column(sa.Column('extensions', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('approvals', schema=None) as b:
        b.drop_column('extensions')
