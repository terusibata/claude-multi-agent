"""
ワークスペース関連スキーマ
会話専用ワークスペースのファイル管理
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ConversationFileInfo(BaseModel):
    """会話ファイル情報"""

    file_id: str = Field(..., description="ファイルID")
    file_path: str = Field(..., description="ワークスペース内のファイルパス")
    original_name: str = Field(..., description="元のファイル名")
    file_size: int = Field(..., description="ファイルサイズ（バイト）")
    mime_type: Optional[str] = Field(None, description="MIMEタイプ")
    version: int = Field(..., description="バージョン番号")
    source: str = Field(..., description="ソース: user_upload / ai_created / ai_modified")
    is_presented: bool = Field(False, description="Presented fileフラグ")
    checksum: Optional[str] = Field(None, description="SHA256チェックサム")
    description: Optional[str] = Field(None, description="ファイル説明")
    created_at: datetime = Field(..., description="作成日時")
    updated_at: datetime = Field(..., description="更新日時")


class ConversationFileCreate(BaseModel):
    """会話ファイル作成リクエスト"""

    file_path: str = Field(..., description="保存先のファイルパス（ワークスペース内）")
    description: Optional[str] = Field(None, description="ファイル説明")


class WorkspaceInfo(BaseModel):
    """ワークスペース情報"""

    conversation_id: str = Field(..., description="会話ID")
    workspace_enabled: bool = Field(..., description="ワークスペース有効フラグ")
    workspace_path: Optional[str] = Field(None, description="ワークスペースパス")
    workspace_created_at: Optional[datetime] = Field(None, description="作成日時")
    file_count: int = Field(0, description="ファイル数")
    total_size: int = Field(0, description="合計サイズ（バイト）")


class WorkspaceFileList(BaseModel):
    """ワークスペースファイル一覧レスポンス"""

    conversation_id: str = Field(..., description="会話ID")
    files: list[ConversationFileInfo] = Field(default_factory=list, description="ファイル一覧")
    total_count: int = Field(0, description="合計ファイル数")
    total_size: int = Field(0, description="合計サイズ（バイト）")


class PresentedFileList(BaseModel):
    """Presentedファイル一覧レスポンス"""

    conversation_id: str = Field(..., description="会話ID")
    files: list[ConversationFileInfo] = Field(default_factory=list, description="Presentedファイル一覧")


class FileVersionHistory(BaseModel):
    """ファイルバージョン履歴"""

    file_path: str = Field(..., description="ファイルパス")
    versions: list[ConversationFileInfo] = Field(default_factory=list, description="バージョン一覧")


class UploadResponse(BaseModel):
    """ファイルアップロードレスポンス"""

    success: bool = Field(..., description="成功フラグ")
    file: ConversationFileInfo = Field(..., description="アップロードされたファイル情報")
    message: str = Field(..., description="メッセージ")


class MultiUploadResponse(BaseModel):
    """複数ファイルアップロードレスポンス"""

    success: bool = Field(..., description="成功フラグ")
    uploaded_files: list[ConversationFileInfo] = Field(default_factory=list, description="アップロードされたファイル一覧")
    failed_files: list[dict] = Field(default_factory=list, description="失敗したファイル一覧")
    message: str = Field(..., description="メッセージ")


class PresentFileRequest(BaseModel):
    """ファイルPresent設定リクエスト"""

    file_path: str = Field(..., description="ファイルパス")
    description: Optional[str] = Field(None, description="ファイル説明（更新する場合）")


class CleanupRequest(BaseModel):
    """ワークスペースクリーンアップリクエスト"""

    older_than_days: int = Field(
        30,
        description="指定日数より古いワークスペースをクリーンアップ",
        ge=1,
        le=365,
    )
    dry_run: bool = Field(
        True,
        description="ドライラン（削除せずにリストのみ返す）",
    )


class CleanupResponse(BaseModel):
    """ワークスペースクリーンアップレスポンス"""

    success: bool = Field(..., description="成功フラグ")
    conversations_cleaned: int = Field(0, description="クリーンアップされた会話数")
    total_size_freed: int = Field(0, description="解放されたサイズ（バイト）")
    conversations: list[str] = Field(default_factory=list, description="クリーンアップされた会話ID一覧")
    dry_run: bool = Field(True, description="ドライランフラグ")


class WorkspaceContextForAI(BaseModel):
    """
    AIに提供するワークスペースコンテキスト
    system_promptに含めるファイルリスト情報
    """

    workspace_path: str = Field(..., description="ワークスペースパス")
    files: list[dict] = Field(default_factory=list, description="ファイル一覧（パス、サイズ、説明）")
    instructions: str = Field(
        ...,
        description="AIへの指示（ファイル操作のガイドライン）",
    )
