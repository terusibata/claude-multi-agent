---
name: pdf-reader
description: PDF (.pdf) ファイルの読み取り・解析スキル。PDFファイルからテキスト、メタデータ、目次、ページ構造を抽出し、ページ範囲指定やキーワード検索にも対応する。AI向けコンパクト出力をデフォルトとし、トークン消費を最小限に抑えつつ文書の構造を完全に理解できる形式で返す。ユーザーがPDFファイルを添付して「内容を教えて」「読み取って」「中身を確認して」「ページの内容は？」「○○について書いてあるページは？」などと言った場合にこのスキルを使用する。PDFの閲覧、解析、テキスト抽出、検索に関するあらゆるリクエストで発動すること。
---

# PDF Reader Skill (AI最適化版)

PDF (.pdf) ファイルを解析し、AI（LLM）が効率的に理解できる構造化出力を返すスキル。

## 依存関係

- Python 3 + `PyMuPDF`（初回実行時に自動インストール）

## コマンド

スクリプトパス: `<このスキルのディレクトリ>/scripts/read_pdf.py`

```bash
# デフォルト: AI向けコンパクト出力（最大10ページ）
python3 scripts/read_pdf.py <pdf_file>

# ページ範囲指定
python3 scripts/read_pdf.py <pdf_file> --pages 1-5
python3 scripts/read_pdf.py <pdf_file> --pages 2,5,8

# キーワード検索
python3 scripts/read_pdf.py <pdf_file> --search "キーワード"

# 概要のみ（大量ページ時の全体把握に有効）
python3 scripts/read_pdf.py <pdf_file> --summary

# ページを画像としてファイル保存（図表確認用）
python3 scripts/read_pdf.py <pdf_file> --pages 1-3 --images
python3 scripts/read_pdf.py <pdf_file> --pages 1 --images --dpi 200

# 出力形式の切替
python3 scripts/read_pdf.py <pdf_file> --format ai      # デフォルト
python3 scripts/read_pdf.py <pdf_file> --format human    # 人間向け
python3 scripts/read_pdf.py <pdf_file> --format json     # JSON (空値省略)
```

## AI向け出力フォーマットの設計思想

デフォルトの `--format ai` は以下の原則で設計されている:

1. **2層構造**: ヘッダー（メタ+目次+概要）→ 各ページ詳細。まず全体像を掴み、必要なページだけ読める
2. **空値完全省略**: 情報がないフィールドは一切出力しない
3. **装飾なし**: 絵文字、罫線、冗長なラベルを排除し情報密度を最大化
4. **文字数表示**: 各ページの文字数を表示し、図表主体ページを識別可能に

### 出力例

```
[PDF] 事業報告書 | 42p | 作成:山田太郎 | 更新:2024-04-01
[目次] 1.はじめに(P1) / 2.事業概要(P5) / 3.財務報告(P20)
[概要] P1:2450字 / P2:1820字 / P3:120字(図表主体) / ...

== P1
  はじめに
  本報告書は2024年度の事業活動について...

== P2
  1. 会社概要
  設立: 2010年4月
  従業員数: 350名
```

## ワークフロー

1. PDFファイルが添付されたら `/mnt/user-data/uploads/` のパスを確認
2. 大量ページ（10ページ以上）の場合はまず `--summary` で概要を確認
3. 必要に応じて `--pages` で範囲を絞って詳細を取得
4. 特定の内容を探す場合は `--search` を使用
5. 図表やレイアウトの確認が必要な場合は `--images` でページ画像を保存
6. ユーザーに結果をまとめて返答する

## 注意事項

- スキャンPDF（画像のみ）のテキスト抽出は不可。OCRは非対応
- 暗号化・パスワード保護されたPDFは読み取れない場合がある
- `--images` で保存した画像は `read_image_file` MCPツールで視覚的に確認可能
