"""
エージェント実行API
ストリーミング対応のエージェント実行
"""
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.schemas.execute import ExecuteRequest
from app.services.agent_config_service import AgentConfigService
from app.services.execute_service import ExecuteService
from app.services.model_service import ModelService

router = APIRouter()


async def event_generator(
    execute_service: ExecuteService,
    request: ExecuteRequest,
    agent_config,
    model,
    tenant_id: str,
) -> AsyncIterator[dict]:
    """
    SSEイベントジェネレータ

    Args:
        execute_service: 実行サービス
        request: 実行リクエスト
        agent_config: エージェント設定
        model: モデル定義
        tenant_id: テナントID

    Yields:
        SSEイベント
    """
    async for event in execute_service.execute_streaming(
        request=request,
        agent_config=agent_config,
        model=model,
        tenant_id=tenant_id,
    ):
        yield {
            "event": event["event"],
            "data": json.dumps(event["data"], ensure_ascii=False, default=str),
        }


@router.post("/execute", summary="エージェント実行")
async def execute_agent(
    tenant_id: str,
    request: ExecuteRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    エージェントを実行します（ストリーミングレスポンス）。

    レスポンスはServer-Sent Events (SSE) 形式でストリーミング送信されます。

    ## リクエストパラメータ

    - **agent_config_id**: エージェント実行設定ID
    - **model_id**: 使用するモデルID
    - **chat_session_id**: セッションID（新規 or 継続）
    - **user_input**: ユーザー入力
    - **executor**: 実行者情報
    - **tokens**: MCPサーバー用認証情報（オプション）
    - **resume_session_id**: 継続するSDKセッションID（オプション）
    - **fork_session**: セッションをフォークするか（オプション）

    ## イベントタイプ

    - **session_start**: セッション開始
    - **text_delta**: テキスト増分
    - **tool_start**: ツール使用開始
    - **tool_complete**: ツール使用完了
    - **thinking**: 思考プロセス
    - **result**: 最終結果
    - **error**: エラー
    """
    # エージェント設定の取得
    config_service = AgentConfigService(db)
    agent_config = await config_service.get_by_id(request.agent_config_id, tenant_id)
    if not agent_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"エージェント設定 '{request.agent_config_id}' が見つかりません",
        )
    if agent_config.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"エージェント設定 '{request.agent_config_id}' は無効です",
        )

    # モデル定義の取得
    model_service = ModelService(db)
    model = await model_service.get_by_id(request.model_id)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"モデル '{request.model_id}' が見つかりません",
        )
    if model.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"モデル '{request.model_id}' は非推奨です",
        )

    # 実行サービスの作成
    execute_service = ExecuteService(db)

    # SSEレスポンスを返す
    return EventSourceResponse(
        event_generator(
            execute_service=execute_service,
            request=request,
            agent_config=agent_config,
            model=model,
            tenant_id=tenant_id,
        ),
        media_type="text/event-stream",
    )


@router.post("/sessions/{session_id}/stop", summary="実行停止リクエスト")
async def stop_execution(
    tenant_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    実行中のエージェントに停止リクエストを送信します。

    **重要:**
    - このAPIは停止リクエストを送信するのみで、即座に処理を中断しません
    - バックエンドの処理は現在のターンの完了まで継続されます
    - ストリーミング接続がクローズされても、バックエンド処理は最後まで実行されます

    **将来の実装予定:**
    - 停止リクエストのフラグを管理し、処理の適切なタイミングで停止する機能
    - 停止状態の確認API
    """
    # TODO: 停止リクエストの実装
    # - セッションの停止フラグを設定
    # - 実行サービス側で停止フラグをチェックし、適切なタイミングで処理を終了
    # - 現在は未実装のため、受け付けのみを返す
    return {"status": "accepted", "message": "停止リクエストを受け付けました"}
