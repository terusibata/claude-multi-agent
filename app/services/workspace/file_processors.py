"""
ファイルタイプ分類ユーティリティ

ファイルの拡張子/MIMEタイプに基づいて処理方法を決定する
"""

from enum import Enum
from pathlib import Path
from typing import Optional

from app.config import get_settings


class FileCategory(str, Enum):
    """ファイルカテゴリ"""

    IMAGE = "image"
    PDF = "pdf"
    OFFICE = "office"
    TEXT = "text"


class FileProcessingMethod(str, Enum):
    """ファイル処理方法"""

    MESSAGES_API_IMAGE = "messages_api_image"  # Messages API content block（画像）
    MESSAGES_API_DOCUMENT = "messages_api_document"  # Messages API document block（PDF）
    EXTRACT_TEXT = "extract_text"  # Pythonライブラリでテキスト抽出（Office）
    TEXT_READ = "text_read"  # 既存Readツール（テキスト系）


# 拡張子とカテゴリのマッピング
EXTENSION_TO_CATEGORY: dict[str, FileCategory] = {
    # 画像
    ".jpg": FileCategory.IMAGE,
    ".jpeg": FileCategory.IMAGE,
    ".png": FileCategory.IMAGE,
    ".gif": FileCategory.IMAGE,
    ".webp": FileCategory.IMAGE,
    # PDF
    ".pdf": FileCategory.PDF,
    # Office
    ".xlsx": FileCategory.OFFICE,
    ".xls": FileCategory.OFFICE,
    ".docx": FileCategory.OFFICE,
    ".doc": FileCategory.OFFICE,
    ".pptx": FileCategory.OFFICE,
    ".ppt": FileCategory.OFFICE,
    # テキスト系（明示的に定義、それ以外もTEXTにフォールバック）
    ".txt": FileCategory.TEXT,
    ".csv": FileCategory.TEXT,
    ".json": FileCategory.TEXT,
    ".md": FileCategory.TEXT,
    ".yaml": FileCategory.TEXT,
    ".yml": FileCategory.TEXT,
    ".xml": FileCategory.TEXT,
    ".html": FileCategory.TEXT,
    ".htm": FileCategory.TEXT,
    ".py": FileCategory.TEXT,
    ".js": FileCategory.TEXT,
    ".ts": FileCategory.TEXT,
    ".tsx": FileCategory.TEXT,
    ".jsx": FileCategory.TEXT,
    ".java": FileCategory.TEXT,
    ".go": FileCategory.TEXT,
    ".rs": FileCategory.TEXT,
    ".c": FileCategory.TEXT,
    ".cpp": FileCategory.TEXT,
    ".h": FileCategory.TEXT,
    ".hpp": FileCategory.TEXT,
    ".sh": FileCategory.TEXT,
    ".sql": FileCategory.TEXT,
    ".css": FileCategory.TEXT,
    ".scss": FileCategory.TEXT,
    ".less": FileCategory.TEXT,
}

# MIMEタイプとカテゴリのマッピング
MIME_TYPE_TO_CATEGORY: dict[str, FileCategory] = {
    # 画像
    "image/jpeg": FileCategory.IMAGE,
    "image/png": FileCategory.IMAGE,
    "image/gif": FileCategory.IMAGE,
    "image/webp": FileCategory.IMAGE,
    # PDF
    "application/pdf": FileCategory.PDF,
    # Office
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": FileCategory.OFFICE,
    "application/vnd.ms-excel": FileCategory.OFFICE,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": FileCategory.OFFICE,
    "application/msword": FileCategory.OFFICE,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": FileCategory.OFFICE,
    "application/vnd.ms-powerpoint": FileCategory.OFFICE,
    # テキスト系
    "text/plain": FileCategory.TEXT,
    "text/csv": FileCategory.TEXT,
    "text/markdown": FileCategory.TEXT,
    "text/html": FileCategory.TEXT,
    "text/xml": FileCategory.TEXT,
    "application/json": FileCategory.TEXT,
    "application/xml": FileCategory.TEXT,
    "application/x-yaml": FileCategory.TEXT,
}

# カテゴリと処理方法のマッピング
CATEGORY_TO_PROCESSING_METHOD: dict[FileCategory, FileProcessingMethod] = {
    FileCategory.IMAGE: FileProcessingMethod.MESSAGES_API_IMAGE,
    FileCategory.PDF: FileProcessingMethod.MESSAGES_API_DOCUMENT,
    FileCategory.OFFICE: FileProcessingMethod.EXTRACT_TEXT,
    FileCategory.TEXT: FileProcessingMethod.TEXT_READ,
}


class FileTypeClassifier:
    """ファイルタイプ分類器"""

    @classmethod
    def get_category(cls, filename: str, mime_type: Optional[str] = None) -> FileCategory:
        """
        ファイルのカテゴリを取得

        Args:
            filename: ファイル名
            mime_type: MIMEタイプ（オプション）

        Returns:
            FileCategory
        """
        ext = Path(filename).suffix.lower()

        # 拡張子で判定
        if ext in EXTENSION_TO_CATEGORY:
            return EXTENSION_TO_CATEGORY[ext]

        # MIMEタイプで判定
        if mime_type:
            # 完全一致
            if mime_type in MIME_TYPE_TO_CATEGORY:
                return MIME_TYPE_TO_CATEGORY[mime_type]
            # text/* はテキストとして扱う
            if mime_type.startswith("text/"):
                return FileCategory.TEXT

        # デフォルトはテキスト
        return FileCategory.TEXT

    @classmethod
    def get_processing_method(
        cls, filename: str, mime_type: Optional[str] = None
    ) -> FileProcessingMethod:
        """
        ファイルの処理方法を取得

        Args:
            filename: ファイル名
            mime_type: MIMEタイプ（オプション）

        Returns:
            FileProcessingMethod
        """
        category = cls.get_category(filename, mime_type)
        return CATEGORY_TO_PROCESSING_METHOD[category]

    @classmethod
    def get_max_file_size(cls, filename: str, mime_type: Optional[str] = None) -> int:
        """
        ファイルの最大サイズを取得

        Args:
            filename: ファイル名
            mime_type: MIMEタイプ（オプション）

        Returns:
            最大サイズ（バイト）
        """
        settings = get_settings()
        category = cls.get_category(filename, mime_type)

        size_map = {
            FileCategory.IMAGE: settings.max_image_file_size,
            FileCategory.PDF: settings.max_pdf_file_size,
            FileCategory.OFFICE: settings.max_office_file_size,
            FileCategory.TEXT: settings.max_text_file_size,
        }

        return size_map.get(category, settings.max_upload_file_size)

    @classmethod
    def is_supported(cls, filename: str, mime_type: Optional[str] = None) -> bool:
        """
        サポートされているファイルタイプかどうか

        Args:
            filename: ファイル名
            mime_type: MIMEタイプ（オプション）

        Returns:
            サポートされているかどうか
        """
        ext = Path(filename).suffix.lower()

        # 拡張子がマッピングに存在するか
        if ext in EXTENSION_TO_CATEGORY:
            return True

        # MIMEタイプがマッピングに存在するか
        if mime_type and (mime_type in MIME_TYPE_TO_CATEGORY or mime_type.startswith("text/")):
            return True

        # テキスト系は広くサポート
        return True

    @classmethod
    def get_mime_type_for_category(cls, category: FileCategory) -> list[str]:
        """
        カテゴリに対応するMIMEタイプのリストを取得

        Args:
            category: ファイルカテゴリ

        Returns:
            MIMEタイプのリスト
        """
        return [mime for mime, cat in MIME_TYPE_TO_CATEGORY.items() if cat == category]

    @classmethod
    def get_extensions_for_category(cls, category: FileCategory) -> list[str]:
        """
        カテゴリに対応する拡張子のリストを取得

        Args:
            category: ファイルカテゴリ

        Returns:
            拡張子のリスト
        """
        return [ext for ext, cat in EXTENSION_TO_CATEGORY.items() if cat == category]
