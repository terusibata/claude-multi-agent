"""
ファイル読み込みツールパッケージ（コンテナ側）

ワークスペース上のファイルをローカルファイルシステムから読み込むためのMCPツール群
"""

from workspace_agent.file_tools.registry import (
    create_file_tools_handlers,
    FILE_TOOLS_PROMPT,
)

__all__ = [
    "create_file_tools_handlers",
    "FILE_TOOLS_PROMPT",
]
