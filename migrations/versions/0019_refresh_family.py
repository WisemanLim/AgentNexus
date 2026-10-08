"""refresh token family — rotation lineage for reuse detection (D17-④)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-07 07:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('oauth_refresh_tokens', schema=None) as b:
        b.add_column(sa.Column('family_id', sa.String(length=40), nullable=True))
        b.create_index('ix_oauth_refresh_tokens_family_id', ['family_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('oauth_refresh_tokens', schema=None) as b:
        b.drop_index('ix_oauth_refresh_tokens_family_id')
        b.drop_column('family_id')
