"""registration review: version pin, source filters, card cache & checks, agent card publish, policy drafts & tests

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-07 09:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('catalog_items', schema=None) as b:
        b.add_column(sa.Column('pinned_version', sa.String(length=40), nullable=True))
    with op.batch_alter_table('registry_sources', schema=None) as b:
        b.add_column(sa.Column('namespaces', sa.JSON(), nullable=True))
        b.add_column(sa.Column('sync_interval_s', sa.Integer(), nullable=True))
        b.add_column(sa.Column('last_result', sa.JSON(), nullable=True))
    with op.batch_alter_table('a2a_agents', schema=None) as b:
        b.add_column(sa.Column('etag', sa.String(length=255), nullable=True))
        b.add_column(sa.Column('cache_ttl_s', sa.Integer(), nullable=False, server_default='3600'))
        b.add_column(sa.Column('owner_team', sa.String(length=120), nullable=True))
    with op.batch_alter_table('agent_profiles', schema=None) as b:
        b.add_column(sa.Column('a2a_card', sa.JSON(), nullable=True))
        b.add_column(sa.Column('a2a_status', sa.String(length=20), nullable=False, server_default='draft'))
    op.create_table(
        'a2a_card_checks',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('agent_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('etag', sa.String(length=255), nullable=True),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('a2a_card_checks', schema=None) as b:
        b.create_index(b.f('ix_a2a_card_checks_agent_id'), ['agent_id'], unique=False)
    op.create_table(
        'policy_drafts',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('policy_id', sa.String(length=40), nullable=True),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('test_result', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('decided_by', sa.String(length=255), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('policy_drafts', schema=None) as b:
        b.create_index(b.f('ix_policy_drafts_policy_id'), ['policy_id'], unique=False)
    op.create_table(
        'policy_tests',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('input', sa.JSON(), nullable=False),
        sa.Column('expect', sa.String(length=10), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('policy_tests')
    with op.batch_alter_table('policy_drafts', schema=None) as b:
        b.drop_index(b.f('ix_policy_drafts_policy_id'))
    op.drop_table('policy_drafts')
    with op.batch_alter_table('a2a_card_checks', schema=None) as b:
        b.drop_index(b.f('ix_a2a_card_checks_agent_id'))
    op.drop_table('a2a_card_checks')
    with op.batch_alter_table('agent_profiles', schema=None) as b:
        b.drop_column('a2a_status')
        b.drop_column('a2a_card')
    with op.batch_alter_table('a2a_agents', schema=None) as b:
        b.drop_column('owner_team')
        b.drop_column('cache_ttl_s')
        b.drop_column('etag')
    with op.batch_alter_table('registry_sources', schema=None) as b:
        b.drop_column('last_result')
        b.drop_column('sync_interval_s')
        b.drop_column('namespaces')
    with op.batch_alter_table('catalog_items', schema=None) as b:
        b.drop_column('pinned_version')
