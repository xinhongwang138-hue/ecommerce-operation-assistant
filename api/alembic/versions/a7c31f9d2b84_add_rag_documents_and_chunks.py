"""add rag_documents and rag_chunks for self-hosted RAG

Revision ID: a7c31f9d2b84
Revises: 8596d7ea15e3
Create Date: 2026-10-30 19:12:04.118233

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = 'a7c31f9d2b84'
down_revision: Union[str, Sequence[str], None] = '8596d7ea15e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 原文与切片都可能超过 MySQL TEXT 的 64KB，MySQL 上必须用 LONGTEXT
_LONG_TEXT = sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'rag_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column(
            'source_kind',
            sa.Enum('text', 'markdown', name='ragsourcekind'),
            nullable=False,
        ),
        sa.Column('content', _LONG_TEXT, nullable=False),
        sa.Column('char_count', sa.Integer(), nullable=False),
        sa.Column('chunk_count', sa.Integer(), nullable=False),
        sa.Column('embedding_model', sa.String(length=80), nullable=True),
        sa.Column('embedded_chunks', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_rag_documents_id'), 'rag_documents', ['id'], unique=False)
    op.create_index(op.f('ix_rag_documents_workspace_id'), 'rag_documents', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_rag_documents_created_by_id'), 'rag_documents', ['created_by_id'], unique=False)
    op.create_index(op.f('ix_rag_documents_title'), 'rag_documents', ['title'], unique=False)

    op.create_table(
        'rag_chunks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('content', _LONG_TEXT, nullable=False),
        sa.Column('char_start', sa.Integer(), nullable=False),
        sa.Column('char_end', sa.Integer(), nullable=False),
        sa.Column('token_count', sa.Integer(), nullable=False),
        sa.Column('embedding', _LONG_TEXT, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['rag_documents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'ordinal', name='uq_rag_chunk_ordinal'),
    )
    op.create_index(op.f('ix_rag_chunks_id'), 'rag_chunks', ['id'], unique=False)
    op.create_index(op.f('ix_rag_chunks_workspace_id'), 'rag_chunks', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_rag_chunks_document_id'), 'rag_chunks', ['document_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_rag_chunks_document_id'), table_name='rag_chunks')
    op.drop_index(op.f('ix_rag_chunks_workspace_id'), table_name='rag_chunks')
    op.drop_index(op.f('ix_rag_chunks_id'), table_name='rag_chunks')
    op.drop_table('rag_chunks')

    op.drop_index(op.f('ix_rag_documents_title'), table_name='rag_documents')
    op.drop_index(op.f('ix_rag_documents_created_by_id'), table_name='rag_documents')
    op.drop_index(op.f('ix_rag_documents_workspace_id'), table_name='rag_documents')
    op.drop_index(op.f('ix_rag_documents_id'), table_name='rag_documents')
    op.drop_table('rag_documents')
