"""シンプルチャットテーブル追加

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ===========================================
    # シンプルチャットテーブル
    # ===========================================
    op.create_table(
        'simple_chats',
        sa.Column('chat_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('tenant_id', sa.String(100), sa.ForeignKey('tenants.tenant_id'), nullable=False, index=True),
        sa.Column('user_id', sa.String(100), nullable=False, index=True),
        sa.Column('model_id', sa.String(100), sa.ForeignKey('models.model_id'), nullable=False),
        sa.Column('application_type', sa.String(100), nullable=False, index=True),
        sa.Column('system_prompt', sa.Text, nullable=False),
        sa.Column('title', sa.String(500), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ===========================================
    # シンプルチャットメッセージテーブル
    # ===========================================
    op.create_table(
        'simple_chat_messages',
        sa.Column('message_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('chat_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('simple_chats.chat_id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('message_seq', sa.Integer, nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ===========================================
    # usage_logsにsimple_chat_idカラム追加
    # ===========================================
    op.add_column(
        'usage_logs',
        sa.Column('simple_chat_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('simple_chats.chat_id'), nullable=True)
    )

    # ===========================================
    # インデックス作成
    # ===========================================
    op.create_index('ix_simple_chats_tenant_user', 'simple_chats', ['tenant_id', 'user_id'])
    op.create_index('ix_simple_chats_created_at', 'simple_chats', ['created_at'])
    op.create_index('ix_simple_chats_application_type', 'simple_chats', ['application_type'])
    op.create_index('ix_simple_chat_messages_chat_seq', 'simple_chat_messages', ['chat_id', 'message_seq'])


def downgrade() -> None:
    # インデックス削除
    op.drop_index('ix_simple_chat_messages_chat_seq')
    op.drop_index('ix_simple_chats_application_type')
    op.drop_index('ix_simple_chats_created_at')
    op.drop_index('ix_simple_chats_tenant_user')

    # usage_logsからsimple_chat_idカラム削除
    op.drop_column('usage_logs', 'simple_chat_id')

    # テーブル削除
    op.drop_table('simple_chat_messages')
    op.drop_table('simple_chats')
