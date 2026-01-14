"""
AIコンテキストビルダー
AIに提供するワークスペースコンテキストを生成
"""
from typing import Optional

from app.schemas.workspace import WorkspaceContextForAI, WorkspaceFileList, WorkspaceInfo


class AIContextBuilder:
    """
    AIコンテキストビルダー

    AIに提供するワークスペース情報とガイドラインを生成
    """

    def build_context(
        self,
        workspace_info: WorkspaceInfo,
        file_list: WorkspaceFileList,
    ) -> Optional[WorkspaceContextForAI]:
        """
        AIに提供するワークスペースコンテキストを生成

        Args:
            workspace_info: ワークスペース情報
            file_list: ファイル一覧

        Returns:
            AIコンテキスト（ワークスペースが無効な場合はNone）
        """
        if not workspace_info or not workspace_info.workspace_enabled:
            return None

        files = [
            {
                "path": f.file_path,
                "size": f.file_size,
                "type": f.mime_type or "unknown",
                "source": f.source,
                "description": f.description or "",
            }
            for f in file_list.files
        ]

        instructions = self._build_instructions(files)

        return WorkspaceContextForAI(
            workspace_path=workspace_info.workspace_path,
            files=files,
            instructions=instructions,
        )

    def _build_instructions(self, files: list[dict]) -> str:
        """
        AIへの指示を構築

        Args:
            files: ファイル情報リスト

        Returns:
            指示テキスト
        """
        file_list_text = self._format_file_list(files)

        return f"""
## ワークスペース情報

あなたはセッション専用ワークスペースで作業しています。

### 利用可能なファイル:
{file_list_text}

### ガイドライン:
1. ファイルの読み取り: Readツールでワークスペース内のファイルを読み取れます
2. ファイルの作成/編集: Writeツールでファイルを作成・編集できます
3. コマンド実行: Bashツールでコマンドを実行できます（カレントディレクトリはワークスペース）
4. ファイル検索: Glob/Grepツールでファイルを検索できます

### 重要: ファイルパスの指定方法
- **必ず相対パスを使用してください**（例: `hello.py`、`output/data.csv`）
- **絶対パス（/tmp/xxx や /home/xxx など）は使用しないでください**
- ワークスペース外のファイルにはアクセスできません
- 親ディレクトリ（..）へのアクセスは禁止されています

### ファイル作成時の重要な注意:
ファイルを作成した場合は、以下のように返答してください:
- 「ファイル 'xxx.py' を作成しました。下記からダウンロードできます。」
- 「python xxx.py で実行できます」のような実行方法の案内は不要です
- ユーザーはこの環境でコマンドを実行できません。代わりにダウンロードして利用します
"""

    def _format_file_list(self, files: list[dict]) -> str:
        """ファイルリストをテキスト形式にフォーマット"""
        if not files:
            return "（ファイルなし）"

        lines = []
        for f in files:
            size_str = self._format_size(f["size"])
            source = "📤" if f["source"] == "user_upload" else "🤖"
            desc = f" - {f['description']}" if f.get("description") else ""
            lines.append(f"  {source} {f['path']} ({size_str}){desc}")

        return "\n".join(lines)

    def _format_size(self, size: int) -> str:
        """ファイルサイズを人間が読みやすい形式にフォーマット"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f}{unit}"
            size /= 1024
        return f"{size:.1f}TB"
