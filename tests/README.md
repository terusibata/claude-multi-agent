# テストガイド

## 概要

本プロジェクトでは、本番環境と同等の条件でテストを実行するため、以下の構成を採用しています。

- **testcontainers**: PostgreSQLコンテナを自動起動し、実際のDBでテスト
- **moto**: AWS S3をモックし、ワークスペース機能をテスト
- **pytest-asyncio**: 非同期コードのテストサポート

---

## 前提条件

### 必須

- **Docker**: testcontainersがPostgreSQLコンテナを起動するために必要
- **Python 3.11+**

### インストール

```bash
pip install -r requirements.txt
```

---

## テスト実行方法

### 基本実行

```bash
# 全テスト実行
pytest

# 詳細出力
pytest -v

# 特定ファイルのみ
pytest tests/api/test_models.py

# 特定テストのみ
pytest tests/api/test_models.py::TestModelDelete::test_delete_model_in_use_by_tenant
```

### マーカーによるフィルタリング

```bash
# 統合テストのみ（API経由）
pytest -m integration

# 単体テストのみ（サービス層直接）
pytest -m unit

# 遅いテストを除外
pytest -m "not slow"
```

### カバレッジ測定

```bash
# カバレッジ付き実行
pytest --cov=app

# HTMLレポート生成
pytest --cov=app --cov-report=html
# → htmlcov/index.html で確認

# 特定ディレクトリのカバレッジ
pytest --cov=app/services --cov-report=term-missing
```

### 並列実行（高速化）

```bash
# CPUコア数に応じて自動並列化
pytest -n auto

# 4並列で実行
pytest -n 4
```

### CI向け実行

```bash
# JUnit形式のレポート出力
pytest --junitxml=test-results.xml --cov=app --cov-report=xml
```

---

## テスト構成

```
tests/
├── conftest.py                      # 共通fixture定義
├── pytest.ini                       # pytest設定
├── README.md                        # このファイル
│
├── api/                             # API統合テスト
│   ├── __init__.py
│   ├── test_models.py              # モデルAPI
│   ├── test_tenants.py             # テナントAPI
│   └── test_conversations.py       # 会話API
│
└── services/                        # サービス層単体テスト
    ├── __init__.py
    ├── test_model_service.py       # ModelService
    └── test_workspace_service.py   # WorkspaceService（S3モック）
```

---

## テスト内容

### API統合テスト (`tests/api/`)

HTTPクライアント経由でAPIエンドポイントをテストします。

#### test_models.py - モデル管理API

| テストクラス | テスト内容 |
|-------------|-----------|
| `TestModelsCRUD` | モデルの作成・取得・更新 |
| `TestModelDelete` | モデル削除（紐づきチェック） |

| テスト名 | 説明 |
|---------|------|
| `test_create_model` | モデル作成（正常系） |
| `test_create_model_duplicate` | 重複モデル作成で409エラー |
| `test_get_models` | モデル一覧取得 |
| `test_get_models_filter_by_status` | ステータスでフィルタリング |
| `test_get_model_by_id` | ID指定でモデル取得 |
| `test_get_model_not_found` | 存在しないモデルで404 |
| `test_update_model` | モデル情報更新 |
| `test_update_model_status` | ステータス変更 |
| `test_delete_model_success` | 紐づきなしで削除成功 |
| `test_delete_model_in_use_by_tenant` | テナント使用中で409 |
| `test_delete_model_in_use_by_conversation` | 会話使用中で409 |
| `test_delete_model_not_found` | 存在しないモデルで404 |

#### test_tenants.py - テナント管理API

| テストクラス | テスト内容 |
|-------------|-----------|
| `TestTenantsCRUD` | テナントのCRUD操作全般 |

| テスト名 | 説明 |
|---------|------|
| `test_create_tenant` | テナント作成（正常系） |
| `test_create_tenant_minimal` | 最小限パラメータで作成 |
| `test_create_tenant_duplicate` | 重複テナントで409 |
| `test_create_tenant_invalid_model` | 存在しないモデル指定 |
| `test_get_tenants` | テナント一覧取得 |
| `test_get_tenant_by_id` | ID指定でテナント取得 |
| `test_get_tenant_not_found` | 存在しないテナントで404 |
| `test_update_tenant` | テナント情報更新 |
| `test_update_tenant_status` | ステータス変更 |
| `test_delete_tenant` | テナント削除 |
| `test_delete_tenant_not_found` | 存在しないテナントで404 |

#### test_conversations.py - 会話管理API

| テストクラス | テスト内容 |
|-------------|-----------|
| `TestConversationsCRUD` | 会話のCRUD操作 |
| `TestConversationMessages` | メッセージ取得 |

| テスト名 | 説明 |
|---------|------|
| `test_create_conversation` | 会話作成（正常系） |
| `test_create_conversation_with_workspace` | ワークスペース有効で作成 |
| `test_create_conversation_uses_tenant_default_model` | テナントデフォルトモデル使用 |
| `test_create_conversation_tenant_not_found` | 存在しないテナントで404 |
| `test_get_conversations` | 会話一覧取得 |
| `test_get_conversations_filter_by_user` | ユーザーでフィルタリング |
| `test_get_conversations_filter_by_status` | ステータスでフィルタリング |
| `test_get_conversation_by_id` | ID指定で会話取得 |
| `test_get_conversation_not_found` | 存在しない会話で404 |
| `test_get_conversation_wrong_tenant` | 別テナントの会話にアクセス不可 |
| `test_update_conversation` | 会話情報更新 |
| `test_archive_conversation` | 会話アーカイブ |
| `test_delete_conversation` | 会話削除 |
| `test_get_messages_empty` | メッセージ一覧（空） |

---

### サービス層単体テスト (`tests/services/`)

サービスクラスを直接テストします。DBセッションを注入して実行。

#### test_model_service.py - ModelService

| テストクラス | テスト内容 |
|-------------|-----------|
| `TestModelServiceCRUD` | CRUD操作 |
| `TestModelServiceDelete` | 削除と紐づきチェック |

| テスト名 | 説明 |
|---------|------|
| `test_create_model` | モデル作成 |
| `test_get_by_id` | ID指定で取得 |
| `test_get_by_id_not_found` | 存在しない場合None |
| `test_get_all` | 全モデル取得 |
| `test_get_all_filter_by_status` | ステータスフィルタ |
| `test_delete_success` | 削除成功 |
| `test_delete_not_found` | 存在しない場合False |
| `test_delete_in_use_by_tenant` | テナント使用中でエラー |
| `test_delete_in_use_by_conversation` | 会話使用中でエラー |
| `test_check_model_usage` | 使用状況チェック |

#### test_workspace_service.py - WorkspaceService（S3モック）

| テストクラス | テスト内容 |
|-------------|-----------|
| `TestWorkspaceServiceBasic` | パス生成等の基本機能 |
| `TestWorkspaceFileOperations` | ファイル操作 |

| テスト名 | 説明 |
|---------|------|
| `test_workspace_path_generation` | ローカルパス生成 |
| `test_s3_key_generation` | S3キー生成 |
| `test_upload_file_to_s3` | S3へのアップロード |
| `test_download_file_from_s3` | S3からのダウンロード |
| `test_list_files` | ファイル一覧取得 |
| `test_register_ai_file` | AIファイル登録 |
| `test_get_presented_files` | Presentedファイル取得 |

---

## Fixture一覧

### conftest.py で定義

| Fixture | スコープ | 説明 |
|---------|---------|------|
| `postgres_container` | session | PostgreSQLコンテナ |
| `database_url` | session | 非同期DB URL |
| `sync_database_url` | session | 同期DB URL |
| `engine` | function | SQLAlchemy非同期エンジン |
| `db_session` | function | DBセッション |
| `client` | function | HTTPテストクライアント |
| `sample_model` | function | サンプルモデル |
| `sample_tenant` | function | サンプルテナント |
| `sample_conversation` | function | サンプル会話 |
| `aws_credentials` | function | AWSモック認証情報 |
| `s3_bucket_name` | function | テスト用S3バケット名 |

---

## トラブルシューティング

### Docker関連

```
Error: Cannot connect to Docker daemon
```
→ Dockerが起動しているか確認してください

```
Error: Pull access denied for postgres
```
→ `docker pull postgres:15-alpine` を手動実行

### DB接続エラー

```
asyncpg.exceptions.ConnectionDoesNotExistError
```
→ テスト間でDBセッションが共有されている可能性。`db_session` fixtureのスコープを確認

### S3モック関連

```
botocore.exceptions.NoCredentialsError
```
→ `aws_credentials` fixtureが適用されているか確認

---

## CI/CD設定例

### GitHub Actions

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run tests
        run: pytest --cov=app --cov-report=xml --junitxml=test-results.xml

      - name: Upload coverage
        uses: codecov/codecov-action@v4
        with:
          file: coverage.xml
```

---

## テスト追加ガイド

### 新しいAPIテストを追加する場合

1. `tests/api/` に `test_xxx.py` を作成
2. `@pytest.mark.integration` マーカーを付与
3. `client` fixtureを使用してAPIを呼び出し

```python
import pytest
from httpx import AsyncClient

class TestXxxAPI:
    @pytest.mark.integration
    async def test_xxx(self, client: AsyncClient):
        response = await client.get("/api/xxx")
        assert response.status_code == 200
```

### 新しいサービステストを追加する場合

1. `tests/services/` に `test_xxx_service.py` を作成
2. `@pytest.mark.unit` マーカーを付与
3. `db_session` fixtureを使用してサービスを初期化

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.xxx_service import XxxService

class TestXxxService:
    @pytest.mark.unit
    async def test_xxx(self, db_session: AsyncSession):
        service = XxxService(db_session)
        result = await service.xxx()
        assert result is not None
```
