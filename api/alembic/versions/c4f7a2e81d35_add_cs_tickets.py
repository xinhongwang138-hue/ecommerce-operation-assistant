"""add cs_tickets for P01 human handoff

Revision ID: c4f7a2e81d35
Revises: b1d8e4c6f209
Create Date: 2026-10-30 20:22:41.664902

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = 'c4f7a2e81d35'
down_revision: Union[str, Sequence[str], None] = 'b1d8e4c6f209'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LONG_TEXT = sa.Text().with_variant(mysql.LONGTEXT(), "mysql")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'cs_tickets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.String(length=200), nullable=False),
        sa.Column('trigger', sa.String(length=40), nullable=False),
        sa.Column('priority', sa.String(length=10), nullable=False),
        sa.Column(
            'status',
            sa.Enum('open', 'claimed', 'closed', name='csticketstatus'),
            nullable=False,
        ),
        sa.Column('summary', _LONG_TEXT, nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['agent_conversations.id'], ),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_cs_tickets_id'), 'cs_tickets', ['id'], unique=False)
    op.create_index(op.f('ix_cs_tickets_workspace_id'), 'cs_tickets', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_cs_tickets_conversation_id'), 'cs_tickets', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_cs_tickets_trigger'), 'cs_tickets', ['trigger'], unique=False)
    op.create_index(op.f('ix_cs_tickets_priority'), 'cs_tickets', ['priority'], unique=False)
    op.create_index(op.f('ix_cs_tickets_status'), 'cs_tickets', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    for index in ('ix_cs_tickets_status', 'ix_cs_tickets_priority', 'ix_cs_tickets_trigger',
                  'ix_cs_tickets_conversation_id', 'ix_cs_tickets_workspace_id', 'ix_cs_tickets_id'):
        op.drop_index(op.f(index), table_name='cs_tickets')
    op.drop_table('cs_tickets')
