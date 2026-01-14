"""初期スキーマ作成

Revision ID: 0001
Revises:
Create Date: 2025-01-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ===========================================
    # モデル定義テーブル
    # ===========================================
    op.create_table(
        'models',
        sa.Column('model_id', sa.String(100), primary_key=True),
        sa.Column('display_name', sa.String(200), nullable=False),
        sa.Column('bedrock_model_id', sa.String(200), nullable=False),
        sa.Column('model_region', sa.String(50), nullable=True),
        sa.Column('input_token_price', sa.DECIMAL(10, 6), nullable=False, server_default='0'),
        sa.Column('output_token_price', sa.DECIMAL(10, 6), nullable=False, server_default='0'),
        sa.Column('cache_creation_price', sa.DECIMAL(10, 6), nullable=False, server_default='0'),
        sa.Column('cache_read_price', sa.DECIMAL(10, 6), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ===========================================
    # テナントテーブル
    # ===========================================
    op.create_table(
        'tenants',
        sa.Column('tenant_id', sa.String(100), primary_key=True),
        sa.Column('system_prompt', sa.Text, nullable=True),
        sa.Column('model_id', sa.String(100), sa.ForeignKey('models.model_id'), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ===========================================
    # Agent Skillsテーブル
    # ===========================================
    op.create_table(
        'agent_skills',
        sa.Column('skill_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('tenant_id', sa.String(100), sa.ForeignKey('tenants.tenant_id'), nullable=False, index=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('display_title', sa.String(300), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('file_path', sa.String(500), nullable=False),
        sa.Column('slash_command', sa.String(200), nullable=True),
        sa.Column('slash_command_description', sa.String(500), nullable=True),
        sa.Column('is_user_selectable', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ===========================================
    # MCPサーバーテーブル
    # ===========================================
    op.create_table(
        'mcp_servers',
        sa.Column('mcp_server_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('tenant_id', sa.String(100), sa.ForeignKey('tenants.tenant_id'), nullable=False, index=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('display_name', sa.String(300), nullable=True),
        sa.Column('type', sa.String(20), nullable=False),
        sa.Column('url', sa.String(500), nullable=True),
        sa.Column('command', sa.String(500), nullable=True),
        sa.Column('args', postgresql.JSON, nullable=True),
        sa.Column('env', postgresql.JSON, nullable=True),
        sa.Column('headers_template', postgresql.JSON, nullable=True),
        sa.Column('allowed_tools', postgresql.JSON, nullable=True),
        sa.Column('tools', postgresql.JSON, nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('openapi_spec', postgresql.JSON, nullable=True),
        sa.Column('openapi_base_url', sa.String(500), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ===========================================
    # 会話テーブル
    # ===========================================
    op.create_table(
        'conversations',
        sa.Column('conversation_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('session_id', sa.String(200), nullable=True),
        sa.Column('tenant_id', sa.String(100), sa.ForeignKey('tenants.tenant_id'), nullable=False, index=True),
        sa.Column('user_id', sa.String(100), nullable=False, index=True),
        sa.Column('model_id', sa.String(100), sa.ForeignKey('models.model_id'), nullable=False),
        sa.Column('title', sa.String(500), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('enable_workspace', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('workspace_path', sa.String(500), nullable=True),
        sa.Column('workspace_created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ===========================================
    # 会話ファイルテーブル
    # ===========================================
    op.create_table(
        'conversation_files',
        sa.Column('file_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('conversations.conversation_id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('file_path', sa.String(1000), nullable=False),
        sa.Column('original_name', sa.String(500), nullable=False),
        sa.Column('file_size', sa.BigInteger, nullable=False, server_default='0'),
        sa.Column('mime_type', sa.String(200), nullable=True),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('source', sa.String(50), nullable=False, server_default='user_upload'),
        sa.Column('is_presented', sa.Boolean, nullable=False, server_default='false'),
        sa.Column('checksum', sa.String(64), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # ===========================================
    # メッセージログテーブル
    # ===========================================
    op.create_table(
        'messages_log',
        sa.Column('message_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('conversations.conversation_id'), nullable=False, index=True),
        sa.Column('message_seq', sa.Integer, nullable=False),
        sa.Column('message_type', sa.String(50), nullable=False),
        sa.Column('message_subtype', sa.String(50), nullable=True),
        sa.Column('content', postgresql.JSON, nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ===========================================
    # 使用状況ログテーブル
    # ===========================================
    op.create_table(
        'usage_logs',
        sa.Column('usage_log_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('tenant_id', sa.String(100), sa.ForeignKey('tenants.tenant_id'), nullable=False, index=True),
        sa.Column('user_id', sa.String(100), nullable=False, index=True),
        sa.Column('model_id', sa.String(100), sa.ForeignKey('models.model_id'), nullable=False),
        sa.Column('session_id', sa.String(200), nullable=True),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('conversations.conversation_id'), nullable=True),
        sa.Column('input_tokens', sa.Integer, nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer, nullable=False, server_default='0'),
        sa.Column('cache_creation_tokens', sa.Integer, nullable=False, server_default='0'),
        sa.Column('cache_read_tokens', sa.Integer, nullable=False, server_default='0'),
        sa.Column('total_tokens', sa.Integer, nullable=False, server_default='0'),
        sa.Column('cost_usd', sa.DECIMAL(10, 6), nullable=False, server_default='0'),
        sa.Column('executed_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ===========================================
    # ツール実行ログテーブル
    # ===========================================
    op.create_table(
        'tool_execution_logs',
        sa.Column('tool_log_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('session_id', sa.String(200), nullable=False, index=True),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=False), nullable=True, index=True),
        sa.Column('tool_name', sa.String(200), nullable=False, index=True),
        sa.Column('tool_use_id', sa.String(200), nullable=True),
        sa.Column('tool_input', postgresql.JSON, nullable=True),
        sa.Column('tool_output', postgresql.JSON, nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='success'),
        sa.Column('execution_time_ms', sa.Integer, nullable=True),
        sa.Column('executed_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ===========================================
    # インデックス作成
    # ===========================================
    op.create_index('ix_tenants_status', 'tenants', ['status'])
    op.create_index('ix_conversations_tenant_user', 'conversations', ['tenant_id', 'user_id'])
    op.create_index('ix_conversations_created_at', 'conversations', ['created_at'])
    op.create_index('ix_usage_logs_tenant_executed', 'usage_logs', ['tenant_id', 'executed_at'])
    op.create_index('ix_tool_logs_executed_at', 'tool_execution_logs', ['executed_at'])


def downgrade() -> None:
    # インデックス削除
    op.drop_index('ix_tool_logs_executed_at')
    op.drop_index('ix_usage_logs_tenant_executed')
    op.drop_index('ix_conversations_created_at')
    op.drop_index('ix_conversations_tenant_user')
    op.drop_index('ix_tenants_status')

    # テーブル削除（依存関係の順序で）
    op.drop_table('tool_execution_logs')
    op.drop_table('usage_logs')
    op.drop_table('messages_log')
    op.drop_table('conversation_files')
    op.drop_table('conversations')
    op.drop_table('mcp_servers')
    op.drop_table('agent_skills')
    op.drop_table('tenants')
    op.drop_table('models')
