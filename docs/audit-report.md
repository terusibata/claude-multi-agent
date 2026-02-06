# バックエンド監査レポート: 本番運用時の不具合・潜在的バグ

## 概要

AIエージェントバックエンドの全ソースコードを精査し、本番運用時に問題となる不具合・潜在的バグを特定しました。
深刻度の高い順に、対応プランとともに記載します。

---

## CRITICAL（本番運用で即座に障害となる）

### BUG-001: 会話ロックのトークンが保存されず、ロック解放が必ず失敗する

**ファイル:** `app/services/execute_service.py:122-123, 173`

**問題:**
`DistributedLockManager.acquire()` はロック解放時に必要なトークン（`str`）を返すが、`execute_streaming()` ではこの戻り値を変数に保存していない。その後の `release()` 呼び出し時にトークンを渡していないため、`TypeError: release() missing 1 required positional argument: 'token'` が発生する。

```python
# 現状（トークンを無視）
await lock_manager.acquire(context.conversation_id)  # 戻り値を捨てている
# ...
await lock_manager.release(context.conversation_id)  # token引数が欠落 → TypeError
```

**影響:**
- 全てのエージェント実行で、ロック解放が例外により失敗する
- ロックはRedisのTTL（10分）が経過するまで保持されるため、同一会話への次のリクエストが10分間ブロックされる
- except節でエラーログは出力されるが、ユーザーには成功レスポンスが返るため問題に気づきにくい

**対応プラン:**
1. `acquire()` の戻り値をローカル変数 `lock_token` に保存する
2. `release()` 呼び出し時に `lock_token` を渡す
3. finally ブロック内でも `lock_token` を参照できるようスコープを調整する

---

### BUG-002: BaseHTTPMiddleware によるSSEストリーミングのバッファリング問題

**ファイル:** `app/middleware/auth.py`, `rate_limit.py`, `security_headers.py`, `tracing.py`

**問題:**
全ミドルウェアが `starlette.middleware.base.BaseHTTPMiddleware` を継承している。Starlette/FastAPIの `BaseHTTPMiddleware` は `call_next` を呼ぶ際、レスポンスボディを内部で消費し、新たな `StreamingResponse` として再送出する。これにより、SSE（Server-Sent Events）レスポンスが**チャンク単位でリアルタイム送信されず、バッファリングされる**。

**影響:**
- `EventSourceResponse` による SSE ストリーミングが期待通りに動作しない可能性がある
- クライアントはイベントをリアルタイムに受信できず、全イベントがまとめて届く、または接続がタイムアウトする
- エージェント実行のリアルタイム進捗表示が機能しなくなる

**対応プラン:**
1. 全ミドルウェアを `BaseHTTPMiddleware` から **pure ASGI middleware** に書き換える
2. 各ミドルウェアで `__init__` + `__call__` パターンの ASGI 実装に変更する
3. SSEエンドポイントでの動作を実際にテストで確認する

---

### BUG-003: S3操作が同期的でイベントループをブロックする

**ファイル:** `app/services/workspace/s3_storage.py` 全体

**問題:**
`S3StorageBackend` は `boto3` の同期クライアント（`boto3.client('s3')`）を使用しており、`put_object`, `get_object`, `list_objects_v2`, `download_file` 等の全操作が同期I/Oとなっている。メソッドは `async def` で定義されているが、内部の S3 呼び出しは `await` ではなく同期実行されるため、**asyncio のイベントループが完全にブロック**される。

**影響:**
- S3操作中（アップロード/ダウンロード/同期）、同一ワーカーの他の全リクエストが停止する
- ファイル同期（`sync_to_local`, `sync_from_local`）は複数ファイルを順次処理するため、ブロック時間が累積する
- 100MBファイルのアップロード中、数十秒間他のリクエストが処理されなくなる

**対応プラン:**
1. `aioboto3` または `aiobotocore` を使用した非同期S3クライアントに置き換える
2. または、`asyncio.to_thread()` で同期S3呼び出しをスレッドプールにオフロードする
3. 大ファイルの `sync_to_local`/`sync_from_local` では並行ダウンロード/アップロードを検討する

---

### BUG-004: SSEストリーミング中のDBセッションライフサイクル問題

**ファイル:** `app/api/conversations.py:498-671`, `app/database.py:101-114`

**問題:**
`stream_conversation()` エンドポイントで `db: AsyncSession = Depends(get_db)` によりDBセッションが注入される。`get_db()` はリクエスト完了後に `commit()` → `close()` を実行する。しかし、SSEレスポンスでは `_background_execution` が `asyncio.create_task()` でバックグラウンドタスクとして実行され、エンドポイント関数の戻り後もDBセッションを使い続ける。

特にクライアント切断時:
1. `_event_generator` が `CancelledError` を受ける
2. FastAPI が Dependency のクリーンアップ（`get_db` の finally）を実行し、セッションが `close()` される
3. バックグラウンドタスクは「引き続き実行」されるが、クローズ済みのセッションを使おうとする

**影響:**
- クライアント切断後のバックグラウンド処理で `SessionClosedError` や `InvalidRequestError` が発生する
- メッセージログや使用量ログの保存が失敗する
- トランザクションの一貫性が保証されない

**対応プラン:**
1. バックグラウンドタスク用に独立したDBセッションを生成する（`async_session_maker()` を直接使用）
2. SSEジェネレータ内でのDB操作は専用セッションスコープで管理する
3. クライアント切断時にもバックグラウンドタスクの結果が正しく永続化されるようにする

---

## HIGH（本番運用で重大な問題を引き起こす可能性が高い）

### BUG-005: タイトル生成が同期Bedrock呼び出しでイベントループをブロック

**ファイル:** `app/services/execute_service.py:563`

**問題:**
`_generate_and_update_title()` 内で `title_generator.generate()` を直接呼び出している。この関数は同期的にBedrock APIを呼び出すため、イベントループをブロックする。一方、`simple_chat_service.py:366` では同じパターンに対して `asyncio.to_thread()` で正しく対処されている。

```python
# execute_service.py - ブロッキング呼び出し
generated_title = title_generator.generate(...)

# simple_chat_service.py - 正しい対処
title = await asyncio.to_thread(self.title_generator.generate, ...)
```

**影響:**
- 初回会話実行時（タイトル生成時）にBedrock API呼び出し分（数秒）イベントループがブロックされる
- 同一ワーカーの他リクエストが遅延する

**対応プラン:**
1. `title_generator.generate()` を `asyncio.to_thread()` でラップする
2. または、タイトル生成を非同期対応に書き換える

---

### BUG-006: `register_ai_file` がファイル全体をダウンロードしてサイズを取得している

**ファイル:** `app/services/workspace_service.py:219`

**問題:**
```python
content, content_type = await self.s3.download(tenant_id, conversation_id, file_path)
# ... len(content) でサイズ取得
```
ファイルサイズと content_type を取得するためだけに、S3からファイル全体をダウンロードしている。`sync_from_local` 後に各ファイルに対してこの処理が走るため、全ファイルがメモリに読み込まれる。

**影響:**
- エージェント実行後のワークスペース同期で、全AIファイルがメモリに二重読み込みされる
- 大量のファイルがある場合、メモリ使用量が急増しOOMの原因になる

**対応プラン:**
1. `S3StorageBackend.get_metadata()` （`head_object`）を使用してサイズとcontent_typeを取得する
2. `register_ai_file` メソッドのファイルダウンロードを廃止する

---

### BUG-007: ファイルアップロードで全コンテンツをメモリに読み込む

**ファイル:** `app/services/workspace_service.py:80`

**問題:**
```python
content = await file.read()  # 最大100MBをメモリに読み込み
```
`max_upload_file_size` が100MBに設定されているため、単一のアップロードで100MBのメモリを消費する。同時アップロードが発生した場合、メモリが枯渇する。

**影響:**
- 同時に数件の大ファイルアップロードでメモリ枯渇が発生する可能性
- OOM Killer によるワーカープロセスの強制終了

**対応プラン:**
1. ストリーミングアップロード（`upload_stream`）を使用し、チャンク単位で処理する
2. ファイルサイズのチェックは Content-Length ヘッダーまたはストリーミング中のバイト数カウントで行う
3. メモリ上限が厳しい場合は一時ファイルを経由する

---

### BUG-008: `_check_context_limit_before_execution` でゼロ除算の可能性

**ファイル:** `app/services/execute_service.py:1088`

**問題:**
```python
usage_percent = (conversation.estimated_context_tokens / max_context) * 100
```
`max_context` は `context.model.context_window` から取得されるが、モデルデータにて `context_window` が 0 または未設定の場合、`ZeroDivisionError` が発生する。

後続の `_calculate_and_update_context_status` メソッド（1134行）では `if max_context > 0` のガードがあるが、この箇所にはない。

**影響:**
- `context_window` が 0 のモデルを使用した場合、エージェント実行が全て失敗する
- 例外はキャッチされず、500エラーとなる

**対応プラン:**
1. `max_context > 0` のガードを追加する
2. `context_window` が 0 の場合はコンテキストチェックをスキップする

---

### BUG-009: `update_conversation_context_status` がトークン数を累積でなく上書きする

**ファイル:** `app/services/conversation_service.py:228-229`

**問題:**
```python
conversation.total_input_tokens = total_input_tokens
conversation.total_output_tokens = total_output_tokens
```
フィールド名 `total_input_tokens` / `total_output_tokens` は累積値を示唆するが、実際には毎回の実行値で上書きされている。つまり、2回目以降の実行で1回目の使用量データが失われる。

**影響:**
- 会話全体のトークン使用量が正しく追跡されない
- コンテキスト制限の判定が、直近の実行のみに基づいて行われ、実際のコンテキストサイズと乖離する
- 長い会話でコンテキストオーバーフローを検出できない

**対応プラン:**
1. 累積加算に変更する（`conversation.total_input_tokens += total_input_tokens`）
2. または、`estimated_context_tokens`（推定値）の計算ロジックを見直し、DBの既存値に加算する方式に統一する
3. マイグレーションで既存データの整合性を確認する

---

### BUG-010: バックグラウンドタスクがタイムアウト後もキャンセルされない

**ファイル:** `app/api/conversations.py:446-463, 487-491`

**問題:**
`_event_generator` 内でイベントタイムアウト（300秒）に達した場合、`break` でループを抜けるが、バックグラウンドの `background_task` はキャンセルされずに実行を継続する。

```python
# タイムアウト時
if time_since_last_event >= EVENT_TIMEOUT_SECONDS:
    # ... エラーイベントを送信
    break  # ← ループを抜ける

# finally ブロック
finally:
    if not background_task.done():
        logger.info("Background task continues...")  # ← キャンセルしない
```

**影響:**
- タイムアウトしたエージェント実行がバックグラウンドで無限に実行を続ける
- サーバーリソース（CPU、メモリ、DB接続、Bedrock API課金）が浪費される
- ロックが10分間解放されない（BUG-001との複合問題）

**対応プラン:**
1. タイムアウト時に `background_task.cancel()` を呼び出す
2. CancelledError を適切にハンドリングし、部分的な結果を保存する
3. タイムアウト後のクリーンアップ処理を実装する

---

### BUG-011: 会話削除時にS3ワークスペースファイルが残留する

**ファイル:** `app/services/conversation_service.py:271-284`

**問題:**
`delete_conversation()` はメッセージログとDBレコードを削除するが、S3上のワークスペースファイルは削除されない。`ConversationFile` テーブルのレコードもカスケード削除されない。

**影響:**
- 削除された会話のファイルがS3に永続的に残り続ける
- 長期運用でS3コストが不必要に増大する
- 孤児ファイルの手動クリーンアップが必要になる

**対応プラン:**
1. `delete_conversation()` に S3 ファイル削除処理を追加する
2. `ConversationFile` テーブルのレコードも削除する
3. S3削除に失敗した場合のフォールバック（バックグラウンドクリーンアップジョブ）を検討する

---

## MEDIUM（本番運用で問題となりうる）

### BUG-012: Redis接続プール初期化の競合状態

**ファイル:** `app/infrastructure/redis.py:23-44`

**問題:**
```python
async def get_redis_pool() -> ConnectionPool:
    global _redis_pool
    if _redis_pool is None:  # ← ロックなし
        _redis_pool = ConnectionPool.from_url(...)
    return _redis_pool
```
初回アクセス時に複数のコルーチンが同時に `_redis_pool is None` チェックを通過する可能性がある。結果として複数のConnectionPoolが作成され、最後以外はリークする。

**対応プラン:**
1. `asyncio.Lock()` を使用して排他制御を行う
2. `ConnectionPool.from_url()` をロック内で実行する

---

### BUG-013: Poolイベントリスナーが全プールに適用される

**ファイル:** `app/database.py:48-82`

**問題:**
```python
@event.listens_for(Pool, "checkout")
def on_checkout(dbapi_connection, connection_record, connection_proxy):
```
`Pool` クラス全体にリスナーを登録しているため、アプリケーション内で生成される全てのコネクションプールに対してイベントが発火する。

**対応プラン:**
1. `@event.listens_for(engine.pool, "checkout")` のようにエンジン固有のプールに限定する
2. または `@event.listens_for(engine, "checkout")` でエンジンレベルのイベントを使用する

---

### BUG-014: `/metrics` エンドポイントが認証の対象となっている

**ファイル:** `app/middleware/auth.py:31-38`

**問題:**
`SKIP_AUTH_PATHS` に `/metrics` が含まれていないため、API認証が有効な環境ではPrometheusがメトリクスを収集できない。

**対応プラン:**
1. `/metrics` を `SKIP_AUTH_PATHS` に追加する
2. または、メトリクス収集には専用の認証方式を設定する

---

### BUG-015: SimpleChat の `stream_message` でDB変更がコミットされるタイミングの問題

**ファイル:** `app/services/simple_chat_service.py:320-412`, `app/api/simple_chats.py:354-363`

**問題:**
`stream_message()` 内でメッセージ保存（`_save_message`）、タイトル更新、使用量ログ保存が行われるが、これらは `flush()` のみでコミットされていない。SSEジェネレータ内の処理であるため、`get_db()` のコミットタイミングとジェネレータの完了タイミングに依存する。

ストリーミング中にエラーが発生した場合、ユーザーメッセージは保存済み（flush済み）だがコミットされず、ロールバックされる。その場合、ユーザーの入力が消失する。

**対応プラン:**
1. ユーザーメッセージの保存後に明示的に `commit()` する（部分コミット）
2. エラー時でもユーザーメッセージが保持されるようにする
3. アシスタント応答と使用量ログは別トランザクションで管理する

---

### BUG-016: `simple_chat_service.py` のコスト計算の単位不一致の可能性

**ファイル:** `app/services/simple_chat_service.py:431-434`

**問題:**
```python
input_cost = (Decimal(input_tokens) / 1000) * model.input_token_price
```
1Kトークンあたりの価格で計算しているが、LLMの価格設定は一般的に1Mトークンあたりで記載される。`model.input_token_price` の単位がDBの定義と一致していることを確認する必要がある。

**対応プラン:**
1. `Model` テーブルの価格フィールドの単位を明確にドキュメント化する
2. `model.calculate_cost()` メソッドと `SimpleChatService._calculate_cost()` の計算ロジックが一致しているか検証する
3. 不一致がある場合は `model.calculate_cost()` に統一する

---

### BUG-017: `entrypoint.sh` でマイグレーション失敗時の処理が不十分

**ファイル:** `entrypoint.sh`

**問題:**
```bash
set -e
alembic upgrade head
exec "$@"
```
`set -e` により `alembic upgrade head` が失敗するとスクリプトが終了するが、マルチコンテナ/マルチタスク環境で複数のコンテナが同時にマイグレーションを実行した場合、ロック競合が発生する可能性がある。

**対応プラン:**
1. マイグレーション実行前にアドバイザリロックを取得する
2. マイグレーション済みかどうかのチェックを追加する
3. リトライロジックを追加する

---

### BUG-018: レート制限キーがヘッダーのスプーフィングに依存

**ファイル:** `app/middleware/rate_limit.py:114-138`

**問題:**
```python
user_id = request.headers.get("X-User-ID")
tenant_id = request.headers.get("X-Tenant-ID")
```
レート制限キーは `X-User-ID` + `X-Tenant-ID` ヘッダーで決定される。フロントエンドとの内部通信前提であるため、フロントエンドが正しい値を設定する信頼モデルに依存している。ただし、フロントエンドにバグがあり同一ヘッダーを送信した場合、全ユーザーが同一のレート制限を共有してしまう。

**対応プラン:**
1. API認証（API Key）とレート制限キーを組み合わせる
2. フォールバックのIPベース制限を適切に設定する
3. ヘッダー未設定時のデフォルト動作を見直す

---

## LOW（改善推奨だが即座の障害リスクは低い）

### BUG-019: `_save_message_log` のメッセージ順序番号のデクリメントが脆弱

**ファイル:** `app/services/execute_service.py:618`

**問題:**
メッセージログをスキップする際に `context.message_seq -= 1` で巻き戻している。連続してスキップされるメッセージがある場合や、並行性の問題が生じた場合、シーケンス番号の衝突が起こりうる。

**対応プラン:**
1. シーケンス番号をスキップさせるのではなく、保存対象のメッセージに対してのみインクリメントする方式に変更する
2. `message_seq` のインクリメントを `_save_message_log` の保存成功時のみ行うようにリファクタリングする

---

### BUG-020: `@lru_cache()` による設定のキャッシュがテスト時に問題となる

**ファイル:** `app/config.py:233-236`

**問題:**
`get_settings()` が `@lru_cache()` でキャッシュされるため、テスト時に環境変数を変更しても設定が反映されない。

**対応プラン:**
1. テスト用に `get_settings.cache_clear()` を `conftest.py` で呼び出す
2. Dependency Injection パターンで設定をオーバーライド可能にする

---

### BUG-021: Redisフェイルオープンによるレート制限の無効化

**ファイル:** `app/middleware/rate_limit.py:189-192`

**問題:**
Redis接続エラー時にレート制限チェックをスキップ（通過を許可）している。Redis障害中は事実上レート制限が無効化される。

**対応プラン:**（内部通信のため優先度は低い）
1. Redis障害の検知とアラート通知を追加する
2. インメモリのフォールバックカウンターを検討する
3. 現状のフェイルオープン方針を意図的な設計として文書化する

---

### BUG-022: ストリーミングエンドポイントのHTTPステータスコード問題

**ファイル:** `app/api/conversations.py:663-671`, `app/api/simple_chats.py:354-363`

**問題:**
`EventSourceResponse` がHTTP 200で返されるため、ストリーミング開始後に発生したエラーはSSEイベントとしてのみ通知される。クライアント側でSSEエラーイベントを正しくハンドリングしていない場合、エラーが見逃される。

**対応プラン:**
1. ストリーミング開始前のバリデーションエラーは通常のHTTPエラーとして返す（現状通り）
2. SSEエラーイベントのフォーマットとクライアント側ハンドリングをドキュメント化する
3. 重大なエラー時はSSE接続を即座にクローズすることを明確にする

---

### BUG-023: `download_stream` でBodyが例外時にクローズされない

**ファイル:** `app/services/workspace/s3_storage.py:226-246`

**問題:**
```python
body = response['Body']
while True:
    chunk = body.read(self.chunk_size)
    if not chunk:
        break
    yield chunk
body.close()  # ← 正常終了時のみ呼ばれる
```
ジェネレータが中途で終了した場合（消費側がbreakした場合等）、`body.close()` が呼ばれずリソースリークする。

**対応プラン:**
1. `try-finally` ブロックで `body.close()` を保証する

---

## 対応の優先度まとめ

| 優先度 | Bug ID | 概要 | 修正の複雑さ |
|--------|--------|------|-------------|
| **CRITICAL** | BUG-001 | 会話ロック解放失敗 | 低（数行の変更） |
| **CRITICAL** | BUG-002 | SSEストリーミングのバッファリング | 高（ミドルウェア全書き換え） |
| **CRITICAL** | BUG-003 | S3同期I/Oブロック | 中（ライブラリ変更） |
| **CRITICAL** | BUG-004 | SSE中のDBセッションライフサイクル | 中（セッション管理の再設計） |
| **HIGH** | BUG-005 | タイトル生成のブロッキング | 低（1行の変更） |
| **HIGH** | BUG-006 | AI ファイル登録で全ファイルダウンロード | 低（API変更） |
| **HIGH** | BUG-007 | ファイルアップロードのメモリ消費 | 中（ストリーミング化） |
| **HIGH** | BUG-008 | ゼロ除算の可能性 | 低（ガード追加） |
| **HIGH** | BUG-009 | トークン数の上書き | 低（加算に変更） |
| **HIGH** | BUG-010 | タイムアウト後のタスク未キャンセル | 低（cancel追加） |
| **HIGH** | BUG-011 | 会話削除時のS3残留 | 中（削除処理追加） |
| **MEDIUM** | BUG-012 | Redis初期化競合 | 低（Lock追加） |
| **MEDIUM** | BUG-013 | Pool リスナーのスコープ | 低（対象変更） |
| **MEDIUM** | BUG-014 | /metrics の認証 | 低（パス追加） |
| **MEDIUM** | BUG-015 | SimpleChat コミットタイミング | 中（トランザクション設計） |
| **MEDIUM** | BUG-016 | コスト計算の単位 | 低（検証・修正） |
| **MEDIUM** | BUG-017 | マイグレーション同時実行 | 中（ロック追加） |
| **MEDIUM** | BUG-018 | レート制限キーのスプーフィング | 低（設計文書化） |
| **LOW** | BUG-019 | メッセージ順序番号 | 中（リファクタリング） |
| **LOW** | BUG-020 | 設定キャッシュ | 低（テスト改善） |
| **LOW** | BUG-021 | Redis フェイルオープン | 低（文書化） |
| **LOW** | BUG-022 | SSE HTTPステータス | 低（文書化） |
| **LOW** | BUG-023 | S3 Body リソースリーク | 低（try-finally追加） |

---

## 推奨対応順序

### Phase 1: 即時対応（CRITICALバグ修正）
1. **BUG-001** - ロックトークンの修正（最優先、数行の変更で修正可能）
2. **BUG-008** - ゼロ除算ガード（簡単な修正）
3. **BUG-005** - タイトル生成のスレッド化（1行の修正）
4. **BUG-009** - トークン累積の修正
5. **BUG-010** - タイムアウト後のタスクキャンセル

### Phase 2: 短期対応（イベントループブロック解消）
6. **BUG-003** - S3操作の非同期化
7. **BUG-004** - SSEストリーミング用のDBセッション分離
8. **BUG-006** - register_ai_file の最適化
9. **BUG-007** - ファイルアップロードのストリーミング化

### Phase 3: 中期対応（ミドルウェア・インフラ改善）
10. **BUG-002** - ミドルウェアのPure ASGI化
11. **BUG-011** - 会話削除時のS3クリーンアップ
12. **BUG-012** - Redis初期化の排他制御
13. **BUG-015** - SimpleChat トランザクション管理

### Phase 4: 品質改善
14. 残りのMEDIUM/LOWバグの修正
15. テストカバレッジの向上
16. ドキュメントの整備
