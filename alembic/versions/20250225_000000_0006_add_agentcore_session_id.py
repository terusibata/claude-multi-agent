"""add agentcore_session_id to conversations

Revision ID: 0006
Revises: 0005
Create Date: 2025-02-25 00:00:00.000000

AgentCore Runtime移行に伴い、conversations テーブルに
agentcore_session_id カラムを追加。
AgentCoreのセッションID（コンテナ親和性用）をconversationにマッピングする。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("agentcore_session_id", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "agentcore_session_id")
