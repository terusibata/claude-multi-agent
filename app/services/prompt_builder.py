"""
システムプロンプトビルダー

コンテナ内エージェントに渡すシステムプロンプトを構築する。
"""

import re

import structlog

from app.schemas.execute import ExecuteRequest

logger = structlog.get_logger(__name__)


def build_system_prompt(request: ExecuteRequest, skills_synced: bool) -> str:
    """コンテナに渡すシステムプロンプトを構築

    Args:
        request: 実行リクエスト
        skills_synced: スキルファイルが同期済みかどうか

    Returns:
        構築されたシステムプロンプト文字列
    """
    parts = [
        "あなたのワークスペースは /workspace です。"
        "ファイルの作成・編集は必ず /workspace ディレクトリ内で行ってください。"
        "相対パスを使用してください（例: hello.py, docs/readme.md）。"
        "/tmp や他のディレクトリへの書き込みは禁止です。",
        "",
        "## ファイル作成ルール",
        "- **相対パスのみ使用**（例: `hello.py`）。絶対パス（/tmp/等）は禁止",
        "- ファイル作成後は `mcp__file-presentation__present_files` で提示",
        '- file_paths は配列で指定: `["hello.py"]`',
        "- **サブエージェント（Task）がファイルを作成した場合も、その完了後に必ず `mcp__file-presentation__present_files` を呼び出してください**",
        "",
        "## ファイル読み込み",
        "ワークスペースのファイルは以下の手順で読んでください：",
        "1. list_workspace_files でファイル一覧を確認",
        "2. 各ファイル形式に対応するスキルで読み取り（PDF/Excel/Word/PowerPoint → 対応するDefault Skillを使用）",
        "3. 画像の内容を理解する必要がある場合は read_image_file を使用（promptパラメータで知りたい内容を指定）",
        "※ テキスト/CSV/JSONファイルは従来のReadツールも使用可能",
    ]

    # preferred_skills 指示（インジェクション防止のためバリデーション付き）
    if request.preferred_skills:
        valid_skills = []
        for skill_name in request.preferred_skills:
            if re.match(r"^[a-zA-Z0-9_\-\u3040-\u9FFF]+$", skill_name):
                valid_skills.append(skill_name)
            else:
                logger.warning(
                    "不正なスキル名を除外",
                    skill_name=skill_name[:50],
                )
        if valid_skills:
            parts.append("")
            parts.append("## 優先スキル")
            parts.append(
                "以下のスキルが利用可能です。関連するタスクには優先的に使用してください:"
            )
            for skill_name in valid_skills:
                parts.append(f"- {skill_name}")

    return "\n".join(parts)
