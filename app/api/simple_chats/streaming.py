"""
シンプルチャットストリーミング実行エンドポイント
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.dependencies import get_active_model, get_active_tenant
from app.database import get_db
from app.models.model import Model
from app.models.tenant import Tenant
from app.schemas.simple_chat import SimpleChatStreamRequest
from app.services.simple_chat_service import SimpleChatService

router = APIRouter()
logger = logging.getLogger(__name__)


async def _simple_chat_event_generator(service, chat, model: Model, user_message: str):
    """シンプルチャットのSSEイベントジェネレータ"""
    try:
        async for event in service.stream_message(chat, model, user_message):
            yield {
                "event": event["event_type"],
                "data": json.dumps(event, ensure_ascii=False, default=str),
            }
    except Exception as e:
        logger.error(
            f"Simple chat streaming error: {e}",
            exc_info=True,
            extra={"chat_id": chat.chat_id},
        )
        yield {
            "event": "error",
            "data": json.dumps(
                {
                    "seq": 0,
                    "event_type": "error",
                    "message": str(e),
                    "error_type": type(e).__name__,
                    "recoverable": False,
                },
                ensure_ascii=False,
            ),
        }


@router.post(
    "/stream",
    summary="シンプルチャットストリーミング実行",
)
async def stream_simple_chat(
    tenant_id: str,
    request: SimpleChatStreamRequest,
    tenant: Tenant = Depends(get_active_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    シンプルチャットのストリーミング実行を行います。

    ## 動作モード

    - **新規作成**: `chat_id` を指定しない場合、新しいチャットを作成
    - **継続**: `chat_id` を指定した場合、既存のチャットを継続
    """
    service = SimpleChatService(db)
    response_headers = {}

    if request.chat_id:
        # 継続モード
        chat = await service.get_chat_by_id(request.chat_id, tenant_id)
        if not chat:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"チャット '{request.chat_id}' が見つかりません",
            )
        if chat.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"チャット '{request.chat_id}' はアーカイブされています",
            )

        model = await get_active_model(chat.model_id, db)

    else:
        # 新規作成モード
        _validate_new_chat_request(request)

        model = await get_active_model(request.model_id, db)

        chat = await service.create_chat(
            tenant_id=tenant_id,
            user_id=request.user_id,
            model_id=request.model_id,
            application_type=request.application_type,
            system_prompt=request.system_prompt,
        )
        await db.commit()

        response_headers["X-Chat-ID"] = chat.chat_id

    return EventSourceResponse(
        _simple_chat_event_generator(
            service=service,
            chat=chat,
            model=model,
            user_message=request.message,
        ),
        media_type="text/event-stream",
        headers=response_headers if response_headers else None,
    )


def _validate_new_chat_request(request: SimpleChatStreamRequest) -> None:
    """新規チャット作成時の必須パラメータをバリデーション"""
    required_fields = {
        "user_id": "新規作成時は user_id が必須です",
        "application_type": "新規作成時は application_type が必須です",
        "system_prompt": "新規作成時は system_prompt が必須です",
        "model_id": "新規作成時は model_id が必須です",
    }
    for field, message in required_fields.items():
        if not getattr(request, field, None):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message,
            )
