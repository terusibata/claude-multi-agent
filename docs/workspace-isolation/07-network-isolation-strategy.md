# Workspace Container Isolation - 実装方針

## アーキテクチャ

会話ごとに `--network none` の Docker コンテナを割り当てる。
コンテナ内に Claude Agent SDK + CLI を配置し、ホスト側の Unix Socket Proxy 経由でのみ外部通信する。

```
Host (Backend FastAPI)
  ├─ ContainerOrchestrator (aiodocker)
  ├─ WarmPool (Redis)
  └─ CredentialInjectionProxy (per container)
       │ Unix Socket
       ▼
  Container (--network none)
    ├─ Claude Agent SDK + CLI
    ├─ Python venv (プリインストール済み)
    └─ /workspace (ユーザーファイル)
```

## コンテナ設定

```python
{
    "NetworkMode": "none",
    "ReadonlyRootfs": True,
    "User": "1000:1000",
    "CapDrop": ["ALL"],
    "CapAdd": ["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE"],
    "SecurityOpt": ["no-new-privileges:true"],
    "Memory": 2 * 1024**3,        # 2GB
    "MemorySwap": 2 * 1024**3,    # swap無効
    "CpuPeriod": 100000,
    "CpuQuota": 200000,           # 2cores
    "PidsLimit": 100,
    "IpcMode": "private",
    "Tmpfs": {
        "/tmp": "rw,noexec,nosuid,size=512M",
        "/var/tmp": "rw,noexec,nosuid,size=256M",
        "/run": "rw,noexec,nosuid,size=64M",
        "/home/appuser/.cache": "rw,noexec,nosuid,size=512M",
        "/home/appuser": "rw,noexec,nosuid,size=64M",
    },
    "StorageOpt": {"size": "5G"},
}
```

コンテナ環境変数:
```
HTTP_PROXY=http+unix:///var/run/proxy.sock
HTTPS_PROXY=http+unix:///var/run/proxy.sock
NODE_USE_ENV_PROXY=1
```
AWS認証情報は**渡さない**。Proxy側で注入する。

## ネットワーク

`--network none` でネットワークIF自体を排除。
全外部通信は `HTTP_PROXY` → Unix Socket → ホスト側 Proxy 経由。
curl, pip, npm, requests, httpx 等は全て `HTTP_PROXY` を尊重するため動作する。
Node.js fetch() は `NODE_USE_ENV_PROXY=1`（v24+）で対応。v20の場合は `global-agent` を使用。

### Proxy の役割
- ドメインホワイトリスト適用（PyPI, npmjs, Bedrock API等のみ許可）
- Bedrock APIリクエストにAWS SigV4署名を注入
- 全リクエストを監査ログに記録
- HTTPS は CONNECT + TLSパススルー（MITMなし）

### ドメイン制御
- API/パッケージのみ必要 → ホワイトリスト方式
- Agent にWeb閲覧能力が必要 → ブラックリスト方式（内部NW + メタデータのみブロック）

## ストレージ

| レイヤー | 場所 | 永続性 |
|---------|------|--------|
| ベースイメージ | Python 3.11 + Node.js 20 + プリインストールライブラリ | 永続（イメージ） |
| pip install追加分 | /opt/venv (Docker Volume) | コンテナ破棄で消滅 |
| ユーザーファイル | /workspace (Docker Volume) | S3に同期 |
| 一時ファイル | /tmp (tmpfs, noexec) | コンテナ破棄で消滅 |

S3同期タイミング: コンテナ起動時（S3→Container）、Agent実行完了時（Container→S3）、コンテナ破棄時（最終同期）。

## ライフサイクル

| パラメータ | 値 |
|-----------|-----|
| 非アクティブTTL | 60分 |
| 絶対TTL | 8時間 |
| 実行タイムアウト | 10分 |
| Warm Pool最小 | 2 |
| Warm Pool最大 | 10 |

コンテナ状態は Redis で管理。GCループが60秒ごとにTTL超過コンテナを破棄。
破棄前にS3同期 + 30秒のグレースピリオド。

## ベースイメージ プリインストール

```
numpy pandas scipy scikit-learn statsmodels
matplotlib seaborn plotly
openpyxl python-docx pymupdf Pillow
requests httpx beautifulsoup4 lxml
pyyaml python-dotenv tqdm rich
fastapi uvicorn[standard]
claude-agent-sdk
```
Node.js側: `@anthropic-ai/claude-code`, `global-agent`（Node.js 20の場合）

## 実装順序

1. ベースイメージ作成
2. Credential Injection Proxy（Unix Socket, ドメイン制御, SigV4注入）
3. ContainerOrchestrator（作成・破棄・Unix Socket通信）
4. S3ファイル同期
5. TTL + GC
6. WarmPool
7. 監視・アラート