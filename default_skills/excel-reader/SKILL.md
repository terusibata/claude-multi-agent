# Excel Reader

Excelファイル(.xlsx)を読み取り、内容を分析するスキルです。

## 使い方

ユーザーがExcelファイルの読み取りや分析を依頼した場合、以下のPythonスクリプトを使用してください。

### シート一覧・基本情報の取得

```bash
python3 /workspace/.claude/skills/excel-reader/read_excel.py info <file_path>
```

### シートデータの取得（CSV形式）

```bash
python3 /workspace/.claude/skills/excel-reader/read_excel.py csv <file_path> [--sheet <sheet_name>] [--start-row <n>] [--end-row <n>]
```

### ワークブック全体のキーワード検索

```bash
python3 /workspace/.claude/skills/excel-reader/read_excel.py search <file_path> <query> [--case-sensitive]
```

## 注意事項

- 対応形式: `.xlsx`（Office Open XML）のみ。`.xls`（レガシーバイナリ形式）は非対応
- 大きなファイルの場合は `--start-row` / `--end-row` で範囲を絞って取得してください
- 出力はUTF-8テキストです
