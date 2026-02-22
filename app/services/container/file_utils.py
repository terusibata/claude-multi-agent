"""
コンテナへのファイル転送ユーティリティ

exec + base64 チャンク方式でコンテナにファイルを書き込む共通関数を提供する。
Docker / ECS 両モードで使用可能。
"""
import base64

import structlog

from app.services.container.base import ContainerManagerBase

logger = structlog.get_logger(__name__)

# shell 引数制限を回避するための base64 チャンクサイズ
_CHUNK_SIZE = 60000


async def write_file_to_container(
    lifecycle: ContainerManagerBase,
    container_id: str,
    dest_path: str,
    data: bytes,
    tmp_prefix: str = "_xfer",
) -> None:
    """exec + base64 チャンク転送でコンテナにファイルを書き込む

    Docker の put_archive API は tmpfs マウント上で失敗する場合がある
    （ReadonlyRootfs + tmpfs 構成、Docker-in-Docker、userns-remap 等）。
    exec はコンテナ内プロセスとして実行されるため tmpfs も正しく書き込める。

    Args:
        lifecycle: コンテナマネージャー
        container_id: 対象コンテナID
        dest_path: コンテナ内の書き込み先パス（絶対パス）
        data: 書き込むバイナリデータ
        tmp_prefix: 一時ファイルのプレフィックス
    """
    # 親ディレクトリを確保
    parent_dir = "/".join(dest_path.split("/")[:-1])
    await lifecycle.exec_in_container(
        container_id, ["mkdir", "-p", parent_dir]
    )

    encoded = base64.b64encode(data).decode("ascii")

    filename = dest_path.split("/")[-1]
    tmp_path = f"/tmp/{tmp_prefix}_{filename}"

    for i in range(0, len(encoded), _CHUNK_SIZE):
        chunk = encoded[i : i + _CHUNK_SIZE]
        op = ">>" if i > 0 else ">"
        exit_code, _ = await lifecycle.exec_in_container(
            container_id,
            ["sh", "-c", f"printf '%s' '{chunk}' {op} '{tmp_path}'"],
        )
        if exit_code != 0:
            await lifecycle.exec_in_container(
                container_id, ["rm", "-f", tmp_path]
            )
            raise RuntimeError(
                f"コンテナへのファイル書き込み失敗(chunk): {dest_path}"
            )

    # base64 デコード → 最終ファイルに書き込み → 一時ファイル削除
    exit_code, _ = await lifecycle.exec_in_container(
        container_id,
        [
            "sh",
            "-c",
            f"base64 -d < '{tmp_path}' > '{dest_path}' && rm -f '{tmp_path}'",
        ],
    )
    if exit_code != 0:
        await lifecycle.exec_in_container(
            container_id, ["rm", "-f", tmp_path]
        )
        raise RuntimeError(
            f"コンテナへのファイル書き込み失敗(decode): {dest_path}"
        )
