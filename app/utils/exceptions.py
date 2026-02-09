"""
カスタム例外クラス
アプリケーション全体で使用する例外の定義
"""
from typing import Optional


class AppError(Exception):
    """アプリケーション基底例外"""

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[dict] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or "APP_ERROR"
        self.details = details or {}


class NotFoundError(AppError):
    """リソースが見つからない例外"""

    def __init__(
        self,
        resource_type: str,
        resource_id: str,
        message: Optional[str] = None,
    ):
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(
            message=message or f"{resource_type} '{resource_id}' が見つかりません",
            error_code="NOT_FOUND",
            details={
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
        )


class ValidationError(AppError):
    """バリデーションエラー"""

    def __init__(
        self,
        field: str,
        message: str,
        value: Optional[str] = None,
    ):
        self.field = field
        self.value = value
        super().__init__(
            message=message,
            error_code="VALIDATION_ERROR",
            details={
                "field": field,
                "value": value,
            },
        )


class InactiveResourceError(AppError):
    """リソースが非アクティブな例外"""

    def __init__(
        self,
        resource_type: str,
        resource_id: str,
        status: str,
    ):
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.status = status
        super().__init__(
            message=f"{resource_type} '{resource_id}' は現在 '{status}' 状態です",
            error_code="INACTIVE_RESOURCE",
            details={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "status": status,
            },
        )


class SecurityError(AppError):
    """セキュリティ関連エラー"""

    def __init__(
        self,
        message: str,
        error_code: str = "SECURITY_ERROR",
        details: Optional[dict] = None,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            details=details or {},
        )


class WorkspaceSecurityError(SecurityError):
    """ワークスペースセキュリティエラー"""

    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(
            message=message,
            error_code="WORKSPACE_SECURITY_ERROR",
            details=details,
        )


class PathTraversalError(SecurityError):
    """パストラバーサル攻撃検出エラー"""

    def __init__(self, path: str):
        super().__init__(
            message="パストラバーサル攻撃を検出しました",
            error_code="PATH_TRAVERSAL_ERROR",
            details={"path": path},
        )


class FileSizeError(AppError):
    """ファイルサイズ超過エラー"""

    def __init__(self, filename: str, size: int, max_size: int):
        max_size_mb = max_size // (1024 * 1024)
        size_mb = size / (1024 * 1024)
        super().__init__(
            message=f"ファイル '{filename}' ({size_mb:.1f}MB) が制限サイズ ({max_size_mb}MB) を超えています",
            error_code="FILE_SIZE_EXCEEDED",
            details={
                "filename": filename,
                "size": size,
                "max_size": max_size,
            },
        )


class SDKError(AppError):
    """SDK関連エラー"""

    def __init__(
        self,
        message: str,
        error_code: str = "SDK_ERROR",
        details: Optional[dict] = None,
    ):
        super().__init__(
            message=message,
            error_code=error_code,
            details=details or {},
        )


class SDKNotInstalledError(SDKError):
    """SDKがインストールされていないエラー"""

    def __init__(self, sdk_name: str, original_error: str):
        super().__init__(
            message=f"{sdk_name}がインストールされていません: {original_error}",
            error_code="SDK_NOT_INSTALLED",
            details={
                "sdk_name": sdk_name,
                "original_error": original_error,
            },
        )


class FileOperationError(AppError):
    """ファイル操作エラー"""

    def __init__(
        self,
        operation: str,
        file_path: str,
        original_error: Optional[str] = None,
    ):
        self.operation = operation
        self.file_path = file_path
        self.original_error = original_error
        super().__init__(
            message=f"ファイル{operation}に失敗しました: {file_path}",
            error_code="FILE_OPERATION_ERROR",
            details={
                "operation": operation,
                "file_path": file_path,
                "original_error": original_error,
            },
        )


class FileEncodingError(AppError):
    """ファイルエンコーディングエラー"""

    def __init__(self, filename: str):
        super().__init__(
            message=f"ファイル '{filename}' はUTF-8でエンコードされていません",
            error_code="FILE_ENCODING_ERROR",
            details={"filename": filename},
        )


