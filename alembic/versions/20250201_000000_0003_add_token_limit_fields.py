"""add token limit fields

Revision ID: 0003
Revises: 0002
Create Date: 2025-02-01 00:00:00.000000

モデルにContext Window制限フィールド、会話に累積トークンフィールドを追加
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # modelsテーブルにContext Window関連フィールドを追加
    op.add_column(
        "models",
        sa.Column(
            "context_window",
            sa.Integer(),
            nullable=False,
            server_default="200000",
            comment="Context Window上限（トークン）",
        ),
    )
    op.add_column(
        "models",
        sa.Column(
            "max_output_tokens",
            sa.Integer(),
            nullable=False,
            server_default="64000",
            comment="最大出力トークン数",
        ),
    )
    op.add_column(
        "models",
        sa.Column(
            "supports_extended_context",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="拡張Context Window（1M等）対応可否",
        ),
    )
    op.add_column(
        "models",
        sa.Column(
            "extended_context_window",
            sa.Integer(),
            nullable=True,
            comment="拡張Context Window上限（トークン）",
        ),
    )

    # conversationsテーブルに累積トークンフィールドを追加
    op.add_column(
        "conversations",
        sa.Column(
            "total_input_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="累積入力トークン数",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "total_output_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="累積出力トークン数",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "estimated_context_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="推定コンテキストトークン数",
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "context_limit_reached",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="コンテキスト制限到達フラグ",
        ),
    )

    # 既存モデルデータの更新（Claude 4.5系のデフォルト値を設定）
    # Sonnet 4.5は1M Context Window（拡張）に対応
    op.execute("""
        UPDATE models
        SET
            context_window = 200000,
            max_output_tokens = 64000,
            supports_extended_context = CASE
                WHEN model_id LIKE '%sonnet%' THEN true
                ELSE false
            END,
            extended_context_window = CASE
                WHEN model_id LIKE '%sonnet%' THEN 1000000
                ELSE NULL
            END
        WHERE context_window = 200000
    """)


def downgrade() -> None:
    # conversationsテーブルからフィールドを削除
    op.drop_column("conversations", "context_limit_reached")
    op.drop_column("conversations", "estimated_context_tokens")
    op.drop_column("conversations", "total_output_tokens")
    op.drop_column("conversations", "total_input_tokens")

    # modelsテーブルからフィールドを削除
    op.drop_column("models", "extended_context_window")
    op.drop_column("models", "supports_extended_context")
    op.drop_column("models", "max_output_tokens")
    op.drop_column("models", "context_window")
