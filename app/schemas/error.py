"""
エラーレスポンススキーマ

統一されたエラーレスポンス形式を定義
"""
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """エラー詳細"""
    field: str | None = Field(None, description="エラーが発生したフィールド")
    message: str = Field(..., description="エラーメッセージ")
    code: str | None = Field(None, description="エラーコード")


class ErrorResponse(BaseModel):
    """
    統一エラーレスポンス

    RFC 7807 Problem Details for HTTP APIsを参考にした形式
    """
    error: "ErrorBody"

    class Config:
        json_schema_extra = {
            "example": {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "入力データが不正です",
                    "details": [
                        {
                            "field": "email",
                            "message": "有効なメールアドレスを入力してください",
                        }
                    ],
                    "request_id": "550e8400-e29b-41d4-a716-446655440000",
                    "timestamp": "2024-01-15T10:30:00Z",
                }
            }
        }


class ErrorBody(BaseModel):
    """エラー本体"""
    code: str = Field(..., description="エラーコード")
    message: str = Field(..., description="ユーザー向けエラーメッセージ")
    details: list[ErrorDetail] | None = Field(
        None,
        description="エラー詳細のリスト",
    )
    request_id: str | None = Field(
        None,
        description="リクエストID（トレーシング用）",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="エラー発生時刻",
    )


# ErrorResponseのモデル再構築（前方参照の解決）
ErrorResponse.model_rebuild()


def create_error_response(
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    request_id: str | None = None,
) -> dict:
    """
    エラーレスポンスを作成

    Args:
        code: エラーコード
        message: ユーザー向けメッセージ
        details: エラー詳細のリスト
        request_id: リクエストID

    Returns:
        エラーレスポンス辞書
    """
    error_details = None
    if details:
        error_details = [
            ErrorDetail(
                field=d.get("field"),
                message=d.get("message", d.get("msg", "")),
                code=d.get("code"),
            )
            for d in details
        ]

    return ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=error_details,
            request_id=request_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    ).model_dump()


# よく使用するエラーコード
class ErrorCodes:
    """エラーコード定数"""
    # 認証・認可
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"

    # リソース
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    GONE = "GONE"

    # バリデーション
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_INPUT = "INVALID_INPUT"

    # レート制限
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"

    # セキュリティ
    SECURITY_ERROR = "SECURITY_ERROR"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"

    # ビジネスロジック
    RESOURCE_LOCKED = "RESOURCE_LOCKED"
    RESOURCE_INACTIVE = "RESOURCE_INACTIVE"

    # サーバーエラー
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    EXTERNAL_SERVICE_ERROR = "EXTERNAL_SERVICE_ERROR"
