"""Agent Skillsにスラッシュコマンド関連カラムを追加

Revision ID: 0005
Revises: 0004
Create Date: 2025-01-13

変更内容:
- agent_skillsテーブルにslash_commandカラムを追加
  - slash_command: スラッシュコマンド表示名（例: /ServiceNowドキュメント検索）
- agent_skillsテーブルにslash_command_descriptionカラムを追加
  - slash_command_description: スラッシュコマンドの説明
- agent_skillsテーブルにis_user_selectableカラムを追加
  - is_user_selectable: ユーザーがUIから選択可能かどうか
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agent_skillsテーブルにslash_commandカラムを追加
    op.add_column(
        'agent_skills',
        sa.Column(
            'slash_command',
            sa.String(100),
            nullable=True,
            comment='スラッシュコマンド表示名（例: /ServiceNowドキュメント検索）'
        )
    )

    # agent_skillsテーブルにslash_command_descriptionカラムを追加
    op.add_column(
        'agent_skills',
        sa.Column(
            'slash_command_description',
            sa.String(500),
            nullable=True,
            comment='スラッシュコマンドの説明（オートコンプリート時に表示）'
        )
    )

    # agent_skillsテーブルにis_user_selectableカラムを追加
    op.add_column(
        'agent_skills',
        sa.Column(
            'is_user_selectable',
            sa.Boolean(),
            nullable=False,
            server_default='true',
            comment='ユーザーがUIから選択可能かどうか'
        )
    )


def downgrade() -> None:
    # agent_skillsテーブルからis_user_selectableカラムを削除
    op.drop_column('agent_skills', 'is_user_selectable')

    # agent_skillsテーブルからslash_command_descriptionカラムを削除
    op.drop_column('agent_skills', 'slash_command_description')

    # agent_skillsテーブルからslash_commandカラムを削除
    op.drop_column('agent_skills', 'slash_command')
