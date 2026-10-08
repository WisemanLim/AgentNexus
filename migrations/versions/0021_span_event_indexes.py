"""span · run event indexes — overview KPI aggregation without full scans (D22-①)

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-08 18:00:00
"""
from alembic import op


revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_spans_parent_trace', 'spans', ['parent_id', 'trace_id'], unique=False)
    op.create_index('ix_run_events_type_ts', 'run_events', ['type', 'ts'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_run_events_type_ts', table_name='run_events')
    op.drop_index('ix_spans_parent_trace', table_name='spans')
