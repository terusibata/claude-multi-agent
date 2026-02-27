---
name: docx-reader
description: Word (.docx) ファイルの読み取り・解析スキル。Word文書からテキスト、見出し構造、表、メタデータを抽出し、段落範囲指定・見出しジャンプ・キーワード検索にも対応する。AI向けコンパクト出力をデフォルトとし、トークン消費を最小限に抑えつつ文書の構造を完全に理解できる形式で返す。ユーザーがWordファイルを添付して「内容を教えて」「読み取って」「中身を確認して」「文書の内容は？」「○○について書いてある箇所は？」などと言った場合にこのスキルを使用する。Wordの閲覧、解析、テキスト抽出、検索に関するあらゆるリクエストで発動すること。
---

# DOCX Reader Skill (AI最適化版)

Word (.docx) ファイルを解析し、AI（LLM）が効率的に理解できる構造化出力を返すスキル。

## 依存関係

- Python 3 + `python-docx`（初回実行時に自動インストール）

## コマンド

スクリプトパス: `<このスキルのディレクトリ>/scripts/read_docx.py`

```bash
# デフォルト: AI向けコンパクト出力（50段落まで）
python3 scripts/read_docx.py <docx_file>

# 段落範囲指定
python3 scripts/read_docx.py <docx_file> --paragraphs 1-50
python3 scripts/read_docx.py <docx_file> --paragraphs 20,30,40

# 見出しジャンプ（見出しテキストを部分一致で検索し、そのセクション内容を表示）
python3 scripts/read_docx.py <docx_file> --heading "第3章"

# キーワード検索（段落＋表を横断）
python3 scripts/read_docx.py <docx_file> --search "キーワード"

# 見出し構造と概要のみ
python3 scripts/read_docx.py <docx_file> --summary

# 最大段落数を変更
python3 scripts/read_docx.py <docx_file> --max-paragraphs 100

# 出力形式の切替
python3 scripts/read_docx.py <docx_file> --format ai      # デフォルト
python3 scripts/read_docx.py <docx_file> --format human    # 人間向け
python3 scripts/read_docx.py <docx_file> --format json     # JSON (空値省略)
```

## AI向け出力フォーマットの設計思想

デフォルトの `--format ai` は以下の原則で設計されている:

1. **2層構造**: ヘッダー（メタ+見出し構造）→ 本文詳細。まず全体像を掴み、必要な箇所だけ読める
2. **見出し階層表示**: H1/H2/H3で見出しレベルを明示し、段落番号を付記
3. **空値完全省略**: 空段落はスキップ
4. **表の構造化表示**: 表はパイプ区切りで行列表示
5. **装飾なし**: 絵文字、罫線を排除し情報密度を最大化

### 出力例

```
[DOCX] 提案書.docx | 120段落 | 15,200字 | 表3個
[目次] H1:はじめに(P1) / H1:現状分析(P15) / H2:課題(P22) / H1:提案内容(P45)

== H1: はじめに (P1-P14, 約2,400字)
  本提案書は2024年度の事業計画について...
  背景として、昨年度の実績を踏まえ...

== H2: 課題 (P22-P44)
  主要な課題を以下に整理する。
  [表1] 3行x4列
    H| 課題 | 影響度 | 優先度 | 対策
    1| レスポンス遅延 | 高 | A | キャッシュ導入
    2| データ整合性 | 中 | B | バリデーション強化
```

## ワークフロー

1. Wordファイルが添付されたら `/mnt/user-data/uploads/` のパスを確認
2. まず `--summary` で見出し構造と概要を確認
3. 必要なセクションを `--heading` で指定して詳細を取得
4. 大量段落の場合は `--paragraphs` で範囲を絞る
5. 特定の内容を探す場合は `--search` を使用
6. ユーザーに結果をまとめて返答する

## 注意事項

- `.doc`（旧形式）は非対応。`.docx`（Office Open XML）形式のみ対応
- 画像の中身（OCR）は非対応。画像の存在のみ検出
- ヘッダー/フッター/コメントは現在非対応
- テキストボックス内のテキストは一部取得できない場合がある
