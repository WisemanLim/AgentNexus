"""SLO · error budget: SLO definitions + per-worker per-minute SLI counters (D15-④)

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-07 05:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None

BOUNDS = (100, 250, 500, 1000, 2000, 5000)


def upgrade() -> None:
    op.create_table(
        'slos',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('scope', sa.String(length=10), nullable=False),
        sa.Column('target', sa.Float(), nullable=False),
        sa.Column('threshold_ms', sa.Integer(), nullable=True),
        sa.Column('window_days', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('alert_state', sa.String(length=20), nullable=True),
        sa.Column('alert_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_by', sa.String(length=255), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'sli_minutes',
        sa.Column('id', sa.String(length=120), nullable=False),
        sa.Column('minute', sa.DateTime(timezone=True), nullable=False),
        sa.Column('day', sa.String(length=10), nullable=False),
        sa.Column('cls', sa.String(length=10), nullable=False),
        sa.Column('worker', sa.String(length=80), nullable=False),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('errors', sa.Integer(), nullable=False, server_default='0'),
        *[sa.Column(f'gt{b}', sa.Integer(), nullable=False, server_default='0') for b in BOUNDS],
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('sli_minutes', schema=None) as b:
        b.create_index('ix_sli_minutes_minute', ['minute'], unique=False)
        b.create_index('ix_sli_minutes_day', ['day'], unique=False)


def downgrade() -> None:
    op.drop_table('sli_minutes')
    op.drop_table('slos')
