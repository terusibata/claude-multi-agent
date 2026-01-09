"""
エージェント実行サービス
Claude Agent SDKを使用したエージェント実行とストリーミング処理
"""
import os
import structlog
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncGenerator, Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.agent_config import AgentConfig
from app.models.model import Model
from app.schemas.execute import ExecuteRequest, ExecutorInfo
from app.services.mcp_server_service import McpServerService
from app.services.session_service import SessionService
from app.services.skill_service import SkillService
from app.services.usage_service import UsageService
from app.utils.streaming import (
    format_error_event,
    format_result_event,
    format_session_start_event,
    format_text_delta_event,
    format_thinking_event,
    format_tool_complete_event,
    format_tool_start_event,
)
from app.utils.tool_summary import generate_tool_result_summary, generate_tool_summary

settings = get_settings()
logger = structlog.get_logger(__name__)


class ExecuteService:
    """エージェント実行サービスクラス"""

    def __init__(self, db: AsyncSession):
        """
        初期化

        Args:
            db: データベースセッション
        """
        self.db = db
        self.session_service = SessionService(db)
        self.usage_service = UsageService(db)
        self.skill_service = SkillService(db)
        self.mcp_service = McpServerService(db)

    def _build_bedrock_env(self, model: Model) -> dict[str, str]:
        """
        AWS Bedrock環境変数の辞書を構築

        Args:
            model: モデル定義

        Returns:
            環境変数の辞書
        """
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1",
        }

        # AWS認証情報を追加（設定されている場合のみ）
        # Noneまたは空文字列の場合は追加しない
        if settings.aws_access_key_id and settings.aws_access_key_id.strip():
            env["AWS_ACCESS_KEY_ID"] = settings.aws_access_key_id
            logger.info(
                "AWS_ACCESS_KEY_ID設定",
                prefix=settings.aws_access_key_id[:8] + "..." if len(settings.aws_access_key_id) > 8 else "短すぎ"
            )
        else:
            logger.warning("AWS_ACCESS_KEY_IDが設定されていません")

        if settings.aws_secret_access_key and settings.aws_secret_access_key.strip():
            env["AWS_SECRET_ACCESS_KEY"] = settings.aws_secret_access_key
            logger.info(
                "AWS_SECRET_ACCESS_KEY設定",
                prefix=settings.aws_secret_access_key[:8] + "..." if len(settings.aws_secret_access_key) > 8 else "短すぎ"
            )
        else:
            logger.warning("AWS_SECRET_ACCESS_KEYが設定されていません")

        if settings.aws_session_token and settings.aws_session_token.strip():
            env["AWS_SESSION_TOKEN"] = settings.aws_session_token
            logger.info("AWS_SESSION_TOKEN設定済み")

        # モデルのリージョンを設定（指定がなければデフォルト）
        if model.model_region:
            env["AWS_REGION"] = model.model_region
        else:
            env["AWS_REGION"] = settings.aws_region

        logger.info(
            "Bedrock環境変数構築完了",
            region=env["AWS_REGION"],
            has_access_key="AWS_ACCESS_KEY_ID" in env,
            has_secret_key="AWS_SECRET_ACCESS_KEY" in env,
            has_session_token="AWS_SESSION_TOKEN" in env
        )

        return env

    async def _build_options(
        self,
        agent_config: AgentConfig,
        model: Model,
        tenant_id: str,
        tokens: Optional[dict[str, str]],
        resume_session_id: Optional[str],
        fork_session: bool,
    ) -> dict[str, Any]:
        """
        ClaudeAgentOptions相当の設定を構築

        Args:
            agent_config: エージェント実行設定
            model: モデル定義
            tenant_id: テナントID
            tokens: MCPサーバー用トークン
            resume_session_id: 継続セッションID
            fork_session: セッションフォークフラグ

        Returns:
            SDK用オプション辞書
        """
        # 許可するツールリストの構築
        allowed_tools = list(agent_config.allowed_tools or [])

        # Skillツールを追加
        if agent_config.agent_skills:
            if "Skill" not in allowed_tools:
                allowed_tools.append("Skill")

        # MCPサーバー設定の構築
        mcp_servers = {}
        if agent_config.mcp_servers:
            mcp_definitions = await self.mcp_service.get_by_ids(
                agent_config.mcp_servers, tenant_id
            )
            mcp_servers = self.mcp_service.build_mcp_config(
                mcp_definitions, tokens or {}
            )
            # MCPツールを許可リストに追加
            mcp_tools = self.mcp_service.get_allowed_tools(mcp_definitions)
            allowed_tools.extend(mcp_tools)

        # テナント専用のcwdを取得
        cwd = self.skill_service.get_tenant_cwd(tenant_id)

        # AWS Bedrock環境変数を構築
        env = self._build_bedrock_env(model)

        options = {
            "system_prompt": agent_config.system_prompt,
            "model": model.bedrock_model_id,
            "allowed_tools": allowed_tools,
            "permission_mode": agent_config.permission_mode,
            "mcp_servers": mcp_servers if mcp_servers else None,
            "cwd": cwd,
            "env": env,
        }

        # Skillsが設定されている場合のみ、setting_sourcesを追加
        # setting_sourcesを指定すると、.claude/から設定を読み込もうとする
        if agent_config.agent_skills:
            options["setting_sources"] = ["project"]

        # セッション継続・フォークの設定
        if resume_session_id:
            options["resume"] = resume_session_id
        if fork_session:
            options["fork_session"] = True

        # Noneの値を削除
        return {k: v for k, v in options.items() if v is not None}

    async def execute_streaming(
        self,
        request: ExecuteRequest,
        agent_config: AgentConfig,
        model: Model,
        tenant_id: str,
    ) -> AsyncGenerator[dict, None]:
        """
        エージェントをストリーミング実行

        Args:
            request: 実行リクエスト
            agent_config: エージェント実行設定
            model: モデル定義
            tenant_id: テナントID

        Yields:
            SSEイベント辞書
        """
        start_time = time.time()
        session_id = None
        messages_log = []
        tools_used = []
        current_tool = None
        assistant_text = ""
        message_seq = 0
        errors = []

        logger.info(
            "エージェント実行開始",
            tenant_id=tenant_id,
            chat_session_id=request.chat_session_id,
            agent_config_id=request.agent_config_id,
            model_id=model.model_id,
            agent_skills=agent_config.agent_skills
        )

        try:
            # オプション構築
            logger.info("SDK オプション構築中...")
            options = await self._build_options(
                agent_config=agent_config,
                model=model,
                tenant_id=tenant_id,
                tokens=request.tokens,
                resume_session_id=request.resume_session_id,
                fork_session=request.fork_session,
            )
            logger.info("オプション構築完了", options_keys=list(options.keys()))

            # セッション存在確認・作成
            logger.info("セッション確認中", chat_session_id=request.chat_session_id)
            existing_session = await self.session_service.get_session_by_id(
                request.chat_session_id, tenant_id
            )
            if not existing_session:
                logger.info("新規セッション作成中...")
                await self.session_service.create_session(
                    chat_session_id=request.chat_session_id,
                    tenant_id=tenant_id,
                    user_id=request.executor.user_id,
                    agent_config_id=request.agent_config_id,
                    title=request.user_input[:100] if request.user_input else None,
                )
                logger.info("セッション作成完了")
            else:
                logger.info("既存セッションを使用")

            # ターン番号を取得
            turn_number = await self.session_service.get_latest_turn_number(
                request.chat_session_id
            ) + 1
            logger.info("ターン番号取得", turn_number=turn_number)

            # Claude Agent SDKをインポート
            logger.info("Claude Agent SDK インポート中...")
            try:
                from claude_agent_sdk import ClaudeAgentOptions, query
                logger.info("Claude Agent SDK インポート成功")
            except ImportError as e:
                yield format_error_event(
                    f"Claude Agent SDKがインストールされていません: {str(e)}",
                    "sdk_not_installed",
                )
                yield format_result_event(
                    subtype="error_during_execution",
                    result=None,
                    errors=[f"Claude Agent SDKがインストールされていません: {str(e)}"],
                    usage={
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_creation_tokens": 0,
                        "cache_read_tokens": 0,
                        "total_tokens": 0,
                    },
                    cost_usd=0,
                    num_turns=0,
                    duration_ms=int((time.time() - start_time) * 1000),
                    tools_summary=[],
                )
                return

            # ClaudeAgentOptionsを構築
            logger.info("ClaudeAgentOptions 構築中...")
            logger.info("SDK options", options=options)
            try:
                sdk_options = ClaudeAgentOptions(**options)
                logger.info("ClaudeAgentOptions 構築成功")
            except Exception as e:
                logger.error("ClaudeAgentOptions 構築エラー", error=str(e), exc_info=True)
                yield format_error_event(
                    f"SDK options構築エラー: {str(e)}",
                    "options_error",
                )
                yield format_result_event(
                    subtype="error_during_execution",
                    result=None,
                    errors=[f"SDK options構築エラー: {str(e)}"],
                    usage={
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_creation_tokens": 0,
                        "cache_read_tokens": 0,
                        "total_tokens": 0,
                    },
                    cost_usd=0,
                    num_turns=0,
                    duration_ms=int((time.time() - start_time) * 1000),
                    tools_summary=[],
                )
                return

            # ストリーミング実行
            logger.info("Claude Agent SDK query()実行開始", user_input=request.user_input[:100])
            async for message in query(
                prompt=request.user_input,
                options=sdk_options,
            ):
                message_seq += 1
                timestamp = datetime.utcnow()
                logger.debug("メッセージ受信", seq=message_seq, type=getattr(message, 'type', 'unknown'))

                # メッセージログに保存
                log_entry = {
                    "type": getattr(message, "type", "unknown"),
                    "subtype": getattr(message, "subtype", None),
                    "timestamp": timestamp.isoformat(),
                }

                # メッセージタイプに応じた処理
                msg_type = getattr(message, "type", None)

                # システムメッセージ（init）
                if msg_type == "system":
                    subtype = getattr(message, "subtype", None)
                    if subtype == "init":
                        session_id = getattr(message, "session_id", None)
                        tools = getattr(message, "tools", [])
                        model_name = getattr(message, "model", model.display_name)

                        # セッションIDを更新
                        if session_id:
                            parent_id = (
                                request.resume_session_id
                                if request.fork_session
                                else None
                            )
                            await self.session_service.update_session(
                                chat_session_id=request.chat_session_id,
                                tenant_id=tenant_id,
                                session_id=session_id,
                                parent_session_id=parent_id,
                            )

                        yield format_session_start_event(
                            session_id=session_id or "",
                            tools=tools,
                            model=model_name,
                        )

                # アシスタントメッセージ
                elif msg_type == "assistant":
                    content_blocks = getattr(message, "content", [])
                    for content in content_blocks:
                        content_type = getattr(content, "type", None)

                        # テキストコンテンツ
                        if content_type == "text":
                            text = getattr(content, "text", "")
                            assistant_text += text
                            yield format_text_delta_event(text)

                        # ツール使用開始
                        elif content_type == "tool_use":
                            tool_id = getattr(content, "id", str(uuid4()))
                            tool_name = getattr(content, "name", "unknown")
                            tool_input = getattr(content, "input", {})

                            current_tool = {
                                "tool_use_id": tool_id,
                                "tool_name": tool_name,
                                "tool_input": tool_input,
                                "status": "running",
                                "started_at": timestamp,
                            }

                            summary = generate_tool_summary(tool_name, tool_input)
                            yield format_tool_start_event(tool_id, tool_name, summary)

                        # 思考プロセス
                        elif content_type == "thinking":
                            thinking_text = getattr(content, "text", "")
                            yield format_thinking_event(thinking_text)

                # ツール結果
                elif msg_type == "tool_result":
                    if current_tool:
                        is_error = getattr(message, "is_error", False)
                        output = getattr(message, "content", None)

                        current_tool["status"] = "error" if is_error else "completed"
                        current_tool["completed_at"] = timestamp
                        current_tool["output"] = output

                        # ツール実行ログを保存
                        execution_time_ms = None
                        if current_tool.get("started_at"):
                            delta = timestamp - current_tool["started_at"]
                            execution_time_ms = int(delta.total_seconds() * 1000)

                        if session_id:
                            await self.usage_service.save_tool_log(
                                session_id=session_id,
                                tool_name=current_tool["tool_name"],
                                tool_use_id=current_tool["tool_use_id"],
                                tool_input=current_tool["tool_input"],
                                tool_output={"content": str(output)[:1000]}
                                if output
                                else None,
                                status=current_tool["status"],
                                execution_time_ms=execution_time_ms,
                                chat_session_id=request.chat_session_id,
                            )

                        result_summary = generate_tool_result_summary(
                            current_tool["tool_name"],
                            current_tool["status"],
                            output,
                        )
                        tools_used.append(
                            {
                                "tool_name": current_tool["tool_name"],
                                "status": current_tool["status"],
                                "summary": result_summary,
                            }
                        )

                        yield format_tool_complete_event(
                            tool_use_id=current_tool["tool_use_id"],
                            tool_name=current_tool["tool_name"],
                            status=current_tool["status"],
                            summary=result_summary,
                        )
                        current_tool = None

                # ストリームイベント（増分更新）
                elif msg_type == "stream_event":
                    event = getattr(message, "event", None)
                    if event:
                        event_type = getattr(event, "type", None)
                        if event_type == "content_block_delta":
                            delta = getattr(event, "delta", None)
                            if delta and getattr(delta, "type", None) == "text_delta":
                                text = getattr(delta, "text", "")
                                assistant_text += text
                                yield format_text_delta_event(text)

                # 結果メッセージ
                elif msg_type == "result":
                    usage = getattr(message, "usage", None)
                    subtype = getattr(message, "subtype", "success")
                    result_errors = getattr(message, "errors", None)

                    if result_errors:
                        errors.extend(result_errors)

                    # 使用状況の取得
                    input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
                    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
                    cache_creation = (
                        getattr(usage, "cache_creation_input_tokens", 0)
                        if usage
                        else 0
                    )
                    cache_read = (
                        getattr(usage, "cache_read_input_tokens", 0) if usage else 0
                    )
                    total_cost = getattr(message, "total_cost_usd", 0) or 0
                    num_turns = getattr(message, "num_turns", 1)
                    duration_ms = int((time.time() - start_time) * 1000)

                    # コストを計算（SDKから取得できない場合）
                    if not total_cost:
                        total_cost = float(
                            model.calculate_cost(
                                input_tokens, output_tokens, cache_creation, cache_read
                            )
                        )

                    # 使用状況ログを保存
                    await self.usage_service.save_usage_log(
                        tenant_id=tenant_id,
                        user_id=request.executor.user_id,
                        model_id=request.model_id,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cache_creation_tokens=cache_creation,
                        cache_read_tokens=cache_read,
                        cost_usd=Decimal(str(total_cost)),
                        agent_config_id=request.agent_config_id,
                        session_id=session_id,
                        chat_session_id=request.chat_session_id,
                    )

                    # 表示キャッシュを保存
                    await self.session_service.save_display_cache(
                        chat_session_id=request.chat_session_id,
                        turn_number=turn_number,
                        user_message=request.user_input,
                        assistant_message=assistant_text,
                        tools_summary=tools_used,
                        metadata={
                            "tokens": input_tokens + output_tokens,
                            "cost_usd": total_cost,
                            "duration_ms": duration_ms,
                            "num_turns": num_turns,
                        },
                    )

                    # 結果イベントを送信
                    yield format_result_event(
                        subtype=subtype,
                        result=assistant_text if subtype == "success" else None,
                        errors=errors if errors else None,
                        usage={
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cache_creation_tokens": cache_creation,
                            "cache_read_tokens": cache_read,
                            "total_tokens": input_tokens + output_tokens,
                        },
                        cost_usd=total_cost,
                        num_turns=num_turns,
                        duration_ms=duration_ms,
                        tools_summary=tools_used,
                    )

                # メッセージログを保存
                await self.session_service.save_message_log(
                    chat_session_id=request.chat_session_id,
                    message_seq=message_seq,
                    message_type=msg_type or "unknown",
                    message_subtype=getattr(message, "subtype", None),
                    content=log_entry,
                )

        except Exception as e:
            # エラー処理
            error_message = str(e)
            duration_ms = int((time.time() - start_time) * 1000)

            # ProcessErrorの場合は詳細情報を取得
            if hasattr(e, "exit_code") and hasattr(e, "stderr"):
                error_message = (
                    f"Command failed with exit code {e.exit_code}\n"
                    f"Error details: {e.stderr}"
                )
                logger.error(
                    "エージェント実行エラー (ProcessError)",
                    exit_code=e.exit_code,
                    stderr=e.stderr,
                    exc_info=True,
                )
            else:
                logger.error("エージェント実行エラー", error=error_message, exc_info=True)

            yield format_error_event(error_message, "execution_error")

            # エラー結果を送信
            yield format_result_event(
                subtype="error_during_execution",
                result=None,
                errors=[error_message],
                usage={
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 0,
                    "total_tokens": 0,
                },
                cost_usd=0,
                num_turns=0,
                duration_ms=duration_ms,
                tools_summary=tools_used,
            )

        finally:
            # コミット
            await self.db.commit()
