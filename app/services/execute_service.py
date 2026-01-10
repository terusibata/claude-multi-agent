"""
エージェント実行サービス
Claude Agent SDKを使用したエージェント実行とストリーミング処理
"""
import json
import os
import structlog
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, AsyncGenerator, Optional
from uuid import uuid4

import boto3
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
    format_title_generated_event,
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

    def _generate_title_sync(
        self,
        user_input: str,
        assistant_response: str,
        model_region: str = "us-east-1",
    ) -> str:
        """
        会話からタイトルを生成（同期版）

        Args:
            user_input: ユーザー入力
            assistant_response: アシスタント応答
            model_region: AWSリージョン

        Returns:
            生成されたタイトル（最大50文字）
        """
        try:
            # Bedrock Runtimeクライアントを作成
            bedrock_runtime = boto3.client(
                service_name="bedrock-runtime",
                region_name=model_region,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                aws_session_token=settings.aws_session_token if settings.aws_session_token else None,
            )

            # タイトル生成用のプロンプト
            prompt = f"""以下の会話から、短く簡潔な日本語のタイトルを生成してください。
タイトルは20文字以内にしてください。

ユーザー入力:
{user_input[:200]}

アシスタント応答:
{assistant_response[:300]}

タイトルのみを出力してください。説明は不要です。"""

            # Bedrock経由でClaude APIを呼び出し
            request_body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": prompt}],
            }

            response = bedrock_runtime.invoke_model(
                modelId="us.anthropic.claude-3-5-haiku-20241022-v1:0",  # 高速・低コストなモデル
                body=json.dumps(request_body),
            )

            # レスポンスをパース
            response_body = json.loads(response["body"].read())
            title = response_body["content"][0]["text"].strip()

            # 最大50文字に制限
            if len(title) > 50:
                title = title[:50]

            logger.info("タイトル生成成功", title=title)
            return title

        except Exception as e:
            logger.warning("タイトル生成失敗、デフォルトタイトル使用", error=str(e))
            # 失敗した場合はユーザー入力の最初の部分を使用
            return user_input[:50] if user_input else "新しいチャット"

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
                # resume_session_idが指定されていない場合、前回のセッションを自動引き継ぎ
                if not request.resume_session_id and existing_session.session_id:
                    request.resume_session_id = existing_session.session_id
                    logger.info(
                        "前回のセッションを自動復元",
                        session_id=existing_session.session_id
                    )

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

            # ターン番号を取得
            turn_number = await self.session_service.get_latest_turn_number(
                request.chat_session_id
            ) + 1
            logger.info("ターン番号取得", turn_number=turn_number)

            # Claude Agent SDKをインポート
            logger.info("Claude Agent SDK インポート中...")
            try:
                from claude_agent_sdk import (
                    ClaudeAgentOptions,
                    query,
                    AssistantMessage,
                    SystemMessage,
                    ResultMessage,
                    TextBlock,
                    ThinkingBlock,
                    ToolUseBlock,
                )
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

                # メッセージタイプを判定
                msg_type = "unknown"
                if isinstance(message, SystemMessage):
                    msg_type = "system"
                elif isinstance(message, AssistantMessage):
                    msg_type = "assistant"
                elif isinstance(message, ResultMessage):
                    msg_type = "result"

                logger.debug("メッセージ受信", seq=message_seq, type=msg_type)

                # メッセージログに保存用のエントリ
                log_entry = {
                    "type": msg_type,
                    "subtype": getattr(message, "subtype", None),
                    "timestamp": timestamp.isoformat(),
                }

                # システムメッセージ
                if isinstance(message, SystemMessage):
                    subtype = message.subtype
                    data = message.data

                    # ログエントリに詳細を追加
                    log_entry["data"] = data

                    if subtype == "init":
                        session_id = data.get("session_id")
                        tools = data.get("tools", [])
                        model_name = data.get("model", model.display_name)

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
                elif isinstance(message, AssistantMessage):
                    content_blocks = message.content

                    # ログエントリに詳細を追加
                    log_entry["content_blocks"] = []

                    for content in content_blocks:
                        # テキストブロック
                        if isinstance(content, TextBlock):
                            text = content.text
                            assistant_text += text
                            log_entry["content_blocks"].append({"type": "text", "text": text})
                            yield format_text_delta_event(text)

                        # ツール使用ブロック
                        elif isinstance(content, ToolUseBlock):
                            tool_id = content.id
                            tool_name = content.name
                            tool_input = content.input

                            current_tool = {
                                "tool_use_id": tool_id,
                                "tool_name": tool_name,
                                "tool_input": tool_input,
                                "status": "running",
                                "started_at": timestamp,
                            }

                            log_entry["content_blocks"].append({
                                "type": "tool_use",
                                "id": tool_id,
                                "name": tool_name,
                                "input": tool_input,
                            })

                            summary = generate_tool_summary(tool_name, tool_input)
                            yield format_tool_start_event(tool_id, tool_name, summary)

                        # 思考ブロック
                        elif isinstance(content, ThinkingBlock):
                            thinking_text = content.text
                            log_entry["content_blocks"].append({"type": "thinking", "text": thinking_text})
                            yield format_thinking_event(thinking_text)

                # 結果メッセージ
                elif isinstance(message, ResultMessage):
                    subtype = message.subtype
                    usage_data = message.usage

                    # ログエントリに詳細を追加
                    log_entry["result"] = message.result
                    log_entry["is_error"] = message.is_error
                    log_entry["usage"] = usage_data
                    log_entry["total_cost_usd"] = message.total_cost_usd
                    log_entry["num_turns"] = message.num_turns
                    log_entry["session_id"] = message.session_id

                    # 使用状況の取得
                    input_tokens = usage_data.get("input_tokens", 0) if usage_data else 0
                    output_tokens = usage_data.get("output_tokens", 0) if usage_data else 0
                    cache_creation = usage_data.get("cache_creation_input_tokens", 0) if usage_data else 0
                    cache_read = usage_data.get("cache_read_input_tokens", 0) if usage_data else 0
                    total_cost = message.total_cost_usd or 0
                    num_turns = message.num_turns
                    duration_ms = int((time.time() - start_time) * 1000)

                    # エラーチェック
                    if message.is_error:
                        errors.append(message.result or "Unknown error")

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

                    # 初回実行時のみタイトルを生成
                    if turn_number == 1 and assistant_text and subtype == "success":
                        logger.info("初回実行のためタイトル生成中...")
                        generated_title = self._generate_title_sync(
                            user_input=request.user_input,
                            assistant_response=assistant_text,
                            model_region=model.model_region or settings.aws_region,
                        )
                        # タイトルを更新
                        await self.session_service.update_session_title(
                            chat_session_id=request.chat_session_id,
                            tenant_id=tenant_id,
                            title=generated_title,
                        )
                        logger.info("タイトル更新完了", title=generated_title)

                        # タイトル生成イベントをストリーミングで送信
                        yield format_title_generated_event(generated_title)

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
                    message_type=msg_type,
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
