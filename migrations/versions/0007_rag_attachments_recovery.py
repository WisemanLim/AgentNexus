"""attachments, knowledge bases (RAG), run heartbeat, model quality

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_runs_heartbeat_at'), ['heartbeat_at'], unique=False)
    with op.batch_alter_table('models', schema=None) as batch_op:
        batch_op.add_column(sa.Column('quality', sa.Float(), nullable=True))
    op.create_table(
        'attachments',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('owner_sub', sa.String(length=255), nullable=False),
        sa.Column('session_id', sa.String(length=40), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('mime', sa.String(length=120), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('path', sa.String(length=500), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('attachments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_attachments_owner_sub'), ['owner_sub'], unique=False)
        batch_op.create_index(batch_op.f('ix_attachments_session_id'), ['session_id'], unique=False)
    op.create_table(
        'knowledge_bases',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('team', sa.String(length=64), nullable=True),
        sa.Column('data_class', sa.String(length=20), nullable=False),
        sa.Column('embedding_model', sa.String(length=120), nullable=False),
        sa.Column('dims', sa.Integer(), nullable=True),
        sa.Column('owner', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('knowledge_bases', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_bases_team'), ['team'], unique=False)
    op.create_table(
        'kb_documents',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('kb_id', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('source', sa.String(length=500), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('chunks', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['kb_id'], ['knowledge_bases.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('kb_documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_kb_documents_kb_id'), ['kb_id'], unique=False)
    op.create_table(
        'kb_chunks',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('kb_id', sa.String(length=64), nullable=False),
        sa.Column('doc_id', sa.String(length=40), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('embedding', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['doc_id'], ['kb_documents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('kb_chunks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_kb_chunks_kb_id'), ['kb_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_kb_chunks_doc_id'), ['doc_id'], unique=False)


def downgrade() -> None:
    for t, ixs in (('kb_chunks', ('ix_kb_chunks_doc_id', 'ix_kb_chunks_kb_id')), ('kb_documents', ('ix_kb_documents_kb_id',)),
                   ('knowledge_bases', ('ix_knowledge_bases_team',)),
                   ('attachments', ('ix_attachments_session_id', 'ix_attachments_owner_sub'))):
        with op.batch_alter_table(t, schema=None) as batch_op:
            for ix in ixs:
                batch_op.drop_index(ix)
        op.drop_table(t)
    with op.batch_alter_table('models', schema=None) as batch_op:
        batch_op.drop_column('quality')
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_runs_heartbeat_at'))
        batch_op.drop_column('heartbeat_at')
