"""
SDKオプションビルダー
ClaudeAgentOptionsの構築を担当
"""
import re
from typing import Any, Optional

import structlog

from app.services.execute.aws_config import AWSConfig
from app.services.execute.context import ExecutionContext, SDKOptions
from app.services.builtin_tools import (
    create_file_presentation_mcp_server,
    create_file_tools_mcp_server,
    FILE_PRESENTATION_PROMPT,
)
from app.services.workspace.file_tools import FILE_TOOLS_PROMPT
from app.services.mcp_server_service import McpServerService
from app.services.openapi_mcp_service import create_openapi_mcp_server
from app.services.skill_service import SkillService
from app.services.workspace_service import WorkspaceService

logger = structlog.get_logger(__name__)

# スキル名に使用可能な文字パターン（セキュリティのため制限）
SAFE_SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


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
            workspace_enabled=context.workspace_enabled,
        )

        # 許可するツールリストの構築
        # ["*"]で全ツールを許可するため、個別のツール追加は不要
        # ただし、将来的に許可ツールを制限する場合はここで追加が必要になる
        allowed_tools = ["*"]

        # テナントのアクティブなスキルを取得
        skills = await self.skill_service.get_all_by_tenant(context.tenant_id)
        active_skills = [s for s in skills if s.status == "active"]

        # テナントのアクティブなMCPサーバーを取得
        all_mcp_servers = await self.mcp_service.get_all_by_tenant(context.tenant_id)
        active_mcp_servers = [m for m in all_mcp_servers if m.status == "active"]

        # MCPサーバー設定の構築
        mcp_servers, mcp_tools, builtin_servers, openapi_servers = await self._build_mcp_servers(
            context, active_mcp_servers, tokens
        )
        allowed_tools.extend(mcp_tools)

        # システムプロンプトの構築
        system_prompt = context.system_prompt or ""

        # cwdの決定
        cwd = await self._determine_cwd(context)
        context.cwd = cwd

        # ワークスペースコンテキストの追加
        system_prompt = await self._add_workspace_context(context, system_prompt)

        # ビルトインMCPサーバーの追加
        mcp_servers, allowed_tools, system_prompt = self._add_builtin_mcp_server(
            context, cwd, mcp_servers, allowed_tools, system_prompt, builtin_servers
        )

        # OpenAPI MCPサーバーの追加
        mcp_servers, allowed_tools, system_prompt = self._add_openapi_mcp_servers(
            mcp_servers, allowed_tools, system_prompt, openapi_servers, tokens
        )

        # preferred_skills の処理（システムプロンプトの先頭に追加）
        if context.preferred_skills:
            system_prompt = self._build_preferred_skills_prompt(
                context.preferred_skills, allowed_tools, system_prompt
            )

        # AWS環境変数の構築
        aws_config = AWSConfig(context.model)
        env = aws_config.build_env_vars()

        # オプションの構築
        sdk_options = SDKOptions(
            system_prompt=system_prompt if system_prompt else None,
            model=context.model.bedrock_model_id,
            allowed_tools=allowed_tools,
            mcp_servers=mcp_servers if mcp_servers else None,
            cwd=cwd,
            env=env,
        )

        # Skills設定（スキルがある場合はproject設定を読み込む）
        if active_skills:
            sdk_options.setting_sources = ["project"]

        options = sdk_options.to_dict()
        logger.info("オプション構築完了", options_keys=list(options.keys()))

        return options

    async def _build_mcp_servers(
        self,
        context: ExecutionContext,
        mcp_definitions: list,
        tokens: Optional[dict[str, str]],
    ) -> tuple[dict, list[str], list[str], list]:
        """
        MCPサーバー設定を構築

        Returns:
            (mcp_servers設定, mcp_tools許可リスト, builtin_serverリスト, openapi_serversリスト)
        """
        mcp_servers = {}
        mcp_tools = []
        builtin_servers = []
        openapi_servers = []

        if mcp_definitions:
            # builtin/openapiタイプのサーバーを分離
            non_special_definitions = []
            for mcp_def in mcp_definitions:
                if mcp_def.type == "builtin":
                    builtin_servers.append(mcp_def.name)
                elif mcp_def.type == "openapi":
                    openapi_servers.append(mcp_def)
                else:
                    non_special_definitions.append(mcp_def)

            # 非特殊サーバーの設定を構築
            if non_special_definitions:
                mcp_servers = self.mcp_service.build_mcp_config(
                    non_special_definitions, tokens or {}
                )
                mcp_tools = self.mcp_service.get_allowed_tools(non_special_definitions)

        return mcp_servers, mcp_tools, builtin_servers, openapi_servers

    async def _determine_cwd(self, context: ExecutionContext) -> str:
        """作業ディレクトリを決定"""
        # デフォルトはテナント専用のcwd
        cwd = self.skill_service.get_tenant_cwd(context.tenant_id)

        if context.workspace_enabled:
            # ワークスペース情報を取得
            workspace_info = await self.workspace_service.get_workspace_info(
                context.tenant_id, context.conversation_id
            )

            # ワークスペースが未有効化またはworkspace_pathが未設定の場合は有効化
            if not workspace_info or not workspace_info.workspace_path:
                await self.workspace_service.enable_workspace(
                    context.tenant_id, context.conversation_id
                )
                workspace_info = await self.workspace_service.get_workspace_info(
                    context.tenant_id, context.conversation_id
                )

            if workspace_info and workspace_info.workspace_enabled:
                # S3からローカルに同期
                cwd = await self.workspace_service.sync_to_local(
                    context.tenant_id, context.conversation_id
                )
                logger.info(
                    "S3→ローカル同期完了",
                    tenant_id=context.tenant_id,
                    conversation_id=context.conversation_id,
                    cwd=cwd,
                )

        return cwd

    async def _add_workspace_context(
        self,
        context: ExecutionContext,
        system_prompt: str,
    ) -> str:
        """ワークスペースコンテキストをシステムプロンプトに追加"""
        if not context.workspace_enabled:
            return system_prompt

        workspace_info = await self.workspace_service.get_workspace_info(
            context.tenant_id, context.conversation_id
        )

        if workspace_info and workspace_info.workspace_enabled:
            workspace_context = await self.workspace_service.get_context_for_ai(
                context.tenant_id, context.conversation_id
            )

            if workspace_context:
                system_prompt = f"{system_prompt}\n\n{workspace_context.instructions}"

        return system_prompt

    def _add_builtin_mcp_server(
        self,
        context: ExecutionContext,
        cwd: str,
        mcp_servers: dict,
        allowed_tools: list[str],
        system_prompt: str,
        requested_builtin_servers: Optional[list[str]] = None,
    ) -> tuple[dict, list[str], str]:
        """
        ビルトインMCPサーバーを追加

        Args:
            context: 実行コンテキスト
            cwd: 作業ディレクトリ
            mcp_servers: MCPサーバー設定辞書
            allowed_tools: 許可ツールリスト
            system_prompt: システムプロンプト
            requested_builtin_servers: リクエストされたビルトインサーバー名のリスト

        Returns:
            更新された (mcp_servers, allowed_tools, system_prompt)
        """
        # file-presentationは常に追加（S3即時アップロードのためworkspace_serviceを渡す）
        file_presentation_server = create_file_presentation_mcp_server(
            cwd,
            self.workspace_service,
            context.tenant_id,
            context.conversation_id,
        )
        if file_presentation_server:
            mcp_servers["file-presentation"] = file_presentation_server
            allowed_tools.append("mcp__file-presentation__present_files")
            system_prompt = f"{system_prompt}\n\n{FILE_PRESENTATION_PROMPT}"
            logger.info(
                "ビルトインMCPサーバー追加完了",
                server_name="file-presentation",
            )

        # file-toolsは常に追加（全テナントでデフォルト利用可能）
        file_tools_server = create_file_tools_mcp_server(
            self.workspace_service,
            context.tenant_id,
            context.conversation_id,
        )
        if file_tools_server:
            mcp_servers["file-tools"] = file_tools_server
            allowed_tools.extend([
                # 共通
                "mcp__file-tools__list_workspace_files",
                "mcp__file-tools__read_image_file",
                # Excel
                "mcp__file-tools__inspect_excel_file",
                "mcp__file-tools__read_excel_sheet",
                # PDF
                "mcp__file-tools__inspect_pdf_file",
                "mcp__file-tools__read_pdf_pages",
                "mcp__file-tools__convert_pdf_to_images",
                # Word
                "mcp__file-tools__inspect_word_file",
                "mcp__file-tools__read_word_section",
                # PowerPoint
                "mcp__file-tools__inspect_pptx_file",
                "mcp__file-tools__read_pptx_slides",
                # 画像
                "mcp__file-tools__inspect_image_file",
            ])
            system_prompt = f"{system_prompt}\n\n{FILE_TOOLS_PROMPT}"
            logger.info(
                "ビルトインMCPサーバー追加完了",
                server_name="file-tools",
            )

        return mcp_servers, allowed_tools, system_prompt

    def _add_openapi_mcp_servers(
        self,
        mcp_servers: dict,
        allowed_tools: list[str],
        system_prompt: str,
        openapi_server_defs: list,
        tokens: Optional[dict[str, str]] = None,
    ) -> tuple[dict, list[str], str]:
        """
        OpenAPI MCPサーバーを追加

        Args:
            mcp_servers: MCPサーバー設定辞書
            allowed_tools: 許可ツールリスト
            system_prompt: システムプロンプト
            openapi_server_defs: OpenAPIサーバー定義のリスト
            tokens: トークン辞書

        Returns:
            更新された (mcp_servers, allowed_tools, system_prompt)
        """
        for server_def in openapi_server_defs:
            if not server_def.openapi_spec:
                logger.warning(
                    "OpenAPI spec not found for server",
                    server_name=server_def.name,
                )
                continue

            # ヘッダーを構築
            headers = {}
            if server_def.headers_template and tokens:
                for key, template in server_def.headers_template.items():
                    def replacer(match: re.Match) -> str:
                        token_key = match.group(1)
                        return tokens.get(token_key, match.group(0))
                    headers[key] = re.sub(r"\$\{(\w+)\}", replacer, template)

            # OpenAPI MCPサーバーを作成
            result = create_openapi_mcp_server(
                openapi_spec=server_def.openapi_spec,
                server_name=server_def.name,
                base_url=server_def.openapi_base_url,
                headers=headers,
            )

            if result:
                server, service = result
                mcp_servers[server_def.name] = server

                # 許可ツールを追加
                openapi_tools = service.get_allowed_tools()
                allowed_tools.extend(openapi_tools)

                # サーバーの説明をシステムプロンプトに追加
                if server_def.description:
                    tool_names = ", ".join([t["name"] for t in service.get_tool_definitions()])
                    prompt_addition = f"""
## {server_def.display_name or server_def.name}

{server_def.description}

利用可能なツール: {tool_names}
"""
                    system_prompt = f"{system_prompt}\n\n{prompt_addition}"

                logger.info(
                    "OpenAPI MCPサーバー追加完了",
                    server_name=server_def.name,
                    tools_count=len(openapi_tools),
                )

        return mcp_servers, allowed_tools, system_prompt

    def _validate_and_sanitize_skill_name(self, skill_name: str) -> Optional[str]:
        """
        スキル名を検証しサニタイズする

        Args:
            skill_name: 検証するスキル名

        Returns:
            サニタイズされたスキル名（無効な場合はNone）
        """
        if not skill_name:
            return None

        # 空白を除去
        cleaned = skill_name.strip()

        if not cleaned:
            return None

        # 最大長チェック（200文字）
        if len(cleaned) > 200:
            logger.warning(
                "スキル名が長すぎます",
                skill_name=cleaned[:50] + "...",
                length=len(cleaned),
            )
            return None

        # 安全な文字のみかチェック
        if not SAFE_SKILL_NAME_PATTERN.match(cleaned):
            logger.warning(
                "不正なスキル名フォーマット",
                skill_name=cleaned,
            )
            return None

        return cleaned

    def _build_preferred_skills_prompt(
        self,
        preferred_skills: list[str],
        allowed_tools: list[str],
        system_prompt: str,
    ) -> str:
        """
        preferred_skillsに基づいてシステムプロンプトを構築

        ユーザーが指定したSkillを優先的に使用するよう指示を追加。
        この指示はシステムプロンプトの先頭に追加される。

        Args:
            preferred_skills: 優先Skill名のリスト（Agent Skill名）
            allowed_tools: 許可されたツール名リスト
            system_prompt: 既存のシステムプロンプト

        Returns:
            更新されたシステムプロンプト
        """
        # スキル名をバリデーションしてサニタイズ
        validated_skills = []
        for skill_name in preferred_skills:
            sanitized = self._validate_and_sanitize_skill_name(skill_name)
            if sanitized:
                validated_skills.append(sanitized)
            else:
                logger.warning(
                    "無効なpreferred_skill名を無視",
                    original_skill_name=skill_name[:100] if skill_name else None,
                )

        # 有効なスキルがない場合はプロンプトを変更せず返す
        if not validated_skills:
            logger.warning("有効なpreferred_skillsがありません")
            return system_prompt

        # スキル名をカンマ区切りで結合（各スキル名は既にサニタイズ済み）
        skill_list = ", ".join(validated_skills)

        # 優先スキル指示を構築
        preferred_skills_prompt = f"""## 重要: 優先使用Skill指定

ユーザーは以下のSkillの使用を明示的に指定しました。
質問に回答する際は、**必ずこれらのSkillを最初に呼び出して**ください。

指定されたSkill: {skill_list}

### 使用手順
1. `Skill` ツールを使って、指定されたSkillを呼び出してください
2. Skillの指示に従って作業を進めてください
3. Skillで対応できない場合のみ、一般知識で補足してください（その場合は情報源がないことを明記）

---

"""
        logger.info(
            "preferred_skills指示を追加",
            preferred_skills=validated_skills,
            original_count=len(preferred_skills),
            validated_count=len(validated_skills),
        )

        # 既存のシステムプロンプトの先頭に追加
        return preferred_skills_prompt + system_prompt
