# Workspace Isolation Design - Overview

## 背景

現在のシステムでは、1つのバックエンドコンテナ内で全ての Claude Agent SDK セッションが実行されている。
ワークスペースは S3 ベースのファイル管理のみで、プロセス・ファイルシステム・ネットワークの隔離がない。

本設計では、**会話ごとに専用コンテナを割り当て**、仮想環境としての完全な隔離を実現する。

## 設計目標

| 目標 | 説明 |
|------|------|
| プロセス隔離 | 各 Agent セッションが独立したコンテナで実行される |
| ファイルシステム隔離 | `pip install` や生成ファイルが他セッションに影響しない |
| ネットワーク隔離 | コンテナから内部サービス（DB、Redis等）へのアクセスを制限 |
| 破壊的コマンド耐性 | `rm -rf /` やフォークボムに対する防御 |
| Python 環境の柔軟性 | ユーザーが `pip install` で自由にライブラリを追加可能 |
| 本番運用の安定性 | スケーリング、障害復旧、コスト効率 |

## アーキテクチャ概要

```
┌─────────────────────────────────────────────────────────┐
│                    Backend (FastAPI)                     │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ API Layer│  │ Container    │  │ Warm Pool        │  │
│  │          │──│ Orchestrator │──│ Manager          │  │
│  └──────────┘  └──────┬───────┘  └──────────────────┘  │
└─────────────────────────┼───────────────────────────────┘
                          │ Docker API
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
   │ Workspace   │ │ Workspace   │ │ Workspace   │
   │ Container A │ │ Container B │ │ Container C │
   │             │ │             │ │             │
   │ Claude SDK  │ │ Claude SDK  │ │ Claude SDK  │
   │ Python venv │ │ Python venv │ │ Python venv │
   │ User files  │ │ User files  │ │ User files  │
   └─────────────┘ └─────────────┘ └─────────────┘
```

## ドキュメント構成

| ファイル | 内容 |
|---------|------|
| [01-current-architecture.md](./01-current-architecture.md) | 現在のアーキテクチャ分析 |
| [02-container-design.md](./02-container-design.md) | コンテナ隔離設計 |
| [03-lifecycle-management.md](./03-lifecycle-management.md) | ライフサイクル管理（TTL、Warm Pool） |
| [04-security.md](./04-security.md) | セキュリティ設計 |
| [05-storage-strategy.md](./05-storage-strategy.md) | ストレージ戦略 |
| [06-production-operations.md](./06-production-operations.md) | 本番運用（スケーリング、監視、コスト） |

## 設計判断サマリ

### Q1: pip install の結果を S3 に保存すべきか？

**→ No。S3 には保存しない。**

- `pip install` の結果はコンテナ内のエフェメラルストレージに留める
- コンテナ破棄時にライブラリも一緒に破棄される（これが正しい動作）
- S3 に保存すると、バージョン競合・セキュリティリスク・ストレージコスト増大の問題が発生する
- 代わりに、よく使うライブラリはベースイメージにプリインストールする（Q3 参照）

### Q2: TTL はどのくらいが適切か？

**→ 1時間（非アクティブベース）が妥当。**

| 比較対象 | TTL |
|----------|-----|
| AWS Lambda | 15〜45分 |
| E2B Sandbox | 最大24時間 |
| GitHub Codespaces | 30分（デフォルト） |
| **本システム推奨** | **1時間（非アクティブ）/ 最大8時間（絶対上限）** |

- AI エージェントの会話は断続的だが、数十分の間隔で再開されることが多い
- 1時間の非アクティブ TTL で、大半のユーザーセッションをカバーできる
- 絶対上限（8時間）を設けて、放置コンテナのリソース消費を防ぐ

### Q3: 重要な Python ライブラリをグローバルに入れるべきか？

**→ Yes。ベースイメージにプリインストールする。**

プリインストール推奨ライブラリ:

```
# データ分析
numpy, pandas, scipy, scikit-learn, statsmodels

# 可視化
matplotlib, seaborn, plotly

# ファイル処理
openpyxl, python-docx, pymupdf, Pillow, csv(標準)

# Web / API
requests, httpx, beautifulsoup4, lxml

# ユーティリティ
pyyaml, python-dotenv, tqdm, rich
```

**メリット:**
- コールドスタート時の `pip install` 待ち時間を削減
- 一般的なデータ分析タスクが即座に実行可能
- Agent が `pip install numpy` のような定番コマンドで時間を浪費しない

**ベースイメージに入れつつ、ユーザーが `pip install` で追加も可能** という構成がベスト。
