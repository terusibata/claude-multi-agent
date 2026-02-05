"""add original_relative_path to conversation_files

Revision ID: 0004
Revises: 0003
Create Date: 2025-02-05 00:00:00.000000

ファイルアップロード時の元の相対パス（表示用）を保存するカラムを追加
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # conversation_filesテーブルにoriginal_relative_pathカラムを追加
    op.add_column(
        "conversation_files",
        sa.Column(
            "original_relative_path",
            sa.String(1000),
            nullable=True,
            comment="元の相対パス（表示用。例: api/users/route.ts）",
        ),
    )


def downgrade() -> None:
    op.drop_column("conversation_files", "original_relative_path")
