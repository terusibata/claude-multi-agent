"""
ビルトインMCPツール

アプリケーション組み込みのMCPサーバーとツール定義を提供。
"""
from app.services.builtin_tools.definitions import (
    BUILTIN_TOOL_DEFINITIONS,
    FILE_PRESENTATION_PROMPT,
    get_all_builtin_tool_definitions,
    get_builtin_tool_definition,
)
from app.services.builtin_tools.file_presentation import create_present_files_handler
from app.services.builtin_tools.server import (
    create_file_presentation_mcp_server,
    create_file_tools_mcp_server,
)

__all__ = [
    "BUILTIN_TOOL_DEFINITIONS",
    "FILE_PRESENTATION_PROMPT",
    "get_all_builtin_tool_definitions",
    "get_builtin_tool_definition",
    "create_present_files_handler",
    "create_file_presentation_mcp_server",
    "create_file_tools_mcp_server",
]
