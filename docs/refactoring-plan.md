# バックエンド リファクタリングプラン

> **前提**: このバックエンドは外部公開せず、フロントエンドからプライベート通信でアクセスされる構成。
> **コード検証日**: 2026-02-10

---

## 検証サマリー

提示された7項目を全てコードベースで検証した結果、**一部の前提が不正確**であった。
以下に各項目の検証結果と、調整後の優先度・実施判断を示す。

---

## 1. API層の404エラーハンドリング重複

| 項目 | 値 |
|------|------|
| 元の優先度 | HIGH |
| **調整後優先度** | **MEDIUM** |
| **実施判断** | **実施する** |

### 検証結果

重複は**確認済み**。以下の全ファイルで `if not X: raise HTTPException(status_code=404, ...)` パターンが繰り返されている:

- `app/api/conversations/router.py` — 4箇所 (75行, 146行, 169行, 191行)
- `app/api/simple_chats/router.py` — 3箇所 (74行, 112行, 134行)
- `app/api/tenants.py` — 3箇所 (77行, 100行, 131行)
- `app/api/models.py` — 4箇所 (41行, 93行, 116行, 148行)
- `app/api/skills.py` — 4箇所 (77行, 193行, 221行, 264行)
- `app/api/mcp_servers.py` — 3箇所 (56行, 131行, 154行)

`app/utils/error_handler.py` に `get_or_404()` が実装済みだが未活用。
一方、`app/api/dependencies.py` では `get_tenant_or_404` が `Depends` ベースで実装済みであり、このパターンが既に機能している。

### 優先度をMEDIUMに下げた理由

- 各重複は3行程度の自己完結したパターンであり、バグの温床になりにくい
- 現状のコードは読みやすく、各エンドポイントで何が起きるか明確
- ただし21箇所の重複はメンテナンスコストに影響するため、統一は価値がある

### 実施方針

**方式A（推奨）: Dependsベースの共通パターンに統一**

`app/api/dependencies.py` に既存の `get_tenant_or_404` と同様のパターンで、主要リソースの取得関数を追加する。

```python
# app/api/dependencies.py に追加

async def get_conversation_or_404(
    conversation_id: str,
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
) -> Conversation:
    service = ConversationService(db)
    conversation = await service.get_conversation_by_id(conversation_id, tenant_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"会話 '{conversation_id}' が見つかりません",
        )
    return conversation
```

- **メリット**: FastAPIの依存性注入と統合され、型安全でテストしやすい
- **対象**: conversation, simple_chat, model, skill, mcp_server の各リソース
- **注意**: 「取得 → 更新 → 404チェック」のパターン（update系）はサービス戻り値に依存するため、Dependsではカバーしにくい。これらは `get_or_404()` を使う

**方式B: `get_or_404()` のAPI層での活用**

`app/utils/error_handler.py` の既存 `get_or_404()` を直接使う。

```python
# Before
conversation = await service.get_conversation_by_id(conversation_id, tenant_id)
if not conversation:
    raise HTTPException(status_code=404, detail=...)

# After
from app.utils.error_handler import get_or_404
conversation = await get_or_404(
    service.get_conversation_by_id, "会話", conversation_id,
    conversation_id, tenant_id,
)
```

- **デメリット**: 引数の渡し方が直感的でない（`resource_id`とget_funcの引数が別）

**結論**: 方式A（Depends）を GET/DELETE 系に、方式B（get_or_404）を PUT/PATCH 系に使い分ける。

### 影響ファイル

| ファイル | 変更内容 |
|----------|----------|
| `app/api/dependencies.py` | リソース取得関数を5つ追加 |
| `app/api/conversations/router.py` | 重複パターンを置換 |
| `app/api/simple_chats/router.py` | 同上 |
| `app/api/tenants.py` | 同上 |
| `app/api/models.py` | 同上 |
| `app/api/skills.py` | 同上 |
| `app/api/mcp_servers.py` | 同上 |

---

## 2. `db.commit()` のエラーハンドリング

| 項目 | 値 |
|------|------|
| 元の優先度 | HIGH |
| **調整後優先度** | **MEDIUM** |
| **実施判断** | **実施する（ただし元の指摘とは異なるアプローチ）** |

### 検証結果 — 元の指摘は不正確

`app/database.py:100-113` の `get_db()` を検証した結果、**セッション管理は既に安全**であることが判明:

```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()   # ← エンドポイント正常終了後に自動コミット
        except Exception:
            await session.rollback()  # ← 例外時に自動ロールバック
            raise
        finally:
            await session.close()
```

つまり:
- エンドポイント内の `await db.commit()` が例外を投げた場合、`get_db()` が **自動的にrollback** する
- 「サイレント失敗」は **発生しない** — 500エラーとして正しく伝播する
- `try/except` がエンドポイント側に無くても、ロールバックは保証されている

### 本当の問題: ダブルコミットパターン

実際の問題は、エンドポイント内の **明示的 `db.commit()` が冗長** であること:

1. エンドポイントが `await db.commit()` を呼ぶ（明示的コミット）
2. エンドポイントが正常返却
3. `get_db()` が `await session.commit()` を呼ぶ（自動コミット — ただしno-op）

これは動作上は問題ないが、**アーキテクチャの意図が不明確**になる。

### 実施方針

**全エンドポイントから明示的な `await db.commit()` を削除し、`get_db()` の自動コミットに統一する。**

- `get_db()` が自動コミットを行うため、エンドポイント側での明示コミットは不要
- ストリーミングエンドポイント(`streaming.py`)のように、トランザクション途中で部分コミットが必要な場合のみ `try/except` 付きで明示コミットを残す

### 削除対象

| ファイル | 行 | 内容 |
|----------|-----|------|
| `app/api/conversations/router.py` | 122, 150, 173, 195 | `await db.commit()` |
| `app/api/simple_chats/router.py` | 117, 139 | `await db.commit()` |
| `app/api/tenants.py` | 61, 115, 136 | `await db.commit()` |
| `app/api/models.py` | 78, 98, 121, 151 | `await db.commit()` |

**例外**: `app/api/conversations/streaming.py:321` は `try/except/rollback` 付きで正しく実装されているため変更不要。

---

## 3. レート制限の `X-Forwarded-For` ヘッダー検証

| 項目 | 値 |
|------|------|
| 元の優先度 | MEDIUM |
| **調整後優先度** | **対応不要** |
| **実施判断** | **実施しない** |

### 検証結果

`app/middleware/rate_limit.py:130-148` の `_get_rate_limit_key()` を検証:

```python
# 1. ユーザーID+テナントIDがあればユーザー単位で制限（主経路）
if user_id and tenant_id:
    return f"{self.key_prefix}user:{tenant_id}:{user_id}"

# 2. フォールバック: IP単位（副経路）
forwarded_for = self._get_header(scope, b"x-forwarded-for")
if forwarded_for:
    client_ip = forwarded_for.split(",")[0].strip()
    return f"{self.key_prefix}ip:{client_ip}"
```

### 対応不要とした理由

1. **プライベートネットワーク前提**: このシステムは外部公開されず、フロントエンドからのプライベート通信のみ。外部攻撃者が `X-Forwarded-For` を偽造する経路がない
2. **主経路は user_id + tenant_id**: フロントエンドから `X-User-Id` / `X-Tenant-Id` ヘッダーが付与される正常系では、IP ベースのフォールバック自体が使われない
3. **対策のROIが低い**: 信頼プロキシIPホワイトリストの導入は設定管理の複雑化を招き、プライベートネットワーク前提での実益が乏しい

### 将来的に対応が必要になる条件

- バックエンドを外部公開する場合
- CDNやWAFを経由する構成に変更する場合

---

## 4. リポジトリ層の不整合

| 項目 | 値 |
|------|------|
| 元の優先度 | MEDIUM |
| **調整後優先度** | **MEDIUM（部分的に実施）** |
| **実施判断** | **一部のみ実施する** |

### 検証結果と個別判断

#### 4-1. `find_by_tenant()` の戻り値不統一 → **実施する**

| リポジトリ | 戻り値 |
|-----------|--------|
| `simple_chat_repository.py:29` | `tuple[list[SimpleChat], int]` (リスト + 総件数) |
| `conversation_repository.py:34` | `list[Conversation]` (リストのみ) |

- SimpleChat側はページネーション用に総件数を返すが、Conversation側は返さない
- API層で `SimpleChatListResponse` は `total` を持つが、会話一覧側は返していない
- **対応**: `ConversationRepository.find_by_tenant()` も `tuple[list, int]` を返すよう統一

#### 4-2. `MessageLogRepository.save()` の `create()` との重複 → **実施しない**

```python
# message_log_repository.py:55-75
async def save(self, message_id, conversation_id, ...) -> MessageLog:
    log = MessageLog(message_id=..., conversation_id=..., ...)
    self.db.add(log)
    await self.db.flush()
    return log
```

- `BaseRepository.create()` はエンティティオブジェクトを受け取る汎用メソッド
- `save()` は個別のフィールドを受け取ってエンティティを構築する型安全なファクトリメソッド
- **呼び出し側で `MessageLog(...)` の構築を強制しない**ため、APIとして価値がある
- **判断**: 意図的な設計であり、リファクタリング不要

#### 4-3. `ConversationRepository.delete_with_related()` のSQL混在 → **実施しない**

```python
# conversation_repository.py:94-105
await self.db.execute(
    ConversationFile.__table__.delete().where(...)
)
await self.db.execute(
    MessageLog.__table__.delete().where(...)
)
```

- これは **raw SQL ではなく SQLAlchemy Core API** (`Table.delete()`)
- ORM の `cascade` で処理する代替案もあるが、`ConversationFile` はリレーションシップに `cascade="all, delete-orphan"` が既に設定されている（`app/models/conversation.py:115`）
- `MessageLog` は Conversation とのリレーションシップが定義されていないため、Core APIでの明示削除は妥当
- **判断**: 現状で正しく動作しており、変更の必要なし

#### 4-4. `ModelRepository.get_all_models()` のラッパー → **実施しない**

```python
# model_repository.py:24-40
async def get_all_models(self, status=None, limit=100, offset=0):
    filters = {}
    if status:
        filters["status"] = status
    return await self.get_all(limit=limit, offset=offset, order_by="created_at", order_desc=True, **filters)
```

- `BaseRepository.get_all()` には `order_by` や `order_desc` のデフォルトがないため、このラッパーはデフォルトのソート順を提供している
- 呼び出し側（`ModelService`）が `get_all()` の内部引数を知る必要がなくなる
- **判断**: 薄いラッパーだが、デフォルトパラメータの提供は正当な責務

### 実施対象

| 対象 | 変更内容 |
|------|----------|
| `app/repositories/conversation_repository.py` | `find_by_tenant()` の戻り値を `tuple[list, int]` に変更 |
| `app/services/conversation_service.py` | 戻り値の型変更に追従 |
| `app/api/conversations/router.py` | レスポンスにtotalを含める（任意） |
| `app/schemas/conversation.py` | `ConversationListResponse` スキーマを追加（任意） |

---

## 5. DBインデックス追加

| 項目 | 値 |
|------|------|
| 元の優先度 | LOW |
| **調整後優先度** | **LOW** |
| **実施判断** | **部分的に実施する** |

### 検証結果

モデル定義を確認した結果、既存のインデックス状況:

| テーブル | カラム | インデックス有無 |
|----------|--------|---------------|
| `conversations` | `tenant_id` | **あり** (index=True) |
| `conversations` | `user_id` | **あり** (index=True) |
| `conversations` | `status` | **なし** |
| `simple_chats` | `tenant_id` | **あり** (index=True) |
| `simple_chats` | `user_id` | **あり** (index=True) |
| `simple_chats` | `status` | **なし** |
| `tenants` | `status` | **なし** |
| `models` | `status` | **なし** |

### 実施判断

- **`conversations.status`**: 一覧取得で頻繁にフィルタされるため **追加推奨**
- **`simple_chats.status`**: 同上で **追加推奨**
- **`tenants.status`**: テナント数は少数（数十〜数百）が想定されるため **不要**
- **`models.status`**: モデル数は極少数（数個〜十数個）のため **不要**
- **`(tenant_id, user_id)` 複合インデックス**: `tenant_id` と `user_id` のそれぞれに個別インデックスが既にあり、PostgreSQLのビットマップインデックススキャンで組み合わせ可能。データ量が大きくない限り **不要**

### 実施内容

Alembicマイグレーションで以下のインデックスを追加:

```python
# conversations.status
# simple_chats.status
```

**注意**: 実施前にクエリのEXPLAIN ANALYZEで実際のパフォーマンスを計測すること。インデックスは書き込み性能とのトレードオフがある。

---

## 6. 空スキーマクラス `ConversationArchiveRequest`

| 項目 | 値 |
|------|------|
| 元の優先度 | LOW |
| **調整後優先度** | **LOW** |
| **実施判断** | **実施する** |

### 検証結果

`app/schemas/conversation.py:56-59`:

```python
class ConversationArchiveRequest(BaseModel):
    """会話アーカイブリクエスト"""
    pass
```

`app/api/conversations/router.py:162` で引数として使用:

```python
async def archive_conversation(
    ...
    request: ConversationArchiveRequest,  # ← 空のリクエストボディを要求
    ...
):
```

### 実施方針

**エンドポイントの引数から `request: ConversationArchiveRequest` を削除する。**

- 空のリクエストボディを要求する意味がない
- 将来的にアーカイブ時のオプション（理由メモなど）が必要になった場合、その時点でスキーマを追加すれば良い
- `ConversationArchiveRequest` クラス自体も削除する

### 影響ファイル

| ファイル | 変更内容 |
|----------|----------|
| `app/schemas/conversation.py` | `ConversationArchiveRequest` を削除 |
| `app/api/conversations/router.py` | `request` 引数を削除、import除去 |

---

## 7. メトリクスラベルの不整合

| 項目 | 値 |
|------|------|
| 元の優先度 | LOW |
| **調整後優先度** | **LOW** |
| **実施判断** | **一部のみ実施する** |

### 検証結果

| メトリクス関数 | ラベル | 使用状況 | 判断 |
|------------|--------|----------|------|
| `get_workspace_proxy_request_duration()` | `["method"]` | `credential_proxy.py:200` で `method=method` として正しく使用 | **問題なし** |
| `get_workspace_s3_sync_duration()` | `["direction"]` | **どこからも使用されていない（デッドコード）** | **削除 or 実装** |
| `get_workspace_gc_cycles()` | `["result"]` | `gc.py:64,68` で `result="success"` / `result="error"` として正しく使用 | **問題なし** |

### 実施方針

- `get_workspace_s3_sync_duration()`: S3同期処理（`app/services/workspace/s3_storage.py`）にメトリクス計測を追加するか、不要であれば関数定義を削除する
- 他2つは対応不要

---

## 実施ロードマップ

### Phase 1（推奨: 次回スプリント）

| # | 項目 | 優先度 | 見積り変更量 |
|---|------|--------|------------|
| 1 | `db.commit()` 冗長呼び出し削除 | MEDIUM | 小（各ファイルから1行ずつ削除） |
| 2 | 空スキーマ `ConversationArchiveRequest` 削除 | LOW | 小（2ファイル） |

**Phase 1を先にする理由**: 変更量が小さく、既存テストが通ることを確認しやすい。特に `db.commit()` 削除は `get_db()` の自動コミットに統一する重要なアーキテクチャ整理。

### Phase 2（推奨: Phase 1完了後）

| # | 項目 | 優先度 | 見積り変更量 |
|---|------|--------|------------|
| 3 | 404エラーハンドリングの統一 | MEDIUM | 中（7ファイル、21箇所） |
| 4 | リポジトリ `find_by_tenant()` 戻り値統一 | MEDIUM | 中（4ファイル） |

### Phase 3（推奨: パフォーマンス計測後）

| # | 項目 | 優先度 | 見積り変更量 |
|---|------|--------|------------|
| 5 | DBインデックス追加 | LOW | 小（Alembicマイグレーション1つ） |
| 6 | デッドメトリクス `s3_sync_duration` 対応 | LOW | 小（1ファイル） |

### 対応不要

| # | 項目 | 理由 |
|---|------|------|
| - | レート制限 `X-Forwarded-For` | プライベートネットワーク前提でリスク極小 |
| - | `MessageLogRepository.save()` 重複 | 型安全なファクトリメソッドとして正当 |
| - | `delete_with_related()` のSQL混在 | SQLAlchemy Core APIであり問題なし |
| - | `get_all_models()` ラッパー | デフォルトパラメータの提供は正当 |
| - | `proxy_request_duration` / `gc_cycles` メトリクス | 正しく使用されている |
