"""simplify mcp_servers to openapi only

Revision ID: 0005
Revises: 0004
Create Date: 2025-02-12 00:00:00.000000

MCPサーバー登録をOpenAPIタイプのみに簡素化。
不要なカラム（type, url, command, args, tools）を削除。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("mcp_servers", "type")
    op.drop_column("mcp_servers", "url")
    op.drop_column("mcp_servers", "command")
    op.drop_column("mcp_servers", "args")
    op.drop_column("mcp_servers", "tools")


def downgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column("tools", postgresql.JSON, nullable=True),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("args", postgresql.JSON, nullable=True),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("command", sa.String(500), nullable=True),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("url", sa.String(500), nullable=True),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("type", sa.String(20), nullable=False, server_default="openapi"),
    )
