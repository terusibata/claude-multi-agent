"""MCPサーバーにツール定義カラムを追加

Revision ID: 0003
Revises: 0002
Create Date: 2025-01-12

変更内容:
- mcp_serversテーブルにtoolsカラムを追加
  - tools: ツール定義のJSONリスト（builtinタイプの場合に使用）
- display_cacheテーブルを削除（使用廃止のため）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # mcp_serversテーブルにtoolsカラムを追加
    op.add_column(
        'mcp_servers',
        sa.Column('tools', postgresql.JSON(), nullable=True)
    )

    # display_cacheテーブルを削除
    op.drop_table('display_cache')


def downgrade() -> None:
    # display_cacheテーブルを再作成
    op.create_table(
        'display_cache',
        sa.Column('cache_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            'chat_session_id',
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey('chat_sessions.chat_session_id'),
            nullable=False,
            index=True
        ),
        sa.Column('turn_number', sa.Integer(), nullable=False),
        sa.Column('user_message', sa.Text(), nullable=True),
        sa.Column('assistant_message', sa.Text(), nullable=True),
        sa.Column('tools_summary', postgresql.JSON(), nullable=True),
        sa.Column('metadata', postgresql.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # mcp_serversテーブルからtoolsカラムを削除
    op.drop_column('mcp_servers', 'tools')
