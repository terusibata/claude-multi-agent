"""
コンテナ作成設定
Docker APIに渡すコンテナ設定を生成する
"""
from app.config import get_settings


def get_container_create_config(container_id: str) -> dict:
    """
    コンテナ作成用Docker API設定を生成

    Args:
        container_id: コンテナ識別子（ソケットパス生成に使用）

    Returns:
        aiodocker.Docker.containers.create() に渡す設定辞書
    """
    settings = get_settings()
    image = settings.container_image

    # SecurityOpt: no-new-privileges + カスタムseccomp（Phase 2）+ AppArmor（Phase 5）
    security_opt = ["no-new-privileges:true"]
    if settings.seccomp_profile_path:
        security_opt.append(f"seccomp={settings.seccomp_profile_path}")
    if settings.apparmor_profile_name:
        security_opt.append(f"apparmor={settings.apparmor_profile_name}")

    # BUG-06/07修正: ソケットディレクトリ単位でBind mount
    # Docker-in-Docker環境ではresolved_socket_host_pathを使用
    host_socket_dir = f"{settings.resolved_socket_host_path}/{container_id}"

    return {
        "Image": image,
        "Env": [
            # Bedrock設定: Proxy側でSigV4署名を注入するため、SDK側の認証はスキップ
            "CLAUDE_CODE_USE_BEDROCK=1",
            "CLAUDE_CODE_SKIP_BEDROCK_AUTH=1",
            f"AWS_REGION={settings.aws_region}",
            # Bedrock APIベースURL: socat TCP→UDS経由でホスト側Reverse Proxyに到達
            "ANTHROPIC_BEDROCK_BASE_URL=http://127.0.0.1:8080",
            # pip/npm/curl等の外部通信用Forward Proxy
            "HTTP_PROXY=http://127.0.0.1:8080",
            "HTTPS_PROXY=http://127.0.0.1:8080",
            "NO_PROXY=localhost,127.0.0.1",
            "PIP_REQUIRE_VIRTUALENV=true",
            # Node.js 20: global-agentでHTTP_PROXYをfetch()に適用
            "GLOBAL_AGENT_HTTP_PROXY=http://127.0.0.1:8080",
            "GLOBAL_AGENT_HTTPS_PROXY=http://127.0.0.1:8080",
            "GLOBAL_AGENT_NO_PROXY=localhost,127.0.0.1",
            "NODE_OPTIONS=--require global-agent/bootstrap",
        ],
        "User": "1000:1000",
        "Labels": {
            "workspace": "true",
            "workspace.container_id": container_id,
        },
        "HostConfig": {
            "NetworkMode": "none",
            "CpuPeriod": 100000,
            "CpuQuota": settings.container_cpu_quota,
            "Memory": settings.container_memory_limit,
            "MemorySwap": settings.container_memory_limit,  # swap無効
            "PidsLimit": settings.container_pids_limit,
            "CapDrop": ["ALL"],
            "CapAdd": ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE"],
            "SecurityOpt": security_opt,
            "Privileged": False,
            "ReadonlyRootfs": True,
            "IpcMode": "private",
            # userns-remap はデーモンレベルで有効化（コンテナ単位の指定不要）
            "Tmpfs": {
                "/tmp": "rw,noexec,nosuid,size=512M",
                "/var/tmp": "rw,noexec,nosuid,size=256M",
                "/run": "rw,noexec,nosuid,size=64M",
                "/home/appuser/.cache": "rw,noexec,nosuid,size=512M",
                "/home/appuser": "rw,noexec,nosuid,size=128M",
                # /workspace はエージェントの作業ディレクトリ（コード実行あり）
                # ReadonlyRootfs: True のため Tmpfs が必要。S3同期で永続化。
                "/workspace": "rw,nosuid,size=1G",
            },
            "Binds": [
                # ディレクトリ単位でBind mount（ソケット競合状態を回避）
                # ホスト: {host_socket_dir}/ → コンテナ: /var/run/ws/
                f"{host_socket_dir}:/var/run/ws:rw",
            ],
            "StorageOpt": {"size": settings.container_disk_limit},
        },
    }


# Redis キープレフィックス
REDIS_KEY_CONTAINER = "workspace:container"  # workspace:container:{conversation_id}
REDIS_KEY_CONTAINER_REVERSE = "workspace:container_reverse"  # workspace:container_reverse:{container_id} → conversation_id
REDIS_KEY_WARM_POOL = "workspace:warm_pool"  # List
REDIS_KEY_WARM_POOL_INFO = "workspace:warm_pool_info"  # workspace:warm_pool_info:{container_id}

# コンテナRedis TTL
CONTAINER_TTL_SECONDS = 3600  # 1時間
WARM_POOL_TTL_SECONDS = 1800  # 30分
