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
        "description": """ユーザーにフォーム入力を要求する。JSON Schemaベースでフォームを定義し、フロントエンドで表示される。
ユーザーの入力結果は次のメッセージとしてJSON形式で送信される。

【フィールドタイプ一覧】

■ テキスト入力
- text: 単一行テキスト
  プロパティ: name(必須), label, placeholder, required, minLength, maxLength, pattern, patternError, default, suggestions(string[])
  例: {"name": "project_name", "type": "text", "label": "プロジェクト名", "required": true, "pattern": "^[a-z0-9-]+$", "suggestions": ["my-app", "api-server"]}

- textarea: 複数行テキスト
  プロパティ: name(必須), label, placeholder, rows, maxLength, suggestions(string[])
  例: {"name": "description", "type": "textarea", "label": "説明", "rows": 3}

■ 選択系
- select: 単一選択（ドロップダウン）
  プロパティ: name(必須), label, options(必須: [{value, label}]), default, required
  例: {"name": "language", "type": "select", "label": "言語", "options": [{"value": "python", "label": "Python"}, {"value": "typescript", "label": "TypeScript"}], "default": "python"}

- multiselect: 複数選択
  プロパティ: name(必須), label, options(必須), minSelect, maxSelect
  例: {"name": "features", "type": "multiselect", "label": "機能", "options": [{"value": "auth", "label": "認証"}, {"value": "api", "label": "API"}]}

- radio: ラジオボタン
  プロパティ: name(必須), label, options(必須), default
  例: {"name": "priority", "type": "radio", "label": "優先度", "options": [{"value": "high", "label": "高"}, {"value": "low", "label": "低"}]}

- checkbox: チェックボックス（真偽値）
  プロパティ: name(必須), label(必須), required, default
  例: {"name": "agree_terms", "type": "checkbox", "label": "利用規約に同意する", "required": true}

■ 動的検索
- autocomplete: 検索付き単一選択
  プロパティ: name(必須), label, searchUrl(必須), searchParams, displayField(必須), valueField(必須), minChars, debounceMs, renderTemplate
  例: {"name": "assignee", "type": "autocomplete", "label": "担当者", "searchUrl": "https://api.example.com/users/search", "searchParams": {"q": "{query}"}, "displayField": "name", "valueField": "id"}

- multi-autocomplete: 検索付き複数選択
  プロパティ: autocompleteと同様 + maxSelect
  例: {"name": "members", "type": "multi-autocomplete", "label": "メンバー", "searchUrl": "...", "displayField": "name", "valueField": "id", "maxSelect": 5}

- cascading-select: 連動選択（親の値で選択肢が変わる）
  プロパティ: name(必須), label, searchUrl(必須), dependsOn(必須: 親フィールド名), dependsOnParam(必須), displayField(必須), valueField(必須)
  例: {"name": "city", "type": "cascading-select", "label": "市区町村", "searchUrl": "...", "dependsOn": "prefecture", "dependsOnParam": "prefecture_code", "displayField": "name", "valueField": "code"}

- async-select: 非同期読み込み選択（ページ読み込み時にAPI取得）
  プロパティ: name(必須), label, loadUrl(必須), displayField(必須), valueField(必須), default
  例: {"name": "category", "type": "async-select", "label": "カテゴリ", "loadUrl": "https://api.example.com/categories", "displayField": "name", "valueField": "id"}

■ 数値
- number: 数値入力
  プロパティ: name(必須), label, min, max, step, default
  例: {"name": "quantity", "type": "number", "label": "数量", "min": 1, "max": 100, "default": 1}

- range: スライダー
  プロパティ: name(必須), label, min, max, step, showValue
  例: {"name": "percentage", "type": "range", "label": "割合 (%)", "min": 0, "max": 100, "step": 5, "showValue": true}

■ 日付
- date: 日付選択
  プロパティ: name(必須), label, minDate, maxDate（"today", "+30days", ISO形式）
  例: {"name": "due_date", "type": "date", "label": "期限", "minDate": "today"}

- datetime: 日時選択
  プロパティ: name(必須), label
  例: {"name": "meeting_time", "type": "datetime", "label": "日時"}

■ ファイル
- file: ファイルアップロード
  プロパティ: name(必須), label, accept, maxSize(バイト), multiple
  例: {"name": "attachment", "type": "file", "label": "添付", "accept": ".pdf,.doc", "maxSize": 10485760}

■ レイアウト（name不要）
- divider: 区切り線
  プロパティ: label（セクション名、オプション）
  例: {"type": "divider", "label": "詳細設定"}

- heading: 見出し
  プロパティ: text(必須), level(1-4), description
  例: {"type": "heading", "text": "基本情報", "level": 2, "description": "必須項目を入力してください"}

- hidden: 隠しフィールド
  プロパティ: name(必須), value(必須)
  例: {"name": "form_version", "type": "hidden", "value": "1.0"}""",
        "input_schema": {
            "type": "object",
            "properties": {
                "form_schema": {
                    "type": "object",
                    "description": "フォーム定義スキーマ",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "フォームのタイトル（例: '新規プロジェクト作成'）"
                        },
                        "description": {
                            "type": "string",
                            "description": "フォームの説明文（オプション）"
                        },
                        "submitLabel": {
                            "type": "string",
                            "description": "送信ボタンのラベル（デフォルト: '送信'）"
                        },
                        "cancelLabel": {
                            "type": "string",
                            "description": "キャンセルボタンのラベル（デフォルト: 'キャンセル'）"
                        },
                        "fields": {
                            "type": "array",
                            "description": "フォームフィールドの配列。各フィールドは type でタイプを指定し、入力フィールドは name が必須。",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {
                                        "type": "string",
                                        "enum": ["text", "textarea", "select", "multiselect", "autocomplete", "multi-autocomplete", "cascading-select", "async-select", "checkbox", "radio", "number", "range", "date", "datetime", "file", "hidden", "divider", "heading"],
                                        "description": "フィールドタイプ"
                                    },
                                    "name": {
                                        "type": "string",
                                        "description": "フィールド名（送信データのキー）。divider, heading以外は必須"
                                    },
                                    "label": {
                                        "type": "string",
                                        "description": "表示ラベル"
                                    },
                                    "required": {
                                        "type": "boolean",
                                        "description": "必須フィールドか"
                                    },
                                    "options": {
                                        "type": "array",
                                        "description": "select/multiselect/radioの選択肢。各要素は {value, label}",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "value": {"type": "string"},
                                                "label": {"type": "string"}
                                            }
                                        }
                                    },
                                    "suggestions": {
                                        "type": "array",
                                        "description": "text/textareaの入力候補（クリックで入力欄に反映）",
                                        "items": {"type": "string"}
                                    },
                                    "placeholder": {"type": "string"},
                                    "default": {"description": "デフォルト値"},
                                    "minLength": {"type": "number"},
                                    "maxLength": {"type": "number"},
                                    "pattern": {"type": "string", "description": "正規表現パターン"},
                                    "patternError": {"type": "string"},
                                    "rows": {"type": "number", "description": "textareaの行数"},
                                    "min": {"type": "number"},
                                    "max": {"type": "number"},
                                    "step": {"type": "number"},
                                    "minSelect": {"type": "number"},
                                    "maxSelect": {"type": "number"},
                                    "searchUrl": {"type": "string", "description": "autocomplete系の検索API URL"},
                                    "searchParams": {"type": "object", "description": "検索パラメータ。{query}がユーザー入力に置換"},
                                    "loadUrl": {"type": "string", "description": "async-selectのデータ取得URL"},
                                    "displayField": {"type": "string"},
                                    "valueField": {"type": "string"},
                                    "dependsOn": {"type": "string", "description": "cascading-selectの親フィールド名"},
                                    "dependsOnParam": {"type": "string"},
                                    "minDate": {"type": "string"},
                                    "maxDate": {"type": "string"},
                                    "accept": {"type": "string", "description": "ファイル許可形式（.pdf,.docなど）"},
                                    "maxSize": {"type": "number", "description": "最大ファイルサイズ（バイト）"},
                                    "multiple": {"type": "boolean"},
                                    "text": {"type": "string", "description": "headingの見出しテキスト"},
                                    "level": {"type": "number", "description": "headingのレベル（1-4）"},
                                    "value": {"description": "hiddenフィールドの固定値"},
                                    "showValue": {"type": "boolean", "description": "rangeで現在値を表示"}
                                },
                                "required": ["type"]
                            }
                        }
                    },
                    "required": ["title", "fields"]
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


# フォームリクエストツールに関するシステムプロンプト
FORM_REQUEST_PROMPT = """
## フォームリクエストツール

ユーザーから複数の情報を収集する必要がある場合は、`mcp__form__request_form` ツールを使用してください。

### 使用ガイドライン

1. **複数の情報が必要な場合はフォームを使用**
   - プロジェクト作成、設定変更、複雑なデータ入力など

2. **単純な質問は会話で確認**
   - はい/いいえなど1つの質問は会話で直接確認

3. **検索が必要なフィールドには autocomplete タイプを使用**
   - ユーザー検索、プロジェクト検索など

4. **テキスト入力には suggestions で入力例を提示**
   - ユーザーの入力を助けるヒントを提供

5. **フォームが長くなる場合は divider や heading でセクション分け**
   - 視覚的な整理でユーザビリティを向上

### フィールドタイプ

- `text`: 単一行テキスト入力（suggestions対応）
- `textarea`: 複数行テキスト入力（suggestions対応）
- `select`: 単一選択（ドロップダウン）
- `multiselect`: 複数選択
- `autocomplete`: 検索付き単一選択（外部API連携）
- `multi-autocomplete`: 検索付き複数選択
- `cascading-select`: 連動選択（親子関係のある選択肢）
- `async-select`: 非同期読み込み選択
- `checkbox`: チェックボックス
- `radio`: ラジオボタン
- `number`: 数値入力
- `range`: スライダー
- `date`: 日付選択
- `datetime`: 日時選択
- `file`: ファイルアップロード
- `hidden`: 隠しフィールド
- `divider`: 区切り線（セクション分け）
- `heading`: 見出し（セクションタイトル）
"""
