"""user TOTP MFA

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06 20:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mfa_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('mfa_secret', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('mfa_secret')
        batch_op.drop_column('mfa_enabled')
