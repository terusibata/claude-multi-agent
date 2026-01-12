"""
ビルトインMCPツールの実装
アプリケーション組み込みのMCPサーバーとツール
"""
import os
import mimetypes
from pathlib import Path
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)


# ビルトインツールの定義
BUILTIN_TOOL_DEFINITIONS = {
    "present_files": {
        "name": "present_files",
        "description": "AIが作成・編集したファイルをユーザーに提示する。ファイルパスのリストと説明を受け取り、ユーザーに提示する情報を返す。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "提示するファイルパスのリスト"
                },
                "description": {
                    "type": "string",
                    "description": "ファイルの説明"
                }
            },
            "required": ["file_paths"]
        }
    }
}


async def present_files_handler(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    present_filesツールの実行ハンドラー

    Args:
        args: ツール引数
            - file_paths: ファイルパスのリスト
            - description: ファイルの説明
        context: 実行コンテキスト
            - workspace_cwd: ワークスペースのカレントディレクトリ
            - tenant_id: テナントID
            - chat_session_id: チャットセッションID

    Returns:
        ツール実行結果
    """
    file_paths = args.get("file_paths", [])
    description = args.get("description", "")
    workspace_cwd = context.get("workspace_cwd", "") if context else ""

    files_info = []
    for file_path in file_paths:
        # 相対パスの場合はworkspace_cwdを基準に解決
        if not os.path.isabs(file_path) and workspace_cwd:
            full_path = os.path.join(workspace_cwd, file_path)
        else:
            full_path = file_path

        path = Path(full_path)
        if path.exists():
            # MIMEタイプを推測
            mime_type, _ = mimetypes.guess_type(str(path))

            files_info.append({
                "path": str(path.absolute()),
                "relative_path": file_path,
                "name": path.name,
                "size": path.stat().st_size,
                "mime_type": mime_type or "application/octet-stream",
                "exists": True
            })
            logger.info(
                "ファイル提示: ファイル検出",
                file_path=file_path,
                full_path=str(path.absolute()),
                size=path.stat().st_size
            )
        else:
            files_info.append({
                "path": full_path,
                "relative_path": file_path,
                "name": os.path.basename(file_path),
                "exists": False
            })
            logger.warning(
                "ファイル提示: ファイルが存在しない",
                file_path=file_path,
                full_path=full_path
            )

    # 存在するファイルのみをフィルタ
    existing_files = [f for f in files_info if f.get("exists")]
    missing_files = [f for f in files_info if not f.get("exists")]

    # 結果メッセージを構築
    result_text = ""
    if description:
        result_text += f"説明: {description}\n\n"

    if existing_files:
        result_text += "提示されたファイル:\n"
        for f in existing_files:
            result_text += f"- {f['name']}: {f['path']} ({f['size']} bytes)\n"

    if missing_files:
        result_text += "\n見つからなかったファイル:\n"
        for f in missing_files:
            result_text += f"- {f['relative_path']}\n"

    return {
        "content": [{
            "type": "text",
            "text": result_text.strip()
        }],
        "files": files_info,
        "presented_files": existing_files,
        "description": description
    }


# ビルトインツールハンドラーのマッピング
BUILTIN_TOOL_HANDLERS: dict[str, Callable] = {
    "present_files": present_files_handler
}


def get_builtin_tool_definition(tool_name: str) -> dict[str, Any] | None:
    """
    ビルトインツールの定義を取得

    Args:
        tool_name: ツール名

    Returns:
        ツール定義（存在しない場合はNone）
    """
    return BUILTIN_TOOL_DEFINITIONS.get(tool_name)


def get_all_builtin_tool_definitions() -> list[dict[str, Any]]:
    """
    全ビルトインツールの定義を取得

    Returns:
        ツール定義のリスト
    """
    return list(BUILTIN_TOOL_DEFINITIONS.values())


async def execute_builtin_tool(
    tool_name: str,
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    ビルトインツールを実行

    Args:
        tool_name: ツール名
        args: ツール引数
        context: 実行コンテキスト

    Returns:
        ツール実行結果

    Raises:
        ValueError: ツールが存在しない場合
    """
    handler = BUILTIN_TOOL_HANDLERS.get(tool_name)
    if not handler:
        raise ValueError(f"Unknown builtin tool: {tool_name}")

    return await handler(args, context)
