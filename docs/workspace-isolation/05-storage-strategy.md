# 05 - ストレージ戦略

## ストレージ層の設計

```
┌─────────────────────────────────────────────────────────┐
│                 Storage Layers                           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Layer 1: ベースイメージ (Read-Only)                     │
│  ├─ Python 3.11 + Node.js 20                           │
│  ├─ プリインストール済みライブラリ (/opt/venv)            │
│  └─ Claude Agent SDK                                    │
│                                                         │
│  Layer 2: エフェメラルストレージ (コンテナ内)              │
│  ├─ pip install 追加分 (/opt/venv)     ← 破棄される     │
│  ├─ ユーザー作業ファイル (/workspace)   ← S3 に同期     │
│  └─ 一時ファイル (/tmp)                ← 破棄される     │
│                                                         │
│  Layer 3: 永続ストレージ (S3)                            │
│  ├─ ユーザーアップロードファイル                          │
│  ├─ AI 生成ファイル（完了時に同期）                       │
│  └─ メタデータは PostgreSQL                              │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## pip install の扱い

### 方針: コンテナ内に閉じる（S3 に保存しない）

| 選択肢 | メリット | デメリット | 採用 |
|--------|---------|-----------|------|
| **S3 に保存** | セッション再開時に復元可能 | バージョン競合、セキュリティリスク、コスト増 | × |
| **コンテナ内のみ** | シンプル、安全、クリーン | セッション再開時に再インストール必要 | **○** |
| **requirements.txt を S3 に保存** | 復元可能、軽量 | 再インストール時間が必要 | △ 将来検討 |

### 理由

1. **セキュリティ**: 悪意のあるパッケージが永続化されるリスクを排除
2. **シンプルさ**: venv の状態管理（バージョン、依存関係の木）は複雑
3. **コスト**: ライブラリの S3 保存は容量が大きく（numpy だけで 50MB+）、コスト効率が悪い
4. **クリーンスタート**: 各セッションがクリーンな状態から始まることで再現性を確保

### ベースイメージのプリインストールで補完

- よく使うライブラリ（numpy, pandas 等）はベースイメージに含める
- `pip install` が必要なケースは特殊なライブラリのみになる
- Agent は必要に応じて自動的に `pip install` を実行するため、UX への影響は最小限

## ファイル同期設計

### 同期タイミング

```
【コンテナ起動時】
  S3 → Container (/workspace)
  └─ ユーザーアップロードファイルをダウンロード

【エージェント実行完了時】
  Container (/workspace) → S3
  └─ AI 生成・変更ファイルをアップロード

【コンテナ破棄時】
  Container (/workspace) → S3 (最終同期)
  └─ 未同期ファイルがあればアップロード
```

### 同期方式

```python
class WorkspaceFileSync:
    async def sync_to_container(
        self,
        conversation_id: str,
        container_id: str,
    ):
        """S3 → コンテナへファイル同期"""
        files = await self.db.get_active_files(conversation_id)
        for file in files:
            s3_data = await self.s3.download(file.s3_key)
            await self.container.write_file(
                container_id,
                f"/workspace/{file.file_path}",
                s3_data,
            )

    async def sync_from_container(
        self,
        conversation_id: str,
        container_id: str,
    ):
        """コンテナ → S3 へ変更ファイル同期"""
        changed_files = await self.container.get_changed_files(
            container_id, "/workspace"
        )
        for file_path, content in changed_files:
            await self.s3.upload(
                key=f"workspaces/{conversation_id}/{file_path}",
                data=content,
            )
            await self.db.upsert_file_record(conversation_id, file_path)
```

### 変更検出

コンテナ内のファイル変更を検出する方法:

1. **inotify ベース**: コンテナ内で inotify でファイル変更を監視（リアルタイム）
2. **チェックサム比較**: 実行前後で `/workspace` のファイルハッシュを比較（シンプル）
3. **Agent SDK のツール結果解析**: Write/Edit ツールの結果からファイル変更を特定（確実）

**推奨: 方法 3（ツール結果解析）をメインとし、方法 2 をフォールバックとする。**

## S3 構成

### バケット構造（変更なし）

```
s3://{bucket}/
  └─ workspaces/
      └─ {tenant_id}/
          └─ {conversation_id}/
              ├─ uploads/           # ユーザーアップロード
              │   ├─ data.csv
              │   └─ image.png
              └─ workspace/         # AI 生成・変更ファイル
                  ├─ analysis.py
                  └─ output/
                      └─ result.json
```

### S3 ライフサイクルルール

| ルール | 条件 | アクション |
|--------|------|-----------|
| 古いバージョンの削除 | 30日経過 | 非最新バージョンを削除 |
| 非アクティブ会話 | 90日アクセスなし | Glacier に移行 |
| アーカイブ済み会話 | 会話 status = "archived" から 180日 | 削除 |
