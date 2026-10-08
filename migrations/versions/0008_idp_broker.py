"""external IdP SSO broker: idp login fields, broker requests

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06 18:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('idp_connections', schema=None) as batch_op:
        batch_op.add_column(sa.Column('login_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column('internal_url', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('scopes', sa.String(length=255), nullable=False,
                                      server_default='openid profile email'))
        batch_op.add_column(sa.Column('last_test', sa.JSON(), nullable=True))
    op.create_table(
        'broker_requests',
        sa.Column('state', sa.String(length=64), nullable=False),
        sa.Column('idp_id', sa.String(length=40), nullable=False),
        sa.Column('mode', sa.String(length=10), nullable=False),
        sa.Column('params', sa.JSON(), nullable=False),
        sa.Column('code_verifier', sa.String(length=128), nullable=False),
        sa.Column('nonce', sa.String(length=64), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('used', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('state'),
    )
    with op.batch_alter_table('broker_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_broker_requests_idp_id'), ['idp_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('broker_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_broker_requests_idp_id'))
    op.drop_table('broker_requests')
    with op.batch_alter_table('idp_connections', schema=None) as batch_op:
        batch_op.drop_column('last_test')
        batch_op.drop_column('scopes')
        batch_op.drop_column('internal_url')
        batch_op.drop_column('login_enabled')
