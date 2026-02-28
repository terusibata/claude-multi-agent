"""
イベント変換モジュール

AgentCore Runtime コンテナ（workspace-agent）から送信されるSSEイベントを
ホスト側の正規形式に変換するロジックを提供する。

変換対象:
  SDK形式: text_delta, thinking, tool_use, tool_result, done, system, error
  ホスト形式: init, progress, assistant, thinking, tool_call, tool_result, done, error
"""

import json

from app.utils.streaming import (
    SequenceCounter,
    create_event,
    format_assistant_event,
    format_container_recovered_event,
    format_done_event,
    format_init_event,
    format_progress_event,
    format_subagent_end_event,
    format_subagent_start_event,
    format_thinking_event,
    format_tool_call_event,
    format_tool_result_event,
)
from app.utils.progress_messages import get_initial_message

# サブエージェントツール名（"Task"は SDK 内部名、"Agent"は新名称）
_SUBAGENT_TOOL_NAMES = {"Agent", "Task"}


class EventTranslator:
    """SDKイベントをホスト正規形式に変換するトランスレータ"""

    def __init__(self) -> None:
        # サブエージェントの agent_id → agent_type マッピング
        # tool_use 時に記録し、tool_result 時に取り出して subagent_end に使用
        self._subagent_types: dict[str, str] = {}

    def translate_event(
        self,
        raw_event: dict,
        seq_counter: SequenceCounter,
        conversation_id: str | None = None,
    ) -> list[dict]:
        """
        SDKイベントをホスト正規形式に変換

        AgentCore Runtime コンテナ（workspace-agent）が送信するイベント形式:
          text_delta, thinking, tool_use, tool_result, done, system, error
        を、ホスト側の正規形式:
          init, progress, assistant, thinking, tool_call, tool_result, done, error
        に変換し、seq と timestamp を付与する。

        Returns:
            変換後イベントのリスト（1つのSDKイベントから複数のホストイベントを返す場合あり）
        """
        event_type = raw_event.get("event", "")
        data = raw_event.get("data", {})

        # parent_tool_use_id → parent_agent_id 変換
        # サブエージェント内のイベントには SDK が parent_tool_use_id を付与する
        parent_agent_id: str | None = data.get("parent_tool_use_id")

        if event_type == "system" and data.get("subtype") == "init":
            # SDK system(init) → 仕様準拠の init イベントに変換
            init_data = (
                data.get("data", {}) if isinstance(data.get("data"), dict) else data
            )
            tools = list(init_data.get("tools", []))

            # MCP サーバーのツール名を追加
            # SDK init メッセージの mcp_servers フィールドから接続済みサーバーの
            # ツール名を抽出し、mcp__<server>__<tool> 形式で tools リストに追加
            for mcp_server in init_data.get("mcp_servers", []):
                server_name = mcp_server.get("name", "")
                status = mcp_server.get("status", "")
                if server_name and status == "connected":
                    for tool_name in mcp_server.get("tools", []):
                        tools.append(f"mcp__{server_name}__{tool_name}")

            return [
                format_init_event(
                    seq=seq_counter.next(),
                    session_id=init_data.get("session_id", ""),
                    tools=tools,
                    model=init_data.get("model", ""),
                    conversation_id=conversation_id,
                )
            ]
        elif event_type == "text_delta":
            # progress(generating) + assistant
            return [
                format_progress_event(
                    seq=seq_counter.next(),
                    progress_type="generating",
                    message=get_initial_message("generating"),
                    parent_agent_id=parent_agent_id,
                ),
                format_assistant_event(
                    seq=seq_counter.next(),
                    content_blocks=[{"type": "text", "text": data.get("text", "")}],
                    parent_agent_id=parent_agent_id,
                ),
            ]
        elif event_type == "thinking":
            # progress(thinking) + thinking
            return [
                format_progress_event(
                    seq=seq_counter.next(),
                    progress_type="thinking",
                    message=get_initial_message("thinking"),
                    parent_agent_id=parent_agent_id,
                ),
                format_thinking_event(
                    seq=seq_counter.next(),
                    content=data.get("content", ""),
                    parent_agent_id=parent_agent_id,
                ),
            ]
        elif event_type == "tool_use":
            # progress(tool, running) + tool_call
            tool_name = data.get("tool_name", "")
            tool_use_id = data.get("tool_use_id", "")
            tool_input = data.get("input", {})
            events = [
                format_progress_event(
                    seq=seq_counter.next(),
                    progress_type="tool",
                    message=get_initial_message("tool", tool_name),
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    tool_status="running",
                    parent_agent_id=parent_agent_id,
                ),
                format_tool_call_event(
                    seq=seq_counter.next(),
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    summary=f"ツール実行: {tool_name}",
                    parent_agent_id=parent_agent_id,
                ),
            ]

            # サブエージェント検出 → subagent_start を追加
            # メインエージェントからの tool_use のみ対象（サブエージェント内の tool_use は除外）
            if (
                tool_name in _SUBAGENT_TOOL_NAMES
                and tool_use_id not in self._subagent_types
                and not parent_agent_id
            ):
                agent_type = tool_input.get("subagent_type", "") if isinstance(tool_input, dict) else ""
                description = tool_input.get("description", "") if isinstance(tool_input, dict) else ""
                model = tool_input.get("model") if isinstance(tool_input, dict) else None
                self._subagent_types[tool_use_id] = agent_type
                events.append(
                    format_subagent_start_event(
                        seq=seq_counter.next(),
                        agent_id=tool_use_id,
                        agent_type=agent_type,
                        description=description,
                        model=model,
                    )
                )

            return events
        elif event_type == "tool_result":
            tool_use_id = data.get("tool_use_id", "")
            tool_name = data.get("tool_name", "")
            is_error = data.get("is_error", False)
            content = data.get("content", "")

            events = [
                format_tool_result_event(
                    seq=seq_counter.next(),
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    status="error" if is_error else "completed",
                    content=content,
                    is_error=is_error,
                    parent_agent_id=parent_agent_id,
                )
            ]

            # サブエージェント完了検出 → subagent_end を追加
            agent_type = self._subagent_types.pop(tool_use_id, None)
            if agent_type is not None:
                preview = content[:200] if isinstance(content, str) else ""
                events.append(
                    format_subagent_end_event(
                        seq=seq_counter.next(),
                        agent_id=tool_use_id,
                        agent_type=agent_type,
                        status="error" if is_error else "completed",
                        result_preview=preview if preview else None,
                    )
                )

            return events
        elif event_type == "done":
            return [
                format_done_event(
                    seq=seq_counter.next(),
                    status="error"
                    if data.get("subtype") == "error_during_execution"
                    else "success",
                    result=data.get("result"),
                    errors=None,
                    usage=EventTranslator.normalize_usage(data.get("usage", {})),
                    cost_usd=str(data.get("cost_usd", "0")),
                    turn_count=data.get("num_turns", 0),
                    duration_ms=data.get("duration_ms", 0),
                    session_id=data.get("session_id"),
                    model_usage=data.get("model_usage"),
                )
            ]
        elif event_type == "file_manifest":
            # file_manifest はホスト側で内部処理する（クライアントには転送しない）
            # execute_service._handle_file_manifest() で処理済み
            return []
        elif event_type == "container_recovered":
            return [
                format_container_recovered_event(
                    seq=seq_counter.next(),
                    message=data.get("message", "Container recovered"),
                    recovered=data.get("recovered", True),
                    retry_recommended=data.get("retry_recommended", True),
                )
            ]
        elif event_type == "subagent_start":
            # ネイティブ subagent_start イベント（将来の SDK バージョンに備える）
            agent_id = data.get("agent_id", "")
            agent_type = data.get("agent_type", "")
            self._subagent_types[agent_id] = agent_type
            return [
                format_subagent_start_event(
                    seq=seq_counter.next(),
                    agent_id=agent_id,
                    agent_type=agent_type,
                    description=data.get("description", ""),
                    model=data.get("model"),
                )
            ]
        elif event_type == "subagent_end":
            # ネイティブ subagent_end イベント（将来の SDK バージョンに備える）
            agent_id = data.get("agent_id", "")
            self._subagent_types.pop(agent_id, None)
            return [
                format_subagent_end_event(
                    seq=seq_counter.next(),
                    agent_id=agent_id,
                    agent_type=data.get("agent_type", ""),
                    status=data.get("status", "completed"),
                    result_preview=data.get("result_preview"),
                )
            ]
        else:
            # error 等: seq/timestamp を付与してそのまま中継
            return [create_event(event_type, seq_counter.next(), data)]

    @staticmethod
    def normalize_usage(raw_usage: dict) -> dict:
        """
        SDK usage フォーマットを仕様準拠のフォーマットに正規化（冪等）

        SDK形式:
          input_tokens, output_tokens, cache_creation_input_tokens,
          cache_read_input_tokens, cache_creation.ephemeral_5m_input_tokens, ...
        仕様形式:
          input_tokens, output_tokens, cache_creation_5m_tokens,
          cache_creation_1h_tokens, cache_read_tokens, total_tokens
        """
        input_tokens = raw_usage.get("input_tokens", 0)
        output_tokens = raw_usage.get("output_tokens", 0)

        # 正規化済みキーが存在する場合はそのまま返す（冪等性）
        if "cache_creation_5m_tokens" in raw_usage:
            cache_5m = raw_usage["cache_creation_5m_tokens"]
            cache_1h = raw_usage.get("cache_creation_1h_tokens", 0)
            cache_read = raw_usage.get("cache_read_tokens", 0)
        else:
            # SDK生フォーマットから正規化
            cache_creation = raw_usage.get("cache_creation", {})
            if isinstance(cache_creation, dict):
                cache_5m = cache_creation.get("ephemeral_5m_input_tokens", 0)
                cache_1h = cache_creation.get("ephemeral_1h_input_tokens", 0)
            else:
                cache_5m = 0
                cache_1h = 0

            # フォールバック: トップレベルの cache_creation_input_tokens を 5m として扱う
            if cache_5m == 0:
                cache_5m = raw_usage.get("cache_creation_input_tokens", 0)

            cache_read = raw_usage.get("cache_read_input_tokens", 0)

        total_tokens = input_tokens + output_tokens + cache_5m + cache_1h + cache_read

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_5m_tokens": cache_5m,
            "cache_creation_1h_tokens": cache_1h,
            "cache_read_tokens": cache_read,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def parse_sse_event(event_str: str) -> dict | None:
        """SSEイベント文字列をパース"""
        event_type = "message"
        data_str = ""

        for line in event_str.strip().split("\n"):
            if line.startswith("event: "):
                event_type = line[7:].strip()
            elif line.startswith("data: "):
                data_str = line[6:]

        if not data_str:
            return None

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            data = {"raw": data_str}

        return {"event": event_type, "data": data}

