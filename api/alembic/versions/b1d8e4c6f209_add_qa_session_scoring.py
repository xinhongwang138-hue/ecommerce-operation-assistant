"""add qa_sessions, qa_messages and qa_scores for P09 assessment

Revision ID: b1d8e4c6f209
Revises: a7c31f9d2b84
Create Date: 2026-10-30 19:41:52.310554

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = 'b1d8e4c6f209'
down_revision: Union[str, Sequence[str], None] = 'a7c31f9d2b84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 会话正文可能远超 MySQL TEXT 的 64KB（一次长对话），MySQL 上必须用 LONGTEXT
_LONG_TEXT = sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'qa_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=120), nullable=False),
        sa.Column('agent_name', sa.String(length=80), nullable=False),
        sa.Column('channel', sa.String(length=40), nullable=False),
        sa.Column(
            'source_kind',
            sa.Enum('import', 'agent', name='qasessionsource'),
            nullable=False,
        ),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('message_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('workspace_id', 'external_id', name='uq_qa_session_external'),
    )
    op.create_index(op.f('ix_qa_sessions_id'), 'qa_sessions', ['id'], unique=False)
    op.create_index(op.f('ix_qa_sessions_workspace_id'), 'qa_sessions', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_qa_sessions_agent_name'), 'qa_sessions', ['agent_name'], unique=False)
    op.create_index(op.f('ix_qa_sessions_source_kind'), 'qa_sessions', ['source_kind'], unique=False)
    op.create_index(op.f('ix_qa_sessions_started_at'), 'qa_sessions', ['started_at'], unique=False)

    op.create_table(
        'qa_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('role', sa.Enum('customer', 'agent', name='qarole'), nullable=False),
        sa.Column('content', _LONG_TEXT, nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['qa_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', 'seq', name='uq_qa_message_seq'),
    )
    op.create_index(op.f('ix_qa_messages_id'), 'qa_messages', ['id'], unique=False)
    op.create_index(op.f('ix_qa_messages_workspace_id'), 'qa_messages', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_qa_messages_session_id'), 'qa_messages', ['session_id'], unique=False)

    op.create_table(
        'qa_scores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('total', sa.Numeric(precision=5, scale=1), nullable=False),
        sa.Column('grade', sa.String(length=2), nullable=False),
        sa.Column('breakdown', sa.JSON(), nullable=False),
        sa.Column('redline', sa.Boolean(), nullable=False),
        sa.Column('redline_rules', sa.JSON(), nullable=False),
        sa.Column('rule_version', sa.String(length=20), nullable=False),
        sa.Column('scored_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['qa_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', name='uq_qa_score_session'),
    )
    op.create_index(op.f('ix_qa_scores_id'), 'qa_scores', ['id'], unique=False)
    op.create_index(op.f('ix_qa_scores_workspace_id'), 'qa_scores', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_qa_scores_session_id'), 'qa_scores', ['session_id'], unique=False)
    op.create_index(op.f('ix_qa_scores_total'), 'qa_scores', ['total'], unique=False)
    op.create_index(op.f('ix_qa_scores_grade'), 'qa_scores', ['grade'], unique=False)
    op.create_index(op.f('ix_qa_scores_redline'), 'qa_scores', ['redline'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    for index in ('ix_qa_scores_redline', 'ix_qa_scores_grade', 'ix_qa_scores_total',
                  'ix_qa_scores_session_id', 'ix_qa_scores_workspace_id', 'ix_qa_scores_id'):
        op.drop_index(op.f(index), table_name='qa_scores')
    op.drop_table('qa_scores')

    for index in ('ix_qa_messages_session_id', 'ix_qa_messages_workspace_id', 'ix_qa_messages_id'):
        op.drop_index(op.f(index), table_name='qa_messages')
    op.drop_table('qa_messages')

    for index in ('ix_qa_sessions_started_at', 'ix_qa_sessions_source_kind',
                  'ix_qa_sessions_agent_name', 'ix_qa_sessions_workspace_id', 'ix_qa_sessions_id'):
        op.drop_index(op.f(index), table_name='qa_sessions')
    op.drop_table('qa_sessions')
