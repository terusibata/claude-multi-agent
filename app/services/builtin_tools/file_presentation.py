"""
ファイル提示ハンドラーとファイルパス解決ユーティリティ
"""
import json
import mimetypes
import os
import shutil
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# =============================================================================
# file_paths 正規化
# =============================================================================


def _normalize_file_paths(file_paths_input: Any) -> list[str]:
    """
    file_pathsを正規化してリスト形式にする

    LLMが文字列やJSON文字列で返す場合にも対応。

    Args:
        file_paths_input: ファイルパス入力（リスト、文字列、JSON文字列）

    Returns:
        正規化されたファイルパスのリスト
    """
    if isinstance(file_paths_input, list):
        return file_paths_input

    if not isinstance(file_paths_input, str):
        return []

    # JSON配列文字列の場合はパース
    if file_paths_input.startswith("["):
        try:
            parsed = json.loads(file_paths_input)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            logger.debug("JSONパースフォールバック", exc_info=True)

    return [file_paths_input]


# =============================================================================
# ファイルパス解決
# =============================================================================


def _resolve_file_path(file_path: str, workspace_cwd: str) -> str:
    """
    ファイルパスをフルパスに解決

    Args:
        file_path: 入力パス（相対/絶対）
        workspace_cwd: ワークスペースのカレントディレクトリ

    Returns:
        解決されたフルパス
    """
    if not os.path.isabs(file_path) and workspace_cwd:
        return os.path.join(workspace_cwd, file_path)
    return file_path


def _compute_relative_path(path: Path, file_path: str, workspace_cwd: str) -> str:
    """
    ファイルのワークスペース相対パスを計算

    ワークスペース外の場合はファイル名を返す。

    Args:
        path: Pathオブジェクト
        file_path: 元の入力パス
        workspace_cwd: ワークスペースのカレントディレクトリ

    Returns:
        相対パス
    """
    if not workspace_cwd:
        return file_path

    abs_path = str(path.absolute())
    abs_cwd = str(Path(workspace_cwd).absolute())

    if not abs_path.startswith(abs_cwd):
        # ワークスペース外
        return file_path

    try:
        return str(path.absolute().relative_to(Path(workspace_cwd).absolute()))
    except ValueError:
        return path.name


# =============================================================================
# ワークスペース外ファイルのコピー処理
# =============================================================================


def _copy_file_to_workspace(path: Path, workspace_cwd: str) -> tuple[Path, str] | None:
    """
    ワークスペース外のファイルをワークスペース内にコピー

    同名ファイルが存在する場合はユニークな名前を生成。

    Args:
        path: コピー元のパス
        workspace_cwd: ワークスペースのカレントディレクトリ

    Returns:
        (コピー先パス, 相対パス) のタプル。失敗時はNone。
    """
    dest_path = Path(workspace_cwd) / path.name

    # 同名ファイルが存在する場合はユニークな名前を生成
    counter = 1
    original_name = dest_path.stem
    suffix = dest_path.suffix
    while dest_path.exists():
        dest_path = Path(workspace_cwd) / f"{original_name}_{counter}{suffix}"
        counter += 1

    try:
        shutil.copy2(str(path), str(dest_path))
        logger.info(
            "ファイル提示: ワークスペース外ファイルをコピー",
            source=str(path),
            destination=str(dest_path),
        )
        return dest_path, dest_path.name
    except Exception as e:
        logger.error(
            "ファイル提示: ファイルコピー失敗",
            source=str(path),
            error=str(e),
        )
        return None


# =============================================================================
# S3アップロード処理
# =============================================================================


async def _upload_to_s3(
    workspace_service,
    tenant_id: str,
    conversation_id: str,
    relative_path: str,
    path: Path,
    mime_type: str | None,
) -> None:
    """
    ファイルをS3にアップロードしてDBに登録

    Args:
        workspace_service: WorkspaceServiceインスタンス
        tenant_id: テナントID
        conversation_id: 会話ID
        relative_path: S3上の相対パス
        path: ローカルファイルのパス
        mime_type: MIMEタイプ
    """
    file_content = path.read_bytes()
    content_type = mime_type or "application/octet-stream"

    await workspace_service.s3.upload(
        tenant_id, conversation_id, relative_path, file_content, content_type,
    )
    await workspace_service.register_ai_file(
        tenant_id, conversation_id, relative_path, is_presented=True,
    )

    logger.info(
        "ファイル提示: S3に即時アップロード完了",
        file_path=relative_path,
        size=len(file_content),
    )


# =============================================================================
# 結果メッセージの構築
# =============================================================================


def _build_result_message(
    existing_files: list[dict],
    missing_files: list[dict],
    description: str,
) -> str:
    """
    ファイル提示の結果メッセージを構築

    Args:
        existing_files: 存在するファイル情報のリスト
        missing_files: 見つからなかったファイル情報のリスト
        description: ファイルの説明

    Returns:
        結果メッセージ文字列
    """
    parts = []

    if existing_files:
        parts.append(f"ファイルを提示しました: {description}\n")
        parts.append("【提示されたファイル】")
        for f in existing_files:
            parts.append(f"• {f['name']} ({f['size']} bytes)")
            parts.append(f"  ダウンロードパス: {f['relative_path']}")
    else:
        parts.append(f"提示するファイルが見つかりませんでした: {description}")

    if missing_files:
        parts.append("")
        parts.append("【見つからなかったファイル】")
        for f in missing_files:
            line = f"• {f['relative_path']}"
            if f.get("error"):
                line += f" ({f['error']})"
            parts.append(line)

    return "\n".join(parts)


# =============================================================================
# present_files ハンドラー
# =============================================================================


def create_present_files_handler(
    workspace_cwd: str = "",
    workspace_service=None,
    tenant_id: str = "",
    conversation_id: str = "",
):
    """
    present_filesツールのハンドラーを作成

    Args:
        workspace_cwd: ワークスペースのカレントディレクトリ
        workspace_service: WorkspaceServiceインスタンス（即時S3アップロード用）
        tenant_id: テナントID
        conversation_id: 会話ID

    Returns:
        ツールハンドラー関数
    """
    async def present_files_handler(args: dict[str, Any]) -> dict[str, Any]:
        """present_filesツールの実行ハンドラー"""
        file_paths = _normalize_file_paths(args.get("file_paths", []))
        description = args.get("description", "")

        files_info = []
        for file_path in file_paths:
            full_path = _resolve_file_path(file_path, workspace_cwd)
            path = Path(full_path)

            if not (path.exists() and path.is_file()):
                files_info.append({
                    "path": full_path,
                    "relative_path": file_path,
                    "name": os.path.basename(file_path),
                    "exists": False,
                })
                logger.warning("ファイル提示: ファイルが存在しない", file_path=file_path, full_path=full_path)
                continue

            mime_type, _ = mimetypes.guess_type(str(path))
            relative_path = file_path

            # ワークスペース外のファイルはワークスペース内にコピー
            is_outside_workspace = (
                workspace_cwd
                and not str(path.absolute()).startswith(str(Path(workspace_cwd).absolute()))
            )

            if is_outside_workspace:
                copy_result = _copy_file_to_workspace(path, workspace_cwd)
                if copy_result is None:
                    files_info.append({
                        "path": full_path,
                        "relative_path": file_path,
                        "name": path.name,
                        "exists": False,
                        "error": "ワークスペースへのコピーに失敗",
                    })
                    continue
                path, relative_path = copy_result
            else:
                relative_path = _compute_relative_path(path, file_path, workspace_cwd)

            # S3にアップロード（workspace_serviceが利用可能な場合）
            if workspace_service and tenant_id and conversation_id:
                try:
                    await _upload_to_s3(
                        workspace_service, tenant_id, conversation_id,
                        relative_path, path, mime_type,
                    )
                except Exception as upload_error:
                    logger.error(
                        "ファイル提示: S3アップロード失敗",
                        file_path=relative_path,
                        error=str(upload_error),
                    )
                    # アップロード失敗してもファイル情報は追加する

            files_info.append({
                "path": str(path.absolute()),
                "relative_path": relative_path,
                "name": path.name,
                "size": path.stat().st_size,
                "mime_type": mime_type or "application/octet-stream",
                "exists": True,
            })

        existing_files = [f for f in files_info if f.get("exists")]
        missing_files = [f for f in files_info if not f.get("exists")]

        result_text = _build_result_message(existing_files, missing_files, description)

        return {
            "content": [{"type": "text", "text": result_text}],
            "_metadata": {
                "files": files_info,
                "presented_files": existing_files,
                "description": description,
            },
        }

    return present_files_handler
