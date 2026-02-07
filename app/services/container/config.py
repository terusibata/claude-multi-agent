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

    # SecurityOpt: no-new-privileges + カスタムseccomp（Phase 2）
    security_opt = ["no-new-privileges:true"]
    if settings.seccomp_profile_path:
        security_opt.append(f"seccomp={settings.seccomp_profile_path}")

    return {
        "Image": image,
        "Env": [
            "ANTHROPIC_BASE_URL=http+unix:///var/run/proxy.sock",
            "HTTP_PROXY=http+unix:///var/run/proxy.sock",
            "HTTPS_PROXY=http+unix:///var/run/proxy.sock",
            "NODE_USE_ENV_PROXY=1",
            "PIP_REQUIRE_VIRTUALENV=true",
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
            "UsernsMode": "host" if not settings.userns_remap_enabled else "",
            "Tmpfs": {
                "/tmp": "rw,noexec,nosuid,size=512M",
                "/var/tmp": "rw,noexec,nosuid,size=256M",
                "/run": "rw,noexec,nosuid,size=64M",
                "/home/appuser/.cache": "rw,noexec,nosuid,size=512M",
                "/home/appuser": "rw,noexec,nosuid,size=64M",
            },
            "Binds": [
                f"{settings.workspace_socket_base_path}/{container_id}/proxy.sock:/var/run/proxy.sock:ro",
                f"{settings.workspace_socket_base_path}/{container_id}/agent.sock:/var/run/agent.sock:rw",
            ],
            "StorageOpt": {"size": settings.container_disk_limit},
        },
    }


# Redis キープレフィックス
REDIS_KEY_CONTAINER = "workspace:container"  # workspace:container:{conversation_id}
REDIS_KEY_WARM_POOL = "workspace:warm_pool"  # List
REDIS_KEY_WARM_POOL_INFO = "workspace:warm_pool_info"  # workspace:warm_pool_info:{container_id}

# コンテナRedis TTL
CONTAINER_TTL_SECONDS = 3600  # 1時間
WARM_POOL_TTL_SECONDS = 1800  # 30分
