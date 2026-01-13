"""
エージェント実行API
ストリーミング対応のエージェント実行（ファイル添付対応）
"""
import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.database import get_db
from app.schemas.execute import ExecuteRequest
from app.services.agent_config_service import AgentConfigService
from app.services.execute_service import ExecuteService
from app.services.model_service import ModelService
from app.services.workspace_service import WorkspaceService
from app.utils.streaming import format_error_event

router = APIRouter()
logger = logging.getLogger(__name__)

# タイムアウト関連の定数
EVENT_TIMEOUT_SECONDS = 300  # イベント待機タイムアウト（秒）
MAX_CONSECUTIVE_TIMEOUTS = 3  # 連続タイムアウトの最大回数


async def _background_execution(
    execute_service: ExecuteService,
    request: ExecuteRequest,
    agent_config,
    model,
    tenant_id: str,
    event_queue: asyncio.Queue,
) -> None:
    """
    バックグラウンドでエージェントを実行し、イベントをキューに送信

    Args:
        execute_service: 実行サービス
        request: 実行リクエスト
        agent_config: エージェント設定
        model: モデル定義
        tenant_id: テナントID
        event_queue: イベントキュー
    """
    try:
        async for event in execute_service.execute_streaming(
            request=request,
            agent_config=agent_config,
            model=model,
            tenant_id=tenant_id,
        ):
            await event_queue.put(event)
    except Exception as e:
        logger.error(
            f"Background execution error: {e}",
            exc_info=True,
            extra={"chat_session_id": request.chat_session_id},
        )
        # エラーイベントをキューに送信してクライアントに通知
        error_event = format_error_event(
            f"バックグラウンド実行エラー: {str(e)}",
            "background_execution_error",
        )
        await event_queue.put(error_event)
    finally:
        # 終了シグナルを送信
        await event_queue.put(None)


async def event_generator(
    execute_service: ExecuteService,
    request: ExecuteRequest,
    agent_config,
    model,
    tenant_id: str,
) -> AsyncIterator[dict]:
    """
    SSEイベントジェネレータ
    クライアントが切断しても、バックグラウンド処理は継続します。

    Args:
        execute_service: 実行サービス
        request: 実行リクエスト
        agent_config: エージェント設定
        model: モデル定義
        tenant_id: テナントID

    Yields:
        SSEイベント
    """
    event_queue: asyncio.Queue = asyncio.Queue()
    consecutive_timeouts = 0

    # バックグラウンドタスクとして実行を開始
    background_task = asyncio.create_task(
        _background_execution(
            execute_service,
            request,
            agent_config,
            model,
            tenant_id,
            event_queue,
        )
    )

    try:
        while True:
            try:
                # タイムアウトを設定してキューから取得
                event = await asyncio.wait_for(
                    event_queue.get(),
                    timeout=EVENT_TIMEOUT_SECONDS,
                )

                # イベントを受信したらタイムアウトカウントをリセット
                consecutive_timeouts = 0

                if event is None:
                    # 処理完了シグナル
                    break

                yield {
                    "event": event["event"],
                    "data": json.dumps(event["data"], ensure_ascii=False, default=str),
                }
            except asyncio.TimeoutError:
                consecutive_timeouts += 1
                logger.warning(
                    f"Event queue timeout ({consecutive_timeouts}/{MAX_CONSECUTIVE_TIMEOUTS})",
                    extra={"chat_session_id": request.chat_session_id},
                )

                # バックグラウンドタスクが完了しているかチェック
                if background_task.done():
                    logger.info(
                        "Background task completed during timeout",
                        extra={"chat_session_id": request.chat_session_id},
                    )
                    break

                # 連続タイムアウトが最大回数を超えたらエラーイベントを送信して終了
                if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                    logger.error(
                        f"Max consecutive timeouts reached, terminating",
                        extra={"chat_session_id": request.chat_session_id},
                    )
                    error_event = format_error_event(
                        "応答タイムアウト: サーバーからの応答がありません",
                        "timeout_error",
                    )
                    yield {
                        "event": error_event["event"],
                        "data": json.dumps(error_event["data"], ensure_ascii=False, default=str),
                    }
                    break

                continue

    except asyncio.CancelledError:
        # クライアントが接続を切断した場合
        logger.info(
            f"Client disconnected for session {request.chat_session_id}, "
            "but background execution continues"
        )
        # バックグラウンドタスクは継続させる（awaitしない）
        raise
    except Exception as e:
        logger.error(
            f"Event generator error: {e}",
            exc_info=True,
            extra={"chat_session_id": request.chat_session_id},
        )
        background_task.cancel()
        raise
    finally:
        # タスクが完了していない場合、ログに記録
        if not background_task.done():
            logger.info(
                f"Background task continues for session {request.chat_session_id}"
            )


@router.post("/execute", summary="エージェント実行")
async def execute_agent(
    tenant_id: str,
    request_data: str = Form(..., description="ExecuteRequestのJSON文字列"),
    files: list[UploadFile] = File(default=[], description="添付ファイル（複数可、オプション）"),
    db: AsyncSession = Depends(get_db),
):
    """
    エージェントを実行します（ストリーミングレスポンス、ファイル添付対応）。

    レスポンスはServer-Sent Events (SSE) 形式でストリーミング送信されます。

    Content-Type: multipart/form-data

    ## リクエストパラメータ

    - **request_data**: ExecuteRequestのJSON文字列
    - **files**: 添付ファイル（複数可、オプション）

    ## ExecuteRequest JSON フィールド

    - **agent_config_id**: エージェント実行設定ID
    - **model_id**: 使用するモデルID
    - **chat_session_id**: セッションID（省略時は新規作成、指定時は継続）
    - **user_input**: ユーザー入力
    - **executor**: 実行者情報
    - **tokens**: MCPサーバー用認証情報（オプション）
    - **resume_session_id**: 継続するSDKセッションID（オプション）
    - **fork_session**: セッションをフォークするか（オプション）
    - **enable_workspace**: ワークスペースを有効にするか（オプション）

    ## イベントタイプ

    - **session_start**: セッション開始
    - **text_delta**: テキスト増分
    - **tool_use**: ツール使用開始
    - **tool_result**: ツール使用完了
    - **thinking**: 思考プロセス
    - **title_generated**: タイトル生成完了（初回実行時のみ）
    - **result**: 最終結果
    - **error**: エラー
    """
    # リクエストをパース
    try:
        request = ExecuteRequest.model_validate_json(request_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"リクエストデータのパースに失敗しました: {str(e)}",
        )

    # ファイルがある場合はS3にアップロード
    if files:
        workspace_service = WorkspaceService(db)
        file_data = []
        for file in files:
            # ファイル名のバリデーション
            if not file.filename:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="ファイル名が指定されていません",
                )
            content = await file.read()
            content_type = file.content_type or "application/octet-stream"
            file_data.append((file.filename, content, content_type))

        await workspace_service.upload_files(
            tenant_id, request.chat_session_id, file_data
        )

        # ワークスペースを有効化
        request.enable_workspace = True
        await workspace_service.enable_workspace(tenant_id, request.chat_session_id)
        await db.commit()

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
