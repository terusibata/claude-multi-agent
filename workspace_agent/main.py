"""
ワークスペースエージェント メインアプリケーション
コンテナ内でUnix Domain Socket上のFastAPIとして動作する
"""
import asyncio
import json
import os
import socket
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from workspace_agent.models import ExecuteRequest, HealthResponse
from workspace_agent.sdk_client import execute_streaming

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger(__name__)

AGENT_SOCKET = "/var/run/ws/agent.sock"

app = FastAPI(title="Workspace Agent", docs_url=None, redoc_url=None)


@app.post("/execute")
async def execute(request: ExecuteRequest) -> StreamingResponse:
    """エージェント実行エンドポイント（SSEストリーミング）"""
    return StreamingResponse(
        execute_streaming(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
async def health() -> HealthResponse:
    """ヘルスチェック"""
    return HealthResponse(status="ok")


@app.get("/diagnostics")
async def diagnostics() -> dict:
    """コンテナ内の環境診断（CLI、socat、ファイルシステム状態を検査）"""
    results = {}
    cli_path = None

    # 1. CLI バイナリ
    try:
        import claude_agent_sdk as sdk
        pkg_dir = Path(sdk.__file__).parent
        bundled = pkg_dir / "_bundled" / "claude"
        cli_path = str(bundled)
        results["cli_binary"] = {
            "path": cli_path,
            "exists": bundled.exists(),
            "size": bundled.stat().st_size if bundled.exists() else 0,
            "executable": os.access(cli_path, os.X_OK) if bundled.exists() else False,
        }
    except Exception as e:
        results["cli_binary"] = {"error": str(e)}

    # 2. CLI バージョン
    if cli_path:
        try:
            import subprocess
            r = subprocess.run(
                [cli_path, "-v"],
                capture_output=True, text=True, timeout=5,
                env={**os.environ, "NODE_OPTIONS": ""},
            )
            results["cli_version"] = {
                "stdout": r.stdout.strip(),
                "stderr": r.stderr.strip()[:200],
                "returncode": r.returncode,
            }
        except Exception as e:
            results["cli_version"] = {"error": str(e)}
    else:
        results["cli_version"] = {"error": "CLI path not resolved"}

    # 3. socat ブリッジ疎通
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(3)
            s.connect(("127.0.0.1", 8080))
            results["socat_bridge"] = {"status": "connected"}
    except Exception as e:
        results["socat_bridge"] = {"status": "failed", "error": str(e)}

    # 4. proxy.sock の存在確認
    proxy_sock = Path("/var/run/ws/proxy.sock")
    results["proxy_socket"] = {
        "path": str(proxy_sock),
        "exists": proxy_sock.exists(),
    }

    # 5. 必須ディレクトリのチェック
    check_dirs = {
        "home": str(Path.home()),
        "claude_config": str(Path.home() / ".claude"),
        "tmp": "/tmp",
        "workspace": "/workspace",
        "workspace_tmp": "/workspace/.tmp",
        "workspace_claude": "/workspace/.claude",
    }
    dir_status = {}
    for name, path in check_dirs.items():
        p = Path(path)
        dir_status[name] = {
            "path": path,
            "exists": p.exists(),
            "writable": os.access(path, os.W_OK) if p.exists() else False,
        }
    results["directories"] = dir_status

    # 6. 環境変数（Bedrock関連）
    env_keys = [
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
        "ANTHROPIC_BEDROCK_BASE_URL", "AWS_REGION", "HOME",
        "CLAUDE_CONFIG_DIR", "NODE_OPTIONS", "TMPDIR",
    ]
    results["environment"] = {k: os.environ.get(k, "(not set)") for k in env_keys}

    # 7. CLI ストリーミングモード初期化テスト（10秒タイムアウト）
    if cli_path:
        results["streaming_init_test"] = await _test_streaming_init(cli_path)
    else:
        results["streaming_init_test"] = {"status": "skipped", "reason": "CLI path not resolved"}

    return results


async def _test_streaming_init(cli_path: str) -> dict:
    """CLI をストリーミングモードで起動し、初期化ハンドシェイクをテストする"""
    try:
        env = {
            **os.environ,
            "NODE_OPTIONS": "",
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "CLAUDE_CODE_SKIP_BEDROCK_AUTH": "1",
            "ANTHROPIC_BEDROCK_BASE_URL": os.environ.get(
                "ANTHROPIC_BEDROCK_BASE_URL", "http://127.0.0.1:8080"
            ),
        }

        cmd = [
            cli_path,
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd="/workspace",
        )

        # initialize リクエスト送信
        init_msg = json.dumps({
            "type": "control_request",
            "request_id": "diag_init_1",
            "request": {"subtype": "initialize"},
        }) + "\n"

        proc.stdin.write(init_msg.encode())
        await proc.stdin.drain()

        # stdout からレスポンス待ち（10秒タイムアウト）
        stdout_lines = []
        stderr_lines = []

        async def read_stderr():
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                stderr_lines.append(line.decode(errors="replace").rstrip())

        stderr_task = asyncio.create_task(read_stderr())

        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
            stdout_lines.append(line.decode(errors="replace").rstrip())
            status = "responded"
        except asyncio.TimeoutError:
            status = "timeout_10s"

        # プロセス終了
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()

        stderr_task.cancel()
        try:
            await stderr_task
        except asyncio.CancelledError:
            pass

        return {
            "status": status,
            "stdout": stdout_lines[:5],
            "stderr": stderr_lines[:30],
            "returncode": proc.returncode,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    logger.info("ワークスペースエージェント起動", socket=AGENT_SOCKET)
    uvicorn.run(app, uds=AGENT_SOCKET, log_level="info")
