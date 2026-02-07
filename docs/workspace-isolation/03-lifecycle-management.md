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

### TTL カウンターの管理

```
Redis Key: workspace:container:{conversation_id}
Value: {
    "container_id": "abc123",
    "host": "10.0.1.5",
    "port": 8080,
    "created_at": "2026-02-07T10:00:00Z",
    "last_active_at": "2026-02-07T10:30:00Z",
    "status": "running"
}
TTL: 3600 (秒) ← 各リクエストで EXPIRE をリセット
```

## Warm Pool 設計

### 目的

コンテナ作成は数秒〜数十秒かかる。Warm Pool で事前にコンテナを準備し、コールドスタートを回避する。

### 設計パラメータ

| パラメータ | 値 | 説明 |
|-----------|-----|------|
| **最小プールサイズ** | 2 | 常時待機するコンテナ数 |
| **最大プールサイズ** | 10 | プールの上限 |
| **補充トリガー** | プール < 最小サイズ | コンテナ割り当て後に自動補充 |
| **プールコンテナ TTL** | 30分 | 未使用の場合の破棄時間 |

### 補充フロー

```
1. コンテナがプールから取得される
2. プールサイズが最小サイズを下回る
3. バックグラウンドタスクが新規コンテナを作成
4. 初期化完了後にプールに追加
```

### 実装イメージ

```python
class WarmPoolManager:
    def __init__(self, min_size: int = 2, max_size: int = 10):
        self.min_size = min_size
        self.max_size = max_size
        self.pool: asyncio.Queue[ContainerInfo] = asyncio.Queue(maxsize=max_size)

    async def acquire(self) -> ContainerInfo:
        """プールからコンテナを取得。空の場合は新規作成。"""
        try:
            container = self.pool.get_nowait()
            asyncio.create_task(self._replenish())
            return container
        except asyncio.QueueEmpty:
            return await self._create_new_container()

    async def _replenish(self):
        """プールが最小サイズを下回った場合に補充"""
        while self.pool.qsize() < self.min_size:
            container = await self._create_new_container()
            await self.pool.put(container)
```

## ガベージコレクション

### GC ループ

```python
async def gc_loop(interval: int = 60):
    """60秒ごとに実行"""
    while True:
        await asyncio.sleep(interval)

        containers = await list_workspace_containers()
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
1. コンテナを "draining" 状態にマーク（新規リクエスト拒否）
2. 実行中リクエストの完了を待機（最大30秒）
3. AI生成ファイルを S3 に同期
4. コンテナ停止 (docker stop)
5. コンテナ削除 (docker rm)
6. Redis からメタデータ削除
```
