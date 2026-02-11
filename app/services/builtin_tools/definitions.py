"""
ビルトインツール定義とプロンプト定数
"""
from typing import Any


# ビルトインツールの定義
BUILTIN_TOOL_DEFINITIONS = {
    "present_files": {
        "name": "present_files",
        "description": (
            "AIが作成・編集したファイルをユーザーに提示する。"
            "ファイルパスのリストと説明を受け取り、ユーザーに提示する情報を返す。"
            "Write/Edit/NotebookEditでファイルを作成・編集した後は、"
            "必ずこのツールを使用してユーザーにファイルを提示してください。"
        ),
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


# ファイル提示ツールに関するシステムプロンプト
FILE_PRESENTATION_PROMPT = """
## ファイル作成ルール
- **相対パスのみ使用**（例: `hello.py`）。絶対パス（/tmp/等）は禁止
- ファイル作成後は `mcp__file-presentation__present_files` で提示
- file_paths は配列で指定: `["hello.py"]`
- **サブエージェント（Task）がファイルを作成した場合も、その完了後に必ず `mcp__file-presentation__present_files` を呼び出してください**
- サブエージェントの結果からファイルパスを確認し、作成されたファイルを提示してください
"""


def get_builtin_tool_definition(tool_name: str) -> dict[str, Any] | None:
    """ビルトインツールの定義を取得"""
    return BUILTIN_TOOL_DEFINITIONS.get(tool_name)


def get_all_builtin_tool_definitions() -> list[dict[str, Any]]:
    """全ビルトインツールの定義を取得"""
    return list(BUILTIN_TOOL_DEFINITIONS.values())
