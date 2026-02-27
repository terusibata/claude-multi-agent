---
name: excel-reader
description: Excel (.xlsx) ファイルの読み取り・解析スキル。Excelファイルからシート情報、セルデータ、数式結果を抽出し、シート指定・行範囲指定・キーワード検索にも対応する。AI向けコンパクトCSV出力をデフォルトとし、トークン消費を最小限に抑えつつスプレッドシートの内容を完全に理解できる形式で返す。ユーザーがExcelファイルを添付して「内容を教えて」「読み取って」「中身を確認して」「シートの内容は？」「○○のデータを探して」などと言った場合にこのスキルを使用する。Excelの閲覧、解析、データ抽出、検索に関するあらゆるリクエストで発動すること。
---

# Excel Reader Skill (AI最適化版)

Excel (.xlsx) ファイルを解析し、AI（LLM）が効率的に理解できる構造化出力を返すスキル。

## 依存関係

- Python 3 + `openpyxl`（初回実行時に自動インストール）

## コマンド

スクリプトパス: `<このスキルのディレクトリ>/scripts/read_excel.py`

```bash
# デフォルト: AI向けコンパクト出力（最初のシート、100行まで）
python3 scripts/read_excel.py <xlsx_file>

# シート指定
python3 scripts/read_excel.py <xlsx_file> --sheet "Sheet1"

# 行範囲指定
python3 scripts/read_excel.py <xlsx_file> --rows 1-100
python3 scripts/read_excel.py <xlsx_file> --rows 50,60,70

# キーワード検索（全シート横断）
python3 scripts/read_excel.py <xlsx_file> --search "キーワード"

# シート一覧と概要のみ
python3 scripts/read_excel.py <xlsx_file> --summary

# 最大行数を変更
python3 scripts/read_excel.py <xlsx_file> --max-rows 200

# 出力形式の切替
python3 scripts/read_excel.py <xlsx_file> --format ai      # デフォルト
python3 scripts/read_excel.py <xlsx_file> --format human    # 人間向け
python3 scripts/read_excel.py <xlsx_file> --format json     # JSON (空値省略)
```

## AI向け出力フォーマットの設計思想

デフォルトの `--format ai` は以下の原則で設計されている:

1. **2層構造**: ヘッダー（シート一覧+次元情報）→ データ本体。まず全体像を掴み、必要なシートだけ読める
2. **CSV形式**: データはRFC 4180準拠のCSV形式で出力。LLMが最も効率的に解釈可能
3. **空値完全省略**: 空行・空列はスキップ
4. **マージセル対応**: マージされたセルは展開して全セルに値を表示
5. **残行通知**: 表示しきれない行がある場合は残行数と取得コマンドを提示

### 出力例

```
[XLSX] 売上レポート.xlsx | 3シート
[シート] S1:月別売上(150行x8列) / S2:商品別(45行x5列) / S3:グラフデータ(12行x3列)

== S1: 月別売上 (150行x8列, 表示:1-100行)
月,売上高,前年比,営業利益,営業利益率,経常利益,純利益,備考
1月,105億,102%,12億,11.4%,11億,8億,
2月,98億,98%,11億,11.2%,10億,7億,
--- 残り50行。取得: --rows "101-150" ---
```

## ワークフロー

1. Excelファイルが添付されたら `/mnt/user-data/uploads/` のパスを確認
2. まず `--summary` でシート一覧と概要を確認
3. 必要なシートを `--sheet` で指定してデータを取得
4. 大量行の場合は `--rows` で範囲を絞る
5. 特定の内容を探す場合は `--search` を使用
6. ユーザーに結果をまとめて返答する

## 注意事項

- `.xls`（旧形式）は非対応。`.xlsx`（Office Open XML）形式のみ対応
- 数式はその結果値を返す（数式そのものは返さない）
- グラフ・画像の内容は取得できない
- 非常に大きなファイル（100MB超）はメモリ不足の可能性あり
