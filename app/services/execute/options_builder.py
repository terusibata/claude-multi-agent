"""
SDKオプションビルダー
ClaudeAgentOptionsの構築を担当
"""
from typing import Any, Optional

import structlog

from app.services.execute.aws_config import AWSConfig
from app.services.execute.context import ExecutionContext, SDKOptions
from app.services.builtin_tools import (
    create_file_presentation_mcp_server,
    FILE_PRESENTATION_PROMPT,
)
from app.services.servicenow_docs_tools import (
    create_servicenow_docs_mcp_server,
    SERVICENOW_DOCS_PROMPT,
)
from app.services.mcp_server_service import McpServerService, BUILTIN_MCP_SERVERS
from app.services.skill_service import SkillService
from app.services.workspace_service import WorkspaceService

logger = structlog.get_logger(__name__)


class OptionsBuilder:
    """
    SDKオプションビルダー

    ExecutionContextからClaudeAgentOptions用の辞書を構築
    """

    def __init__(
        self,
        mcp_service: McpServerService,
        skill_service: SkillService,
        workspace_service: WorkspaceService,
    ):
        """
        初期化

        Args:
            mcp_service: MCPサーバーサービス
            skill_service: スキルサービス
            workspace_service: ワークスペースサービス
        """
        self.mcp_service = mcp_service
        self.skill_service = skill_service
        self.workspace_service = workspace_service

    async def build(
        self,
        context: ExecutionContext,
        tokens: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """
        SDKオプションを構築

        Args:
            context: 実行コンテキスト
            tokens: MCPサーバー用トークン

        Returns:
            SDK用オプション辞書
        """
        logger.info(
            "SDK オプション構築中...",
            workspace_enabled=context.enable_workspace,
        )

        # 許可するツールリストの構築
        allowed_tools = await self._build_allowed_tools(context)

        # MCPサーバー設定の構築
        mcp_servers, mcp_tools, builtin_servers = await self._build_mcp_servers(context, tokens)
        allowed_tools.extend(mcp_tools)

        # システムプロンプトの構築
        system_prompt = context.agent_config.system_prompt or ""

        # cwdの決定
        cwd = await self._determine_cwd(context)
        context.cwd = cwd

        # ワークスペースコンテキストの追加
        system_prompt = await self._add_workspace_context(context, system_prompt)

        # ビルトインMCPサーバーの追加（DB登録されたbuiltinサーバーを含む）
        mcp_servers, allowed_tools, system_prompt = self._add_builtin_mcp_server(
            cwd, mcp_servers, allowed_tools, system_prompt, builtin_servers
        )

        # AWS環境変数の構築
        aws_config = AWSConfig(context.model)
        env = aws_config.build_env_vars()

        # オプションの構築
        sdk_options = SDKOptions(
            system_prompt=system_prompt if system_prompt else None,
            model=context.model.bedrock_model_id,
            allowed_tools=allowed_tools,
            permission_mode=context.agent_config.permission_mode,
            mcp_servers=mcp_servers if mcp_servers else None,
            cwd=cwd,
            env=env,
        )

        # Skills設定
        if context.agent_config.agent_skills:
            sdk_options.setting_sources = ["project"]

        # セッション継続・フォーク設定
        if context.resume_session_id:
            sdk_options.resume = context.resume_session_id
        if context.fork_session:
            sdk_options.fork_session = True

        options = sdk_options.to_dict()
        logger.info("オプション構築完了", options_keys=list(options.keys()))

        return options

    async def _build_allowed_tools(
        self,
        context: ExecutionContext,
    ) -> list[str]:
        """許可するツールリストを構築"""
        allowed_tools = list(context.agent_config.allowed_tools or [])

        # Skillツールを追加
        if context.agent_config.agent_skills:
            if "Skill" not in allowed_tools:
                allowed_tools.append("Skill")

        return allowed_tools

    async def _build_mcp_servers(
        self,
        context: ExecutionContext,
        tokens: Optional[dict[str, str]],
    ) -> tuple[dict, list[str], list[str]]:
        """
        MCPサーバー設定を構築

        Returns:
            (mcp_servers設定, mcp_tools許可リスト, builtin_serverリスト)
        """
        mcp_servers = {}
        mcp_tools = []
        builtin_servers = []

        if context.agent_config.mcp_servers:
            mcp_definitions = await self.mcp_service.get_by_ids(
                context.agent_config.mcp_servers, context.tenant_id
            )

            # builtinタイプのサーバーを分離
            non_builtin_definitions = []
            for mcp_def in mcp_definitions:
                if mcp_def.type == "builtin":
                    builtin_servers.append(mcp_def.name)
                else:
                    non_builtin_definitions.append(mcp_def)

            # 非builtinサーバーの設定を構築
            if non_builtin_definitions:
                mcp_servers = self.mcp_service.build_mcp_config(
                    non_builtin_definitions, tokens or {}
                )
                mcp_tools = self.mcp_service.get_allowed_tools(non_builtin_definitions)

        return mcp_servers, mcp_tools, builtin_servers

    async def _determine_cwd(self, context: ExecutionContext) -> str:
        """作業ディレクトリを決定"""
        # デフォルトはテナント専用のcwd
        cwd = self.skill_service.get_tenant_cwd(context.tenant_id)

        if context.enable_workspace:
            # ワークスペース情報を取得
            workspace_info = await self.workspace_service.get_workspace_info(
                context.tenant_id, context.chat_session_id
            )

            # ワークスペースが未有効化の場合は有効化
            if not workspace_info:
                await self.workspace_service.enable_workspace(
                    context.tenant_id, context.chat_session_id
                )
                workspace_info = await self.workspace_service.get_workspace_info(
                    context.tenant_id, context.chat_session_id
                )

            if workspace_info and workspace_info.workspace_enabled:
                # S3からローカルに同期
                cwd = await self.workspace_service.sync_to_local(
                    context.tenant_id, context.chat_session_id
                )
                logger.info(
                    "S3→ローカル同期完了",
                    tenant_id=context.tenant_id,
                    session_id=context.chat_session_id,
                    cwd=cwd,
                )

        return cwd

    async def _add_workspace_context(
        self,
        context: ExecutionContext,
        system_prompt: str,
    ) -> str:
        """ワークスペースコンテキストをシステムプロンプトに追加"""
        if not context.enable_workspace:
            return system_prompt

        workspace_info = await self.workspace_service.get_workspace_info(
            context.tenant_id, context.chat_session_id
        )

        if workspace_info and workspace_info.workspace_enabled:
            workspace_context = await self.workspace_service.get_context_for_ai(
                context.tenant_id, context.chat_session_id
            )

            if workspace_context:
                system_prompt = f"{system_prompt}\n\n{workspace_context.instructions}"

        return system_prompt

    def _add_builtin_mcp_server(
        self,
        cwd: str,
        mcp_servers: dict,
        allowed_tools: list[str],
        system_prompt: str,
        requested_builtin_servers: Optional[list[str]] = None,
    ) -> tuple[dict, list[str], str]:
        """
        ビルトインMCPサーバーを追加

        Args:
            cwd: 作業ディレクトリ
            mcp_servers: MCPサーバー設定辞書
            allowed_tools: 許可ツールリスト
            system_prompt: システムプロンプト
            requested_builtin_servers: リクエストされたビルトインサーバー名のリスト

        Returns:
            更新された (mcp_servers, allowed_tools, system_prompt)
        """
        # file-presentationは常に追加
        file_presentation_server = create_file_presentation_mcp_server(cwd)
        if file_presentation_server:
            mcp_servers["file-presentation"] = file_presentation_server
            allowed_tools.append("mcp__file-presentation__present_files")
            system_prompt = f"{system_prompt}\n\n{FILE_PRESENTATION_PROMPT}"
            logger.info(
                "ビルトインMCPサーバー追加完了",
                server_name="file-presentation",
            )

        # リクエストされたビルトインサーバーを追加
        if requested_builtin_servers:
            for server_name in requested_builtin_servers:
                if server_name == "servicenow-docs":
                    servicenow_server = create_servicenow_docs_mcp_server()
                    if servicenow_server:
                        mcp_servers["servicenow-docs"] = servicenow_server
                        # BUILTIN_MCP_SERVERSから許可ツールを取得
                        builtin_def = BUILTIN_MCP_SERVERS.get("servicenow-docs")
                        if builtin_def and builtin_def.get("allowed_tools"):
                            allowed_tools.extend(builtin_def["allowed_tools"])
                        system_prompt = f"{system_prompt}\n\n{SERVICENOW_DOCS_PROMPT}"
                        logger.info(
                            "ビルトインMCPサーバー追加完了",
                            server_name="servicenow-docs",
                        )

        return mcp_servers, allowed_tools, system_prompt
