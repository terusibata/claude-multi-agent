# バックエンド監査レポート: 本番運用時の不具合・潜在的バグ

## 概要

AIエージェントバックエンドの全ソースコードを精査し、本番運用時に問題となる不具合・潜在的バグを特定しました。
深刻度の高い順に、対応プランとともに記載します。

## 修正状況サマリー

| 状態 | 件数 | Bug ID |
|------|------|--------|
| **修正済み** | 19件 | BUG-001〜015, 017, 019, 020, 023 |
| **対応不要** | 1件 | BUG-016（仕様通り） |
| **未対応（設計判断）** | 3件 | BUG-018, 021, 022 |

---

## CRITICAL（本番運用で即座に障害となる）

### BUG-001: 会話ロックのトークンが保存されず、ロック解放が必ず失敗する ✅ 修正済み

**ファイル:** `app/services/execute_service.py`

**問題:**
`DistributedLockManager.acquire()` はロック解放時に必要なトークン（`str`）を返すが、`execute_streaming()` ではこの戻り値を変数に保存していない。`release()` 呼び出し時にトークンを渡していないため、`TypeError` が発生する。

**修正内容:**
- `acquire()` の戻り値を `lock_token` 変数に保存
- `release()` 呼び出し時に `lock_token` を渡す
- finally ブロック内で `lock_token` の存在を確認してから解放

---

### BUG-002: BaseHTTPMiddleware によるSSEストリーミングのバッファリング問題 ✅ 修正済み

**ファイル:** `app/middleware/auth.py`, `rate_limit.py`, `security_headers.py`, `tracing.py`

**問題:**
全ミドルウェアが `BaseHTTPMiddleware` を継承しており、SSEレスポンスがバッファリングされてリアルタイム送信されない。

**修正内容:**
- 全4ミドルウェアを Pure ASGI middleware（`__init__` + `__call__` パターン）に書き換え
- `scope`, `receive`, `send` を直接操作する実装に変更

---

### BUG-003: S3操作が同期的でイベントループをブロックする ✅ 修正済み

**ファイル:** `app/services/workspace/s3_storage.py`

**問題:**
`boto3` の同期クライアントによるS3操作が asyncio イベントループを完全にブロックする。

**修正内容:**
- 全てのS3操作を `asyncio.to_thread()` でスレッドプールにオフロード
- `list_files`, `sync_from_local` 等のバッチ操作も内部関数として `to_thread` でラップ
- `delete_prefix()` メソッドを追加（BUG-011用）

---

### BUG-004: SSEストリーミング中のDBセッションライフサイクル問題 ✅ 修正済み

**ファイル:** `app/api/conversations.py`

**問題:**
SSEレスポンスのバックグラウンドタスクがリクエストスコープのDBセッションを共有しており、クライアント切断時にセッションのクリーンアップ競合が発生する。

**修正内容:**
- `_background_execution` が `async_session_maker()` で独立したDBセッションを生成するよう変更
- `_event_generator` と `stream_conversation` から `execute_service` の受け渡しを削除
- リクエストスコープのセッションはバリデーション用途のみに限定

---

## HIGH（本番運用で重大な問題を引き起こす可能性が高い）

### BUG-005: タイトル生成が同期Bedrock呼び出しでイベントループをブロック ✅ 修正済み

**ファイル:** `app/services/execute_service.py`

**修正内容:**
- `title_generator.generate()` を `asyncio.to_thread()` でラップ

---

### BUG-006: `register_ai_file` がファイル全体をダウンロードしてサイズを取得している ✅ 修正済み

**ファイル:** `app/services/workspace_service.py`

**修正内容:**
- `s3.download()` の代わりに `s3.get_metadata()`（`head_object`）を使用してファイルサイズとcontent_typeを取得

---

### BUG-007: ファイルアップロードで全コンテンツをメモリに読み込む ✅ 修正済み

**ファイル:** `app/services/workspace_service.py`

**修正内容:**
- `file.read()` による全コンテンツ読み込みを廃止
- `s3.upload_stream()` に `file.file`（SpooledTemporaryFile）を直接渡してストリーミングアップロード
- ファイルサイズチェックはメタデータの申告サイズで実施

---

### BUG-008: `_check_context_limit_before_execution` でゼロ除算の可能性 ✅ 修正済み

**ファイル:** `app/services/execute_service.py`

**修正内容:**
- `max_context > 0` のガードを追加

---

### BUG-009: `update_conversation_context_status` がトークン数を累積でなく上書きする ✅ 修正済み

**ファイル:** `app/services/conversation_service.py`

**修正内容:**
- `conversation.total_input_tokens = total_input_tokens` を
  `conversation.total_input_tokens = (conversation.total_input_tokens or 0) + total_input_tokens` に変更

---

### BUG-010: バックグラウンドタスクがタイムアウト後もキャンセルされない ✅ 修正済み

**ファイル:** `app/api/conversations.py`

**修正内容:**
- `_event_generator` の finally ブロックで `background_task.cancel()` を実行
- `CancelledError` を適切にハンドリング

---

### BUG-011: 会話削除時にS3ワークスペースファイルが残留する ✅ 修正済み

**ファイル:** `app/services/conversation_service.py`

**修正内容:**
- `delete_conversation()` に S3 ファイル削除処理（`s3.delete_prefix()`）を追加
- `ConversationFile` テーブルのレコードも削除するよう追加

---

## MEDIUM（本番運用で問題となりうる）

### BUG-012: Redis接続プール初期化の競合状態 ✅ 修正済み

**ファイル:** `app/infrastructure/redis.py`

**修正内容:**
- `asyncio.Lock()` を使用したダブルチェックロッキングパターンで排他制御

---

### BUG-013: Poolイベントリスナーが全プールに適用される ✅ 修正済み

**ファイル:** `app/database.py`

**修正内容:**
- `@event.listens_for(Pool, ...)` を `@event.listens_for(engine.sync_engine.pool, ...)` に変更
- このエンジン固有のプールにのみリスナーを登録

---

### BUG-014: `/metrics` エンドポイントが認証の対象となっている ✅ 修正済み

**ファイル:** `app/middleware/auth.py`

**修正内容:**
- `/metrics` を `SKIP_AUTH_PATHS` に追加

---

### BUG-015: SimpleChat の `stream_message` でDB変更がコミットされるタイミングの問題 ✅ 修正済み

**ファイル:** `app/services/simple_chat_service.py`

**修正内容:**
- ユーザーメッセージ保存後に `await self.db.commit()` を実行して即座にコミット
- ストリーミングエラーが発生してもユーザーメッセージが保持される

---

### BUG-016: `simple_chat_service.py` のコスト計算の単位不一致の可能性 ⏭️ 対応不要

**理由:** コスト計算は AWS 公式ドキュメントの表記（1Kトークンあたり）に合わせており、DB の価格設定もこの単位で統一されている。仕様通りのため修正不要。

---

### BUG-017: マイグレーション同時実行の競合 ✅ 修正済み

**ファイル:** `alembic/env.py`

**修正内容:**
- `do_run_migrations()` に PostgreSQL アドバイザリーロック（`pg_advisory_lock`）を追加
- 複数コンテナが同時にマイグレーションを実行しても排他制御される

---

### BUG-018: レート制限キーがヘッダーのスプーフィングに依存 📝 設計上許容

**理由:** フロントエンドとの内部通信前提であり、API認証（API Key）でアクセス制御されている。フロントエンドのバグによる影響は、API Key 単位のフォールバック制限で緩和される。現行の設計を文書化し、必要に応じてフロントエンドの検証を強化する方針。

---

## LOW（改善推奨だが即座の障害リスクは低い）

### BUG-019: `_save_message_log` のメッセージ順序番号のデクリメントが脆弱 ✅ 修正済み

**ファイル:** `app/services/execute_service.py`

**修正内容:**
- メッセージ受信時のインクリメントを廃止
- 保存成功時にのみ `context.message_seq += 1` を実行するよう変更
- スキップ時のデクリメント処理を削除

---

### BUG-020: `@lru_cache()` による設定のキャッシュがテスト時に問題となる ✅ 修正済み

**ファイル:** `app/config.py`

**修正内容:**
- `clear_settings_cache()` 関数を追加（`get_settings.cache_clear()` をラップ）
- テスト時に `conftest.py` から呼び出すことで設定のオーバーライドが可能

---

### BUG-021: Redisフェイルオープンによるレート制限の無効化 📝 設計上許容

**理由:** 内部通信前提のため、可用性を優先するフェイルオープン方針は妥当。Redis障害時のアラート通知はインフラ監視層で対応する方針。

---

### BUG-022: ストリーミングエンドポイントのHTTPステータスコード問題 📝 設計上許容

**理由:** SSEプロトコルの仕様上、ストリーミング開始後のエラー通知はイベントとして送信するのが標準的な実装。クライアント側でSSEエラーイベントを適切にハンドリングする前提。

---

### BUG-023: `download_stream` でBodyが例外時にクローズされない ✅ 修正済み

**ファイル:** `app/services/workspace/s3_storage.py`

**修正内容:**
- `try-finally` ブロックで `body.close()` を保証

---

## 修正済み対応一覧

| Bug ID | 深刻度 | 修正ファイル | 概要 |
|--------|--------|-------------|------|
| BUG-001 | CRITICAL | `execute_service.py` | ロックトークン保存・解放 |
| BUG-002 | CRITICAL | `middleware/*.py` (4ファイル) | Pure ASGI化 |
| BUG-003 | CRITICAL | `s3_storage.py` | `asyncio.to_thread()` |
| BUG-004 | CRITICAL | `conversations.py` | 独立DBセッション |
| BUG-005 | HIGH | `execute_service.py` | `asyncio.to_thread()` |
| BUG-006 | HIGH | `workspace_service.py` | `head_object` 使用 |
| BUG-007 | HIGH | `workspace_service.py` | ストリームアップロード |
| BUG-008 | HIGH | `execute_service.py` | ゼロ除算ガード |
| BUG-009 | HIGH | `conversation_service.py` | トークン累積加算 |
| BUG-010 | HIGH | `conversations.py` | タスクキャンセル |
| BUG-011 | HIGH | `conversation_service.py` | S3/DBクリーンアップ |
| BUG-012 | MEDIUM | `redis.py` | `asyncio.Lock()` |
| BUG-013 | MEDIUM | `database.py` | プール固有リスナー |
| BUG-014 | MEDIUM | `auth.py` | `/metrics` スキップ |
| BUG-015 | MEDIUM | `simple_chat_service.py` | 即座コミット |
| BUG-017 | MEDIUM | `alembic/env.py` | アドバイザリーロック |
| BUG-019 | LOW | `execute_service.py` | seq管理リファクタ |
| BUG-020 | LOW | `config.py` | キャッシュクリア関数 |
| BUG-023 | LOW | `s3_storage.py` | `try-finally` |
