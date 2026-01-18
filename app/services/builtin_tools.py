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
    },
    "request_form": {
        "name": "request_form",
        "description": "ユーザーにフォーム入力を要求する。フロントエンドでフォームUIが表示され、入力結果は次のメッセージとしてJSON形式で送信される。フィールドタイプ: text, textarea, select, multiselect, radio, checkbox, autocomplete, multi-autocomplete, cascading-select, async-select, number, range, date, datetime, file, hidden, divider, heading。詳細なフィールド仕様はAgent Skillsを参照。",
        "input_schema": {
            "type": "object",
            "properties": {
                "form_schema": {
                    "type": "object",
                    "description": "フォーム定義。title(string,必須), description(string), submitLabel(string), cancelLabel(string), fields(array,必須)を含む。fieldsは{type, name, label, ...}の配列。",
                    "additionalProperties": True
                }
            },
            "required": ["form_schema"]
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
- **サブエージェント（Task）がファイルを作成した場合も、その完了後に必ず `mcp__file-presentation__present_files` を呼び出してください**
- サブエージェントの結果からファイルパスを確認し、作成されたファイルを提示してください
"""


def create_request_form_handler():
    """
    request_formツールのハンドラーを作成

    Returns:
        ツールハンドラー関数
    """
    async def request_form_handler(args: dict[str, Any]) -> dict[str, Any]:
        """
        request_formツールの実行ハンドラー

        フォームスキーマを受け取り、フロントエンドでフォームを表示するための
        レスポンスを返す。ユーザーの入力は次のメッセージとして送信される。

        Args:
            args: ツール引数
                - form_schema: フォーム定義スキーマ

        Returns:
            ツール実行結果（フォームリクエスト情報）
        """
        form_schema = args.get("form_schema", {})

        # スキーマのバリデーション
        if not form_schema:
            return {
                "content": [{
                    "type": "text",
                    "text": "エラー: form_schemaが指定されていません。"
                }],
                "isError": True
            }

        title = form_schema.get("title")
        fields = form_schema.get("fields", [])

        if not title:
            return {
                "content": [{
                    "type": "text",
                    "text": "エラー: フォームタイトル（title）は必須です。"
                }],
                "isError": True
            }

        if not fields:
            return {
                "content": [{
                    "type": "text",
                    "text": "エラー: フォームフィールド（fields）は必須です。"
                }],
                "isError": True
            }

        # フィールドの簡易バリデーション
        valid_field_types = {
            "text", "textarea", "select", "multiselect",
            "autocomplete", "multi-autocomplete", "cascading-select", "async-select",
            "checkbox", "radio", "number", "range",
            "date", "datetime", "file", "hidden",
            "divider", "heading"
        }

        for i, field in enumerate(fields):
            field_type = field.get("type")
            if not field_type:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"エラー: フィールド[{i}]にtypeが指定されていません。"
                    }],
                    "isError": True
                }

            if field_type not in valid_field_types:
                return {
                    "content": [{
                        "type": "text",
                        "text": f"エラー: フィールド[{i}]の無効なtype: '{field_type}'。"
                               f"有効なタイプ: {', '.join(sorted(valid_field_types))}"
                    }],
                    "isError": True
                }

            # 入力フィールドはnameが必須（divider, headingは除く）
            if field_type not in {"divider", "heading"}:
                if not field.get("name"):
                    return {
                        "content": [{
                            "type": "text",
                            "text": f"エラー: フィールド[{i}]（type: {field_type}）にnameが指定されていません。"
                        }],
                        "isError": True
                    }

        # フォームリクエストのレスポンスを構築
        logger.info(
            "フォームリクエスト",
            title=title,
            field_count=len(fields)
        )

        result_text = f"フォーム入力を待機しています。\n\n"
        result_text += f"【{title}】\n"
        if form_schema.get("description"):
            result_text += f"{form_schema['description']}\n"
        result_text += f"\nフィールド数: {len(fields)}\n"
        result_text += "ユーザーがフォームに入力後、次のメッセージとして入力内容が送信されます。"

        return {
            "content": [{
                "type": "text",
                "text": result_text
            }],
            # メタデータとしてフォームスキーマを返す（フロントエンドで利用）
            "_metadata": {
                "type": "form_request",
                "schema": form_schema,
                "status": "waiting_for_input"
            }
        }

    return request_form_handler


def create_form_request_mcp_server():
    """
    フォームリクエスト用のSDK MCPサーバーを作成

    Returns:
        SDK MCPサーバー設定
    """
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError:
        logger.warning("claude_agent_sdk not available, skipping form request MCP server")
        return None

    # request_formツールを定義
    @tool(
        "request_form",
        "ユーザーにフォーム入力を要求する。JSON Schemaベースでフォームを定義し、フロントエンドで表示される。",
        {
            "form_schema": dict
        }
    )
    async def request_form_tool(args: dict[str, Any]) -> dict[str, Any]:
        """
        フォームスキーマを受け取り、フロントエンドでフォームを表示する
        """
        handler = create_request_form_handler()
        return await handler(args)

    # MCPサーバーとして登録
    server = create_sdk_mcp_server(
        name="form",
        version="1.0.0",
        tools=[request_form_tool]
    )

    return server


# フォームリクエストツールに関するシステムプロンプト（Agent Skills用）
FORM_REQUEST_PROMPT = """
## フォームリクエストツール (`mcp__form__request_form`)

ユーザーから複数の情報を収集する場合に使用。フロントエンドでフォームUIが表示され、入力結果は次のメッセージとしてJSON形式で送信される。

### 基本構造
```json
{"title": "タイトル", "description": "説明", "fields": [...]}
```

### フィールドタイプと使用例

■ テキスト系
- `text`: 単一行 `{"type":"text","name":"project_name","label":"名前","required":true,"suggestions":["候補1","候補2"]}`
- `textarea`: 複数行 `{"type":"textarea","name":"desc","label":"説明","rows":3}`

■ 選択系
- `select`: 単一選択 `{"type":"select","name":"lang","label":"言語","options":[{"value":"py","label":"Python"}],"default":"py"}`
- `multiselect`: 複数選択 `{"type":"multiselect","name":"features","label":"機能","options":[...],"maxSelect":3}`
- `radio`: ラジオ `{"type":"radio","name":"priority","label":"優先度","options":[{"value":"high","label":"高"}]}`
- `checkbox`: チェック `{"type":"checkbox","name":"agree","label":"同意する","required":true}`

■ 動的検索（外部API連携）
- `autocomplete`: 検索単一 `{"type":"autocomplete","name":"user","label":"担当者","searchUrl":"https://...","searchParams":{"q":"{query}"},"displayField":"name","valueField":"id"}`
- `multi-autocomplete`: 検索複数（autocomplete + maxSelect）
- `cascading-select`: 連動選択 `{"type":"cascading-select","name":"city","searchUrl":"...","dependsOn":"pref","dependsOnParam":"pref_code","displayField":"name","valueField":"code"}`
- `async-select`: 初期読込 `{"type":"async-select","name":"cat","loadUrl":"https://...","displayField":"name","valueField":"id"}`

■ 数値・日付
- `number`: 数値 `{"type":"number","name":"qty","label":"数量","min":1,"max":100}`
- `range`: スライダー `{"type":"range","name":"pct","label":"%","min":0,"max":100,"step":5,"showValue":true}`
- `date`: 日付 `{"type":"date","name":"due","label":"期限","minDate":"today"}`
- `datetime`: 日時 `{"type":"datetime","name":"time","label":"日時"}`

■ ファイル・その他
- `file`: アップロード `{"type":"file","name":"doc","label":"添付","accept":".pdf,.doc","maxSize":10485760}`
- `hidden`: 隠し `{"type":"hidden","name":"ver","value":"1.0"}`

■ レイアウト（name不要）
- `divider`: 区切り線 `{"type":"divider","label":"詳細設定"}`
- `heading`: 見出し `{"type":"heading","text":"基本情報","level":2}`

### 使用ガイドライン
1. 複数情報収集時はフォームを使用（単一質問は会話で確認）
2. テキスト入力には`suggestions`で入力例を提示
3. 長いフォームは`divider`/`heading`でセクション分け
"""
