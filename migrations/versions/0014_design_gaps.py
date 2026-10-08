"""design gaps: routing rule versions, per-key allowed models, A2A framework (SCR-H02·H13·G01)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-07 23:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('routing_rules', schema=None) as b:
        b.add_column(sa.Column('version', sa.Integer(), nullable=False, server_default='0'))
    op.create_table(
        'routing_rule_versions',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('rule_id', sa.String(length=40), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('actor', sa.String(length=255), nullable=True),
        sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('routing_rule_versions', schema=None) as b:
        b.create_index('ix_routing_rule_versions_rule_id', ['rule_id'], unique=False)
    with op.batch_alter_table('personal_tokens', schema=None) as b:
        b.add_column(sa.Column('allowed_models', sa.JSON(), nullable=True))
    with op.batch_alter_table('a2a_agents', schema=None) as b:
        b.add_column(sa.Column('framework', sa.String(length=40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('a2a_agents', schema=None) as b:
        b.drop_column('framework')
    with op.batch_alter_table('personal_tokens', schema=None) as b:
        b.drop_column('allowed_models')
    with op.batch_alter_table('routing_rule_versions', schema=None) as b:
        b.drop_index('ix_routing_rule_versions_rule_id')
    op.drop_table('routing_rule_versions')
    with op.batch_alter_table('routing_rules', schema=None) as b:
        b.drop_column('version')
