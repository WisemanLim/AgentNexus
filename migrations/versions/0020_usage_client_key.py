"""usage record client key — budgets scoped to a gateway client key (D20-①)

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-08 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('usage_records', schema=None) as b:
        b.add_column(sa.Column('client_key', sa.String(length=40), nullable=True))
        b.create_index('ix_usage_records_client_key', ['client_key'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('usage_records', schema=None) as b:
        b.drop_index('ix_usage_records_client_key')
        b.drop_column('client_key')
