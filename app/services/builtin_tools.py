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
                - file_paths: ファイルパスのリスト
                - description: ファイルの説明

        Returns:
            ツール実行結果
        """
        file_paths = args.get("file_paths", [])
        description = args.get("description", "")

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
        result_text = f"ファイルを提示しました: {description}\n\n"

        if existing_files:
            result_text += "【提示されたファイル】\n"
            for f in existing_files:
                result_text += f"• {f['name']} ({f['size']} bytes)\n"
                result_text += f"  パス: {f['relative_path']}\n"

        if missing_files:
            result_text += "\n【見つからなかったファイル】\n"
            for f in missing_files:
                result_text += f"• {f['relative_path']}\n"

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
## ファイル提示について

Write、Edit、NotebookEditツールでファイルを作成または編集した場合は、作業完了後に必ず`mcp__file-presentation__present_files`ツールを使用してユーザーにファイルを提示してください。

### present_filesツールの使用方法
- file_paths: 作成・編集したファイルのパスをリストで指定
- description: ファイルの内容や目的を簡潔に説明

### 例
ファイルを作成した後:
```
mcp__file-presentation__present_files({
  "file_paths": ["hello.py"],
  "description": "Pythonのサンプルプログラム"
})
```

複数ファイルを作成した場合:
```
mcp__file-presentation__present_files({
  "file_paths": ["src/main.py", "src/utils.py", "README.md"],
  "description": "プロジェクトの初期ファイル一式"
})
```
"""
