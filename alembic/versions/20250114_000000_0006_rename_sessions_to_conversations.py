"""session → conversation への名称変更

Revision ID: 0006
Revises: 0005
Create Date: 2025-01-14

変更内容:
- chat_sessionsテーブル → conversationsテーブルにリネーム
- chat_session_idカラム → conversation_idにリネーム（全テーブル）
- 関連するインデックスの更新
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. インデックスを先に削除（chat_sessionsテーブル）
    op.drop_index('ix_chat_sessions_tenant_user', table_name='chat_sessions')
    op.drop_index('ix_chat_sessions_created_at', table_name='chat_sessions')

    # 2. session_filesテーブルのインデックスを削除
    op.drop_index('ix_session_files_path_version', table_name='session_files')
    op.drop_index('ix_session_files_presented', table_name='session_files')
    op.drop_index('ix_session_files_source', table_name='session_files')
    op.drop_index('ix_session_files_chat_session_id', table_name='session_files')

    # 3. 外部キー制約を削除（依存関係の順序で）
    op.drop_constraint('messages_log_chat_session_id_fkey', 'messages_log', type_='foreignkey')
    op.drop_constraint('display_cache_chat_session_id_fkey', 'display_cache', type_='foreignkey')
    op.drop_constraint('usage_logs_chat_session_id_fkey', 'usage_logs', type_='foreignkey')
    op.drop_constraint('session_files_chat_session_id_fkey', 'session_files', type_='foreignkey')

    # 4. chat_sessionsテーブルをconversationsにリネーム
    op.rename_table('chat_sessions', 'conversations')

    # 5. conversationsテーブルのプライマリキーカラムをリネーム
    op.alter_column('conversations', 'chat_session_id', new_column_name='conversation_id')

    # 6. 各テーブルのchat_session_idカラムをconversation_idにリネーム
    op.alter_column('messages_log', 'chat_session_id', new_column_name='conversation_id')
    op.alter_column('display_cache', 'chat_session_id', new_column_name='conversation_id')
    op.alter_column('usage_logs', 'chat_session_id', new_column_name='conversation_id')
    op.alter_column('session_files', 'chat_session_id', new_column_name='conversation_id')
    op.alter_column('tool_execution_logs', 'chat_session_id', new_column_name='conversation_id')

    # 7. session_filesテーブルをconversation_filesにリネーム
    op.rename_table('session_files', 'conversation_files')

    # 8. 外部キー制約を再作成
    op.create_foreign_key(
        'messages_log_conversation_id_fkey',
        'messages_log', 'conversations',
        ['conversation_id'], ['conversation_id']
    )
    op.create_foreign_key(
        'display_cache_conversation_id_fkey',
        'display_cache', 'conversations',
        ['conversation_id'], ['conversation_id']
    )
    op.create_foreign_key(
        'usage_logs_conversation_id_fkey',
        'usage_logs', 'conversations',
        ['conversation_id'], ['conversation_id']
    )
    op.create_foreign_key(
        'conversation_files_conversation_id_fkey',
        'conversation_files', 'conversations',
        ['conversation_id'], ['conversation_id'],
        ondelete='CASCADE'
    )

    # 9. インデックスを再作成（conversationsテーブル）
    op.create_index('ix_conversations_tenant_user', 'conversations', ['tenant_id', 'user_id'])
    op.create_index('ix_conversations_created_at', 'conversations', ['created_at'])

    # 10. conversation_filesテーブルのインデックスを再作成
    op.create_index('ix_conversation_files_conversation_id', 'conversation_files', ['conversation_id'])
    op.create_index(
        'ix_conversation_files_path_version',
        'conversation_files',
        ['conversation_id', 'file_path', 'version']
    )
    op.create_index(
        'ix_conversation_files_presented',
        'conversation_files',
        ['conversation_id', 'is_presented'],
        postgresql_where=sa.text("is_presented = true")
    )
    op.create_index(
        'ix_conversation_files_source',
        'conversation_files',
        ['conversation_id', 'source']
    )

    # 11. messages_logのインデックスを再作成
    op.drop_index('ix_messages_log_chat_session_id', table_name='messages_log')
    op.create_index('ix_messages_log_conversation_id', 'messages_log', ['conversation_id'])

    # 12. display_cacheのインデックスを再作成
    op.drop_index('ix_display_cache_chat_session_id', table_name='display_cache')
    op.create_index('ix_display_cache_conversation_id', 'display_cache', ['conversation_id'])


def downgrade() -> None:
    # 逆順で元に戻す

    # 1. インデックスを削除
    op.drop_index('ix_display_cache_conversation_id', table_name='display_cache')
    op.create_index('ix_display_cache_chat_session_id', 'display_cache', ['conversation_id'])

    op.drop_index('ix_messages_log_conversation_id', table_name='messages_log')
    op.create_index('ix_messages_log_chat_session_id', 'messages_log', ['conversation_id'])

    op.drop_index('ix_conversation_files_source', table_name='conversation_files')
    op.drop_index('ix_conversation_files_presented', table_name='conversation_files')
    op.drop_index('ix_conversation_files_path_version', table_name='conversation_files')
    op.drop_index('ix_conversation_files_conversation_id', table_name='conversation_files')

    op.drop_index('ix_conversations_created_at', table_name='conversations')
    op.drop_index('ix_conversations_tenant_user', table_name='conversations')

    # 2. 外部キー制約を削除
    op.drop_constraint('conversation_files_conversation_id_fkey', 'conversation_files', type_='foreignkey')
    op.drop_constraint('usage_logs_conversation_id_fkey', 'usage_logs', type_='foreignkey')
    op.drop_constraint('display_cache_conversation_id_fkey', 'display_cache', type_='foreignkey')
    op.drop_constraint('messages_log_conversation_id_fkey', 'messages_log', type_='foreignkey')

    # 3. conversation_filesをsession_filesにリネーム
    op.rename_table('conversation_files', 'session_files')

    # 4. カラムを元に戻す
    op.alter_column('tool_execution_logs', 'conversation_id', new_column_name='chat_session_id')
    op.alter_column('session_files', 'conversation_id', new_column_name='chat_session_id')
    op.alter_column('usage_logs', 'conversation_id', new_column_name='chat_session_id')
    op.alter_column('display_cache', 'conversation_id', new_column_name='chat_session_id')
    op.alter_column('messages_log', 'conversation_id', new_column_name='chat_session_id')

    # 5. conversationsテーブルのカラムを元に戻す
    op.alter_column('conversations', 'conversation_id', new_column_name='chat_session_id')

    # 6. conversationsをchat_sessionsにリネーム
    op.rename_table('conversations', 'chat_sessions')

    # 7. 外部キー制約を再作成
    op.create_foreign_key(
        'session_files_chat_session_id_fkey',
        'session_files', 'chat_sessions',
        ['chat_session_id'], ['chat_session_id'],
        ondelete='CASCADE'
    )
    op.create_foreign_key(
        'usage_logs_chat_session_id_fkey',
        'usage_logs', 'chat_sessions',
        ['chat_session_id'], ['chat_session_id']
    )
    op.create_foreign_key(
        'display_cache_chat_session_id_fkey',
        'display_cache', 'chat_sessions',
        ['chat_session_id'], ['chat_session_id']
    )
    op.create_foreign_key(
        'messages_log_chat_session_id_fkey',
        'messages_log', 'chat_sessions',
        ['chat_session_id'], ['chat_session_id']
    )

    # 8. session_filesのインデックスを再作成
    op.create_index('ix_session_files_chat_session_id', 'session_files', ['chat_session_id'])
    op.create_index('ix_session_files_source', 'session_files', ['chat_session_id', 'source'])
    op.create_index(
        'ix_session_files_presented',
        'session_files',
        ['chat_session_id', 'is_presented'],
        postgresql_where=sa.text("is_presented = true")
    )
    op.create_index(
        'ix_session_files_path_version',
        'session_files',
        ['chat_session_id', 'file_path', 'version']
    )

    # 9. chat_sessionsのインデックスを再作成
    op.create_index('ix_chat_sessions_created_at', 'chat_sessions', ['created_at'])
    op.create_index('ix_chat_sessions_tenant_user', 'chat_sessions', ['tenant_id', 'user_id'])
