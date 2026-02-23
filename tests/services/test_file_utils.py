"""
file_utils ユニットテスト

write_file_to_container のチャンク分割・エラーハンドリングをテストする。
"""
import base64
from unittest.mock import AsyncMock, call

import pytest

from app.services.container.file_utils import _CHUNK_SIZE, write_file_to_container


def _make_lifecycle(exec_return=(0, "")):
    """テスト用のモック lifecycle を生成"""
    mock = AsyncMock()
    mock.exec_in_container = AsyncMock(return_value=exec_return)
    return mock


class TestWriteSmallFile:
    """小さいファイル（単一チャンク）の書き込みテスト"""

    @pytest.mark.asyncio
    async def test_writes_small_file_in_single_chunk(self):
        """_CHUNK_SIZE 未満のデータが単一チャンクで書き込まれること"""
        lifecycle = _make_lifecycle()
        data = b"hello world"

        await write_file_to_container(
            lifecycle, "ws-001", "/workspace/test.txt", data
        )

        calls = lifecycle.exec_in_container.call_args_list
        # mkdir -p, 1チャンク(>), decode の3回
        assert len(calls) == 3

        # mkdir -p /workspace
        assert calls[0] == call("ws-001", ["mkdir", "-p", "/workspace"])

        # チャンク書き込み: 最初のチャンクは ">" を使用
        chunk_cmd = calls[1][0][1]  # ["sh", "-c", "printf ... > ..."]
        assert chunk_cmd[0] == "sh"
        assert "> " in chunk_cmd[2]
        assert ">> " not in chunk_cmd[2]

    @pytest.mark.asyncio
    async def test_creates_parent_directory(self):
        """深いネストのパスで正しい親ディレクトリが作成されること"""
        lifecycle = _make_lifecycle()
        data = b"content"

        await write_file_to_container(
            lifecycle, "ws-001", "/workspace/deep/nested/dir/file.py", data
        )

        mkdir_call = lifecycle.exec_in_container.call_args_list[0]
        assert mkdir_call == call(
            "ws-001", ["mkdir", "-p", "/workspace/deep/nested/dir"]
        )


class TestWriteLargeFile:
    """大きいファイル（複数チャンク）の書き込みテスト"""

    @pytest.mark.asyncio
    async def test_writes_large_file_in_multiple_chunks(self):
        """_CHUNK_SIZE を超えるデータが複数チャンクに分割されること"""
        lifecycle = _make_lifecycle()

        # base64エンコード後に _CHUNK_SIZE を少し超えるデータを生成
        # base64 は 3バイト→4文字に変換するので、 _CHUNK_SIZE * 3/4 バイトで
        # ちょうど _CHUNK_SIZE 文字のbase64になる
        raw_size = (_CHUNK_SIZE * 3 // 4) + 100  # 少し超過
        data = b"A" * raw_size

        await write_file_to_container(
            lifecycle, "ws-001", "/workspace/large.bin", data
        )

        calls = lifecycle.exec_in_container.call_args_list
        # mkdir + 2チャンク + decode = 4回
        assert len(calls) == 4

        # 1つ目のチャンク: ">" を使用
        first_chunk_cmd = calls[1][0][1][2]
        assert "> " in first_chunk_cmd
        # ">>" が含まれないことを確認（">" のみ）
        assert ">> " not in first_chunk_cmd

        # 2つ目のチャンク: ">>" を使用
        second_chunk_cmd = calls[2][0][1][2]
        assert ">> " in second_chunk_cmd


class TestErrorHandling:
    """エラーハンドリングテスト"""

    @pytest.mark.asyncio
    async def test_raises_on_chunk_write_failure(self):
        """チャンク書き込み失敗時に RuntimeError が発生しクリーンアップされること"""
        lifecycle = _make_lifecycle()
        # mkdir成功、チャンク書き込み失敗
        lifecycle.exec_in_container.side_effect = [
            (0, ""),   # mkdir
            (1, ""),   # chunk write failure
            (0, ""),   # rm cleanup
        ]

        with pytest.raises(RuntimeError, match="chunk"):
            await write_file_to_container(
                lifecycle, "ws-001", "/workspace/fail.txt", b"data"
            )

        # クリーンアップ rm -f が呼ばれたことを確認
        cleanup_call = lifecycle.exec_in_container.call_args_list[-1]
        assert cleanup_call[0][1][0] == "rm"

    @pytest.mark.asyncio
    async def test_raises_on_decode_failure(self):
        """デコード失敗時に RuntimeError が発生すること"""
        lifecycle = _make_lifecycle()
        # mkdir成功、チャンク成功、デコード失敗
        lifecycle.exec_in_container.side_effect = [
            (0, ""),   # mkdir
            (0, ""),   # chunk write
            (1, ""),   # decode failure
            (0, ""),   # rm cleanup
        ]

        with pytest.raises(RuntimeError, match="decode"):
            await write_file_to_container(
                lifecycle, "ws-001", "/workspace/fail.txt", b"data"
            )

    @pytest.mark.asyncio
    async def test_cleans_up_tmp_on_decode_failure(self):
        """デコード失敗時に一時ファイルがクリーンアップされること"""
        lifecycle = _make_lifecycle()
        lifecycle.exec_in_container.side_effect = [
            (0, ""),   # mkdir
            (0, ""),   # chunk write
            (1, ""),   # decode failure
            (0, ""),   # rm cleanup
        ]

        with pytest.raises(RuntimeError):
            await write_file_to_container(
                lifecycle, "ws-001", "/workspace/fail.txt", b"data"
            )

        cleanup_call = lifecycle.exec_in_container.call_args_list[-1]
        assert cleanup_call == call(
            "ws-001", ["rm", "-f", "/tmp/_xfer_fail.txt"]
        )


class TestCustomPrefix:
    """カスタムプレフィックスのテスト"""

    @pytest.mark.asyncio
    async def test_uses_custom_tmp_prefix(self):
        """tmp_prefix パラメータがファイルパスに反映されること"""
        lifecycle = _make_lifecycle()
        data = b"content"

        await write_file_to_container(
            lifecycle, "ws-001", "/workspace/file.txt", data,
            tmp_prefix="_custom",
        )

        chunk_cmd = lifecycle.exec_in_container.call_args_list[1][0][1][2]
        assert "/tmp/_custom_file.txt" in chunk_cmd
