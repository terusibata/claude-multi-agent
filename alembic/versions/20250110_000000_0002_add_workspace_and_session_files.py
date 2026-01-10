"""セッション専用ワークスペースとファイル管理機能を追加

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-10

変更内容:
- chat_sessionsテーブルにワークスペース関連カラムを追加
  - workspace_enabled: ワークスペース有効フラグ
  - workspace_path: ワークスペースパス
  - workspace_created_at: ワークスペース作成日時
- session_filesテーブルを新規作成
  - セッション内のファイル管理
  - バージョン管理対応
  - Presented file機能対応
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
    # chat_sessionsテーブルにワークスペース関連カラムを追加
    op.add_column(
        'chat_sessions',
        sa.Column('workspace_enabled', sa.Boolean(), nullable=False, server_default='false')
    )
    op.add_column(
        'chat_sessions',
        sa.Column('workspace_path', sa.String(500), nullable=True)
    )
    op.add_column(
        'chat_sessions',
        sa.Column('workspace_created_at', sa.DateTime(timezone=True), nullable=True)
    )

    # session_filesテーブルを作成
    op.create_table(
        'session_files',
        sa.Column('file_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            'chat_session_id',
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey('chat_sessions.chat_session_id', ondelete='CASCADE'),
            nullable=False,
            index=True
        ),
        sa.Column('file_path', sa.String(1000), nullable=False),
        sa.Column('original_name', sa.String(500), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('mime_type', sa.String(200), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('source', sa.String(50), nullable=False, server_default='user_upload'),
        sa.Column('is_presented', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('checksum', sa.String(64), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    # インデックス作成
    op.create_index(
        'ix_session_files_path_version',
        'session_files',
        ['chat_session_id', 'file_path', 'version']
    )
    op.create_index(
        'ix_session_files_presented',
        'session_files',
        ['chat_session_id', 'is_presented'],
        postgresql_where=sa.text("is_presented = true")
    )
    op.create_index(
        'ix_session_files_source',
        'session_files',
        ['chat_session_id', 'source']
    )


def downgrade() -> None:
    # インデックス削除
    op.drop_index('ix_session_files_source')
    op.drop_index('ix_session_files_presented')
    op.drop_index('ix_session_files_path_version')

    # session_filesテーブルを削除
    op.drop_table('session_files')

    # chat_sessionsテーブルからワークスペース関連カラムを削除
    op.drop_column('chat_sessions', 'workspace_created_at')
    op.drop_column('chat_sessions', 'workspace_path')
    op.drop_column('chat_sessions', 'workspace_enabled')
