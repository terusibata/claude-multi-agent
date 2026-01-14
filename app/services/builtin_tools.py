"""
ビルトインMCPツールの実装
アプリケーション組み込みのMCPサーバーとツール
"""
import os
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)


# ビルトインツールの定義
BUILTIN_TOOL_DEFINITIONS = {
    "present_files": {
        "name": "present_files",
        "description": "AIが作成・編集したファイルをユーザーに提示する。ファイルパスのリストと説明を受け取り、ユーザーに提示する情報を返す。Write/Edit/NotebookEditでファイルを作成・編集した後は、必ずこのツールを使用してユーザーにファイルを提示してください。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "提示するファイルパスのリスト（作成・編集したファイルのパス）"
                },
                "description": {
                    "type": "string",
                    "description": "ファイルの説明（何を作成・編集したか）"
                }
            },
            "required": ["file_paths", "description"]
        }
    }
}


def create_present_files_handler(workspace_cwd: str = ""):
    """
    present_filesツールのハンドラーを作成

    Args:
        workspace_cwd: ワークスペースのカレントディレクトリ

    Returns:
        ツールハンドラー関数
    """
    async def present_files_handler(args: dict[str, Any]) -> dict[str, Any]:
        """
        present_filesツールの実行ハンドラー

        Args:
            args: ツール引数
                - file_paths: ファイルパスのリスト（または単一の文字列パス）
                - description: ファイルの説明

        Returns:
            ツール実行結果
        """
        file_paths_input = args.get("file_paths", [])
        description = args.get("description", "")

        # file_pathsの正規化
        if isinstance(file_paths_input, str):
            # JSON文字列の場合はパース
            if file_paths_input.startswith("["):
                import json
                try:
                    file_paths = json.loads(file_paths_input)
                except json.JSONDecodeError:
                    file_paths = [file_paths_input]
            else:
                file_paths = [file_paths_input]
            logger.info("ファイル提示: file_paths正規化", original=file_paths_input, result=file_paths)
        else:
            file_paths = file_paths_input

        files_info = []
        for file_path in file_paths:
            # 相対パスの場合はworkspace_cwdを基準に解決
            if not os.path.isabs(file_path) and workspace_cwd:
                full_path = os.path.join(workspace_cwd, file_path)
            else:
                full_path = file_path

            path = Path(full_path)
            if path.exists() and path.is_file():
                # MIMEタイプを推測
                mime_type, _ = mimetypes.guess_type(str(path))

                # ファイルがワークスペース外にある場合、ワークスペース内にコピー
                relative_path = file_path
                if workspace_cwd and not str(path.absolute()).startswith(str(Path(workspace_cwd).absolute())):
                    # ワークスペース外のファイル → ワークスペース内にコピー
                    dest_path = Path(workspace_cwd) / path.name

                    # 同名ファイルが存在する場合はユニークな名前を生成
                    counter = 1
                    original_name = dest_path.stem
                    suffix = dest_path.suffix
                    while dest_path.exists():
                        dest_path = Path(workspace_cwd) / f"{original_name}_{counter}{suffix}"
                        counter += 1

                    try:
                        shutil.copy2(str(path), str(dest_path))
                        relative_path = dest_path.name
                        logger.info(
                            "ファイル提示: ワークスペース外ファイルをコピー",
                            source=str(path),
                            destination=str(dest_path)
                        )
                    except Exception as copy_error:
                        logger.error(
                            "ファイル提示: ファイルコピー失敗",
                            source=str(path),
                            error=str(copy_error)
                        )
                        files_info.append({
                            "path": full_path,
                            "relative_path": file_path,
                            "name": path.name,
                            "exists": False,
                            "error": f"コピー失敗: {str(copy_error)}"
                        })
                        continue
                elif workspace_cwd:
                    # ワークスペース内のファイル → 相対パスを計算
                    try:
                        relative_path = str(path.absolute().relative_to(Path(workspace_cwd).absolute()))
                    except ValueError:
                        relative_path = path.name

                files_info.append({
                    "path": str(path.absolute()),
                    "relative_path": relative_path,
                    "name": path.name,
                    "size": path.stat().st_size,
                    "mime_type": mime_type or "application/octet-stream",
                    "exists": True
                })
                logger.info(
                    "ファイル提示: ファイル検出",
                    file_path=file_path,
                    full_path=str(path.absolute()),
                    relative_path=relative_path,
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
        if existing_files:
            result_text = f"ファイルを提示しました: {description}\n\n"
            result_text += "【提示されたファイル】\n"
            for f in existing_files:
                result_text += f"• {f['name']} ({f['size']} bytes)\n"
                result_text += f"  ダウンロードパス: {f['relative_path']}\n"
        else:
            result_text = f"提示するファイルが見つかりませんでした: {description}\n\n"

        if missing_files:
            result_text += "\n【見つからなかったファイル】\n"
            for f in missing_files:
                result_text += f"• {f['relative_path']}"
                if f.get("error"):
                    result_text += f" ({f['error']})"
                result_text += "\n"

        return {
            "content": [{
                "type": "text",
                "text": result_text.strip()
            }],
            # メタデータとして追加情報を返す
            "_metadata": {
                "files": files_info,
                "presented_files": existing_files,
                "description": description
            }
        }

    return present_files_handler


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


def create_file_presentation_mcp_server(workspace_cwd: str = ""):
    """
    ファイル提示用のSDK MCPサーバーを作成

    Args:
        workspace_cwd: ワークスペースのカレントディレクトリ

    Returns:
        SDK MCPサーバー設定
    """
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError:
        logger.warning("claude_agent_sdk not available, skipping builtin MCP server")
        return None

    # present_filesツールを定義
    @tool(
        "present_files",
        "AIが作成・編集したファイルをユーザーに提示する。Write/Edit/NotebookEditでファイルを作成・編集した後は、必ずこのツールを使用してユーザーにファイルを提示してください。",
        {
            "file_paths": list[str],
            "description": str
        }
    )
    async def present_files_tool(args: dict[str, Any]) -> dict[str, Any]:
        """
        ファイルパスのリストを受け取り、ユーザーに提示する情報を返す
        """
        handler = create_present_files_handler(workspace_cwd)
        return await handler(args)

    # MCPサーバーとして登録
    server = create_sdk_mcp_server(
        name="file-presentation",
        version="1.0.0",
        tools=[present_files_tool]
    )

    return server


# ファイル提示ツールに関するシステムプロンプト
FILE_PRESENTATION_PROMPT = """
## ファイル作成ルール
- **相対パスのみ使用**（例: `hello.py`）。絶対パス（/tmp/等）は禁止
- ファイル作成後は `mcp__file-presentation__present_files` で提示
- file_paths は配列で指定: `["hello.py"]`
"""
