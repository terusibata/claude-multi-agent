# 03 - ライフサイクル管理

## コンテナ状態遷移

```
                    ┌──────────┐
           ┌───────│ Warm Pool │◄──── pre-warm
           │       └──────────┘
           │ assign
           ▼
      ┌─────────┐     execute     ┌──────────┐
      │  Ready  │ ──────────────► │ Running  │
      └─────────┘                 └────┬─────┘
                                       │
                                       │ idle timeout
                                       ▼
                                  ┌──────────┐    TTL expired    ┌───────────┐
                                  │   Idle   │ ────────────────► │ Destroying│
                                  └──────────┘                   └─────┬─────┘
                                       │                               │
                                       │ new request                   │ cleanup
                                       ▼                               ▼
                                  ┌──────────┐                   ┌───────────┐
                                  │ Running  │                   │ Destroyed │
                                  └──────────┘                   └───────────┘
```

## TTL 設計

### タイムアウト設定

| パラメータ | 値 | 説明 |
|-----------|-----|------|
| **非アクティブ TTL** | 60分 | 最後のリクエストからの経過時間 |
| **絶対 TTL** | 8時間 | コンテナ作成からの最大存続時間 |
| **実行タイムアウト** | 10分 | 単一リクエストの最大実行時間 |
| **ヘルスチェック間隔** | 30秒 | コンテナ死活監視 |
| **グレースピリオド** | 30秒 | 破棄前の猶予時間（実行中リクエスト完了待ち） |

### TTL カウンターの管理（Redis）

```
Redis Key: workspace:container:{conversation_id}
Value (Hash):
  container_id: "abc123"
  host: "10.0.1.5"
  port: 8080
  created_at: "2026-02-07T10:00:00Z"
  last_active_at: "2026-02-07T10:30:00Z"
  status: "running" | "idle" | "draining"
TTL: 3600 (秒) ← 各リクエスト完了時に EXPIRE をリセット
```

## Warm Pool 設計

### 目的

コンテナ作成 + イメージ起動は 3〜10 秒かかる。Warm Pool で事前に準備し、初回レスポンスを高速化する。

### 設計パラメータ

| パラメータ | 値 | 説明 |
|-----------|-----|------|
| **最小プールサイズ** | 2 | 常時待機するコンテナ数 |
| **最大プールサイズ** | 10 | プールの上限 |
| **補充トリガー** | プール < 最小サイズ | コンテナ割り当て後に自動補充 |
| **プールコンテナ TTL** | 30分 | 未使用の場合の破棄時間 |

### Redis ベースの Warm Pool（マルチインスタンス対応）

```
Redis Key: workspace:warm_pool (List)
Value: ["container_id_1", "container_id_2", ...]

Redis Key: workspace:warm_pool:{container_id} (Hash)
Value:
  host: "10.0.1.5"
  port: 8080
  created_at: "2026-02-07T10:00:00Z"
TTL: 1800 (30分)
```

```python
class WarmPoolManager:
    """Redis ベースの Warm Pool（複数 Backend インスタンスで共有）"""

    def __init__(self, docker: aiodocker.Docker, redis: Redis,
                 min_size: int = 2, max_size: int = 10):
        self.docker = docker
        self.redis = redis
        self.min_size = min_size
        self.max_size = max_size

    async def acquire(self) -> ContainerInfo:
        """プールからコンテナを取得。空の場合は新規作成。"""
        # LPOP でアトミックに取得（複数インスタンスで競合しない）
        container_id = await self.redis.lpop("workspace:warm_pool")
        if container_id:
            info = await self._get_container_info(container_id)
            if info and await self._is_healthy(info):
                asyncio.create_task(self._replenish())
                return info
        # プール空 or 取得コンテナが不健全 → 新規作成
        return await self._create_new_container()

    async def _replenish(self):
        """プールが最小サイズを下回った場合に補充"""
        pool_size = await self.redis.llen("workspace:warm_pool")
        while pool_size < self.min_size:
            container = await self._create_new_container()
            await self.redis.rpush("workspace:warm_pool", container.id)
            await self.redis.hset(f"workspace:warm_pool:{container.id}", mapping={...})
            await self.redis.expire(f"workspace:warm_pool:{container.id}", 1800)
            pool_size += 1

    async def _create_new_container(self) -> ContainerInfo:
        """新規コンテナを作成して起動"""
        container = await self.docker.containers.create_or_replace(
            config={
                "Image": "workspace-base:latest",
                "HostConfig": {
                    **RESOURCE_LIMITS,
                    **SECURITY_CONFIG,
                },
            }
        )
        await container.start()
        await self._wait_for_healthy(container)
        return ContainerInfo(...)
```

## ガベージコレクション

### GC ループ

```python
async def gc_loop(interval: int = 60):
    """60秒ごとに実行。全 Backend インスタンスで実行されるが、
       Redis の状態に基づいて冪等に動作する。"""
    while True:
        await asyncio.sleep(interval)
        containers = await docker.containers.list(
            filters={"label": ["workspace=true"]}
        )
        for container in containers:
            if should_destroy(container):
                await graceful_destroy(container)

def should_destroy(container: ContainerInfo) -> bool:
    now = datetime.utcnow()
    # 非アクティブ TTL 超過
    if (now - container.last_active_at) > timedelta(hours=1):
        return True
    # 絶対 TTL 超過
    if (now - container.created_at) > timedelta(hours=8):
        return True
    # ヘルスチェック失敗
    if container.health_status == "unhealthy":
        return True
    return False
```

### グレースフルシャットダウン

```
1. Redis でステータスを "draining" に更新（新規リクエスト拒否）
2. 実行中リクエストの完了を待機（最大30秒）
3. AI生成ファイルを S3 に同期
4. コンテナ停止 (docker stop --time 30)
5. コンテナ削除 (docker rm)
6. Redis からメタデータ削除
```
