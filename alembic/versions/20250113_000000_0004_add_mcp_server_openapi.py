"""MCPサーバーにOpenAPI関連カラムを追加

Revision ID: 0004
Revises: 0003
Create Date: 2025-01-13

変更内容:
- mcp_serversテーブルにopenapi_specカラムを追加
  - openapi_spec: OpenAPI仕様のJSON（openapiタイプの場合に使用）
- mcp_serversテーブルにopenapi_base_urlカラムを追加
  - openapi_base_url: OpenAPI APIのベースURL（openapiタイプの場合に使用）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # mcp_serversテーブルにopenapi_specカラムを追加
    op.add_column(
        'mcp_servers',
        sa.Column('openapi_spec', postgresql.JSON(), nullable=True)
    )

    # mcp_serversテーブルにopenapi_base_urlカラムを追加
    op.add_column(
        'mcp_servers',
        sa.Column('openapi_base_url', sa.String(500), nullable=True)
    )


def downgrade() -> None:
    # mcp_serversテーブルからopenapi_base_urlカラムを削除
    op.drop_column('mcp_servers', 'openapi_base_url')

    # mcp_serversテーブルからopenapi_specカラムを削除
    op.drop_column('mcp_servers', 'openapi_spec')
