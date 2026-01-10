"""アーティファクトテーブル追加

Revision ID: 0002
Revises: 0001
Create Date: 2025-01-10

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
    # アーティファクトテーブル作成
    op.create_table(
        'artifacts',
        sa.Column('artifact_id', postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column('chat_session_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('chat_sessions.chat_session_id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('turn_number', sa.Integer, nullable=False),
        sa.Column('tool_execution_log_id', postgresql.UUID(as_uuid=False), sa.ForeignKey('tool_execution_logs.tool_execution_log_id', ondelete='SET NULL'), nullable=True),
        sa.Column('artifact_type', sa.String(50), nullable=False, server_default='file'),
        sa.Column('filename', sa.String(500), nullable=False),
        sa.Column('file_path', sa.String(1000), nullable=True),
        sa.Column('s3_key', sa.String(1000), nullable=True),
        sa.Column('content', sa.Text, nullable=True),
        sa.Column('mime_type', sa.String(100), nullable=True),
        sa.Column('file_size', sa.Integer, nullable=True),
        sa.Column('tool_name', sa.String(50), nullable=False),
        sa.Column('title', sa.String(500), nullable=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # インデックス作成
    op.create_index('ix_artifacts_chat_session_id', 'artifacts', ['chat_session_id'])
    op.create_index('ix_artifacts_created_at', 'artifacts', ['created_at'])


def downgrade() -> None:
    # インデックス削除
    op.drop_index('ix_artifacts_created_at', table_name='artifacts')
    op.drop_index('ix_artifacts_chat_session_id', table_name='artifacts')

    # テーブル削除
    op.drop_table('artifacts')
