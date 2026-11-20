# REQ-011: 保存ファイル形式の拡充（TIFF / WebP / BMP / GIF 等の一般画像形式対応）

- **ステータス**: `未着手`
- **優先ターゲット**: Brother × macOS（クロスプラットフォーム・汎用eSCL拡張を考慮）
- **元要件（原案）**: [`docs/requirements/drafts/20261120.md`](./drafts/20261120.md)
- **依存する先行要件**: [`REQ-004`](./REQ-004-scan-save-and-continuous-workflow.md)

---

## 1. 背景・目的

現在の保存ファイル形式は `PDF`・`JPEG`・`PNG` の 3 種類のみとなっている。実務や用途に応じてアーカイブ向けのロスレス圧縮（`TIFF`）・Web共有や軽量保存向けの次世代フォーマット（`WebP`）・Windows/汎用ツール互換（`BMP`）や簡易画像（`GIF`）など、一般的な画像ファイル形式を一通り選択してそのまま保存できるようにしたい。

## 2. 要件概要とスコープ

### 実施すること（In Scope）
- `VALID_SAVE_FORMATS` および拡張子マッピングを拡張し、既存の `PDF`・`JPEG`・`PNG` に加えて **`TIFF`・`WebP`・`BMP`・`GIF`** を正式サポートする
- `ImageExporter`（`src/open_escl_client/core/exporter.py`）および `encode_image_bytes`（`multi_crop.py`）で、各形式に適した色空間変換（RGBA→RGB 等）、DPI メタデータ埋め込み（`TIFF` / `WebP` / `PNG` / `JPEG` / `PDF`）、圧縮品質パラメータ（`JPEG` / `WebP`）、およびマルチページ保存（`PDF` に加えマルチページ `TIFF` 結合）に対応する
- `SaveSettingsPanel.qml` と `AppViewModel` で新規フォーマットを選択でき、選択中フォーマットに応じて圧縮品質スライダーやマルチページ結合オプションの表示／有効状態が適切に切り替わるようにする

### 今回のブランチでは実施しないこと（Out of Scope / 別要件へ分割）
- 外部バイナリ依存が必要な特殊 RAW / HEIC 専用エンコーダの追加（将来拡張とし、まずは Pillow 標準で macOS/Windows 双方にて確実に動作する汎用形式を網羅する）

## 3. 完了条件（Acceptance Criteria）

- [ ] 保存形式ドロップダウンから `PDF`, `JPEG`, `PNG`, `TIFF`, `WebP`, `BMP`, `GIF` を選択でき、それぞれの正しい拡張子で保存されること
- [ ] `JPEG` / `WebP` 選択時は圧縮品質が反映され、`TIFF` 選択時は DPI メタデータ（およびマルチページ選択時は複数ページ TIFF）が正しく出力されること
- [ ] Brother × macOS 環境（またはモック検証）で意図通り動作すること
- [ ] Windows や汎用 eSCL 機器への将来対応を阻害するハードコードが含まれていないこと

---

## 4. 提案 Git ブランチ名

```text
feature/expanded-export-formats
```

---

## 5. タスク一覧（1タスク ＝ 1コミット）

### タスク 1: `UserConfig`・`FileNamingEngine`・`ImageExporter` の画像出力形式拡張

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/config/user_config.py` の `VALID_SAVE_FORMATS` と `normalize_save_format` を拡張し、`PDF`, `JPEG`, `PNG`, `TIFF`, `WebP`, `BMP`, `GIF`（別名 `.tif` → `TIFF` 等の正規化含む）をサポートする
  - `src/open_escl_client/core/naming.py` の `_FORMAT_TO_EXTENSION` を全形式（`.pdf`, `.jpg`, `.png`, `.tif`, `.webp`, `.bmp`, `.gif`）に対応させる
  - `src/open_escl_client/core/exporter.py` の `ImageExporter`（および `continuous_scan.py` のマルチページ保存判定）で、`TIFF`（LZW 圧縮＋DPI＋マルチページ TIFF 結合）、`WebP`（`quality`＋DPI/EXIF）、`BMP`、`GIF`（パレット変換）の単ページ／複数ページ出力を実装する
  - `src/open_escl_client/imaging/multi_crop.py` の `encode_image_bytes` と `MultiCropPipelineConfig.output_format` も全形式に対応させる
  - `tests/core/test_exporter.py`、`tests/core/test_naming.py`、`tests/config/test_user_config.py` に新規フォーマット出力・DPI・マルチページ TIFF の単体テストを追加する
- **提案コミットメッセージ**:

```text
feat(core,imaging): 保存ファイル形式にTIFF・WebP・BMP・GIFを追加し各形式の品質・DPI出力を実装

## 背景・目的
PDF・JPEG・PNG に加えて一般的な画像フォーマット（TIFF / WebP / BMP / GIF）を網羅し、アーカイブ用途から軽量保存まで柔軟に保存形式を選べるようにするため（REQ-011 タスク1）。

## 変更内容
- `src/open_escl_client/config/user_config.py` と `src/open_escl_client/core/naming.py` の対応フォーマットおよび拡張子解決を `PDF / JPEG / PNG / TIFF / WebP / BMP / GIF` へ拡張
- `src/open_escl_client/core/exporter.py` で TIFF（LZW圧縮・DPI・マルチページ対応）、WebP（品質・DPI対応）、BMP、GIF の各形式出力を実装し、`MultiCropScanPipeline` とも連携
- `tests/core/test_exporter.py` / `test_naming.py` / `test_user_config.py` に各フォーマットの出力検証テストを追加

## 影響範囲・備考
- Pillow 標準APIを使用しているため macOS / Windows の両OSで追加バイナリなしに動作
```

---

### タスク 2: `AppViewModel` と `SaveSettingsPanel.qml` の保存形式選択 UI 更新

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/ui/viewmodel.py` の `saveFormats` リスト、`showJpegQuality`（`JPEG` と `WebP` で有効化する `supportsQuality` 等）、`supportsMultipage`（`PDF` と `TIFF` でマルチページ結合トグルを有効化）、およびプレビュー保存ファイル名の更新処理を全形式へ対応させる
  - `src/open_escl_client/ui/qml/SaveSettingsPanel.qml` の形式選択コンボボックスと品質スライダー・マルチページ結合チェックボックスの表示テキスト（例: `複数スキャンを1つのPDF/TIFFファイルに結合`）を更新する
  - `tests/ui/test_qml_load.py` に新規フォーマット切り替え時の品質スライダー・マルチページ設定連動・保存実行テストを追加する
- **提案コミットメッセージ**:

```text
feat(ui): 保存設定パネルのファイル形式選択肢を拡充し品質・マルチページUI連携を更新

## 背景・目的
バックエンドで拡張した全画像ファイル形式（TIFF / WebP / BMP / GIF）をUIから選択でき、形式ごとの品質設定やマルチページ結合設定が直感的に連動するようにするため（REQ-011 タスク2）。

## 変更内容
- `src/open_escl_client/ui/viewmodel.py` の保存形式候補・品質対応判定（JPEG / WebP）・マルチページ対応判定（PDF / TIFF）を更新
- `src/open_escl_client/ui/qml/SaveSettingsPanel.qml` の表示条件とラベルを拡張フォーマットに合わせて更新
- `tests/ui/test_qml_load.py` に TIFF / WebP / BMP / GIF 選択時のUI連動およびファイル出力テストを追加

## 影響範囲・備考
- 対象OS: macOS / Windows 共通
```
