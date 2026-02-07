# Workspace Isolation Design Document

> **Status**: Draft
> **Created**: 2026-02-07
> **Target**: 本番運用に耐えうるワークスペース仮想環境隔離設計

## 目的

本システムのワークスペース機能に対し、**仮想環境としての隔離**を導入する。
Claude Agent SDK が実行するプロセスは、ファイルシステム操作・コマンド実行・`pip install` 等を含むため、
テナント間の安全性・ホストシステムの保護・破壊的コマンドへの防御が不可欠である。

## ドキュメント構成

| # | ファイル | 内容 |
|---|---------|------|
| 1 | [01-current-architecture.md](./01-current-architecture.md) | 現行アーキテクチャ分析 — 現在の実行フロー・ワークスペース構造・リスク評価 |
| 2 | [02-isolation-strategy.md](./02-isolation-strategy.md) | 隔離戦略の設計 — 技術選定・コンテナ設計・ネットワーク/FS/プロセス隔離 |
| 3 | [03-security-hardening.md](./03-security-hardening.md) | セキュリティ強化 — seccomp/AppArmor、破壊的コマンド防御、pip 安全化 |
| 4 | [04-implementation-plan.md](./04-implementation-plan.md) | 実装計画 — フェーズ分割・コード変更箇所・マイグレーション戦略 |
| 5 | [05-production-operations.md](./05-production-operations.md) | 本番運用 — スケーリング・監視・障害復旧・コスト分析 |

## 設計原則

1. **Defense in Depth（多層防御）**: 単一の隔離技術に依存せず、複数層を重ねる
2. **最小権限の原則**: エージェントに必要最小限の権限のみを付与する
3. **Ephemeral by Default（一時性）**: サンドボックスは使い捨て、状態は外部（S3）に永続化
4. **既存アーキテクチャとの整合性**: 現行の S3 ワークスペースフローを活かす
5. **段階的導入**: 一度にすべてを変更せず、フェーズごとに安全に導入する

## スコープ

### In Scope
- Claude Agent SDK プロセスの仮想環境隔離
- `pip install` 等の Python 環境操作の安全化
- `rm -rf /` 等の破壊的コマンドに対するホスト保護
- テナント間・会話間のプロセス/ファイルシステム隔離
- ネットワークアクセス制御
- リソース制限（CPU / メモリ / ディスク / PID）

### Out of Scope
- フロントエンド UI の変更
- 認証/認可基盤の変更
- S3 ストレージ構造の変更
