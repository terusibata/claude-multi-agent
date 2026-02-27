---
name: image-reader
description: 画像ファイルのメタデータ解析スキル。JPEG/PNG/GIF/WebP/BMP/TIFF/SVG画像からサイズ、解像度、カラーモード、EXIF情報（カメラ、撮影条件、GPS等）を抽出する。ユーザーが画像ファイルについて「画像の情報を教えて」「解像度は？」「サイズは？」「EXIF情報は？」「カメラは何？」「撮影日は？」などと言った場合にこのスキルを使用する。画像のメタデータ取得・解析に関するあらゆるリクエストで発動すること。画像の視覚的な確認（中身を見る）は read_image_file MCPツールを使用すること。
---

# Image Reader Skill (メタデータ解析)

画像ファイルのメタデータを解析し、AI（LLM）が効率的に理解できる構造化出力を返すスキル。

## 依存関係

- Python 3 + `Pillow`（初回実行時に自動インストール）

## コマンド

スクリプトパス: `<このスキルのディレクトリ>/scripts/read_image.py`

```bash
# デフォルト: AI向けコンパクト出力
python3 scripts/read_image.py <image_file>

# 詳細EXIF情報
python3 scripts/read_image.py <image_file> --exif

# 出力形式の切替
python3 scripts/read_image.py <image_file> --format ai      # デフォルト
python3 scripts/read_image.py <image_file> --format human    # 人間向け
python3 scripts/read_image.py <image_file> --format json     # JSON (空値省略)
```

## AI向け出力フォーマットの設計思想

デフォルトの `--format ai` は以下の原則で設計されている:

1. **1行凝縮**: 基本情報を1行にまとめ、即座に全体像を把握可能
2. **EXIF要約**: 撮影条件を1行に凝縮（カメラ、F値、シャッター速度、ISO）
3. **空値完全省略**: 情報がないフィールドは一切出力しない

### 出力例

```
[IMG] photo.jpg | 4032x3024 | JPEG | sRGB | 72DPI | 3.2MB
[EXIF] カメラ:iPhone 15 Pro | F1.78 | 1/120s | ISO64 | 2024-04-01 14:30:00
```

## ワークフロー

1. 画像ファイルが添付されたら `/mnt/user-data/uploads/` のパスを確認
2. まずこのスキルでメタデータを確認
3. 画像の中身を視覚的に確認したい場合は `read_image_file` MCPツールを使用
4. ユーザーに結果をまとめて返答する

## 注意事項

- このスキルはメタデータ取得のみ。画像の中身（視覚的確認）は `read_image_file` MCPツールを使用
- EXIF情報は写真ファイル（JPEG/TIFF）にのみ存在。PNG/GIF/WebP等には通常含まれない
- GPS情報が含まれている場合は表示する（プライバシー注意）
- SVGファイルはテキストベースのため、viewBoxサイズのみ取得
