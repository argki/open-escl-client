# REQ-013: 検索可能 PDF（透明テキスト埋め込み PDF）生成機能の実装

- **ステータス**: `未着手`
- **優先ターゲット**: Brother × macOS（クロスプラットフォーム・汎用eSCL拡張を考慮）
- **元要件（原案）**: [`REQ-012`](./REQ-012-high-accuracy-japanese-ocr-and-naming.md)（スコープ外事項からの切り出し）
- **依存する先行要件**: [`REQ-012`](./REQ-012-high-accuracy-japanese-ocr-and-naming.md)

---

## 1. 背景・目的

[`REQ-012`](./REQ-012-high-accuracy-japanese-ocr-and-naming.md) によりローカル完結で高精度な日本語 OCR（macOS Apple Vision Framework 等）とファイル名自動抽出機能が実装されたが、保存される PDF ファイル自体は現状 `img2pdf` によるラスター画像のみの構成となっている。

スキャンした請求書・領収書・通知書・契約書などを長期アーカイブする際、PDF ビューア（macOS プレビュー、Spotlight、Adobe Acrobat 等）上で**本文テキストをキーワード検索・選択・コピーできる「検索可能 PDF（Searchable PDF / 透明テキストレイヤー付き PDF）」** を生成できるようにすることで、文書管理の利便性を大幅に向上させる。

---

## 2. 要件概要とスコープ

### 実施すること（In Scope）
- 既存の `JapaneseOcrEngine` が返す `OcrResult`（各 `OcrTextBlock` のテキストと正規化座標 `OcrBoundingBox`）を利用し、スキャン画像ページの上に位置合わせした透明テキストレイヤーを重ねて PDF を構築する**検索可能 PDF 生成エンジン**の実装
- 単ページ PDF および複数ページ連続スキャン PDF の双方での透明テキスト埋め込み対応
- 日本語（全角かな・漢字・英数字）テキストを文字化けなく検索・コピー可能に埋め込むフォント／描画処理と、OS 間の互換性（macOS 優先実装＋Windows 等向けクロスプラットフォーム生成パス）の両立
- `UserConfig`、`ExportOptions`、`MultiCropScanPipeline`、`ContinuousScanSession`、`AppViewModel`、および `SaveSettingsPanel.qml` への「検索可能 PDF を生成」設定トグルの統合
- OCR 結果が空・失敗・無効時であっても通常の画像 PDF（既存 `img2pdf` 出力）へ安全にフォールバックし、保存を失敗させない設計

### 今回のブランチでは実施しないこと（Out of Scope / 別要件へ分割）
- 既存 PDF ファイルを後から開いて再 OCR・テキスト埋め込みを行うバッチ変換ツール（別要件で検討）
- 外部クラウド OCR API を用いた検索可能 PDF 生成（[`REQ-014`](./REQ-014-optional-cloud-ocr-backends.md) のバックエンド統合後に自動連携可能）

## 3. 完了条件（Acceptance Criteria）

- [ ] スキャンした日本語文書を「検索可能 PDF」有効状態で保存した際、見た目は元のスキャン画像のまま、PDF ビューアおよび PDF パーサーで日本語テキストの検索・選択・抽出ができること
- [ ] 単ページ PDF（一括切り出し保存含む）およびマルチページ PDF の全ページに対して、各ページの画像サイズ・DPI に合わせた位置に透明テキストが埋め込まれること
- [ ] OCR 未実行・テキスト未検出・エラー発生時や PDF 以外の保存形式（JPEG/PNG/TIFF 等）選択時は、既存保存フローを阻害せず安全に保存完了すること
- [ ] Brother × macOS 環境（またはモック検証・実フォント描画テスト）で日本語テキスト検索が意図通り動作すること
- [ ] Windows や汎用 eSCL 機器への将来対応を阻害する OS 固有ハードコード（macOS 専用実装への無条件依存）がコアインターフェースに漏れていないこと

---

## 4. 提案 Git ブランチ名

```text
feature/searchable-pdf-ocr-embedding
```

---

## 5. タスク一覧（1タスク ＝ 1コミット）

### タスク 1: 検索可能 PDF 生成エンジンと透明テキスト埋め込み基盤の実装

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/core/searchable_pdf.py` を新設し、ページ画像バイト列・DPI・`OcrResult` のペアを受け取って透明テキストレイヤー付き PDF を生成する `SearchablePdfBuilder` およびバックエンド抽象化（macOS 向けネイティブ `PDFKit` / CoreGraphics 透明テキスト描画または日本語対応クロスプラットフォーム PDF テキスト合成）を定義する
  - 各 `OcrTextBlock` の正規化バウンディングボックス（左上原点 `[0.0, 1.0]`）を PDF 座標系（左下原点・ポイント単位、`dpi` に基づくページ寸法）へ正確に変換し、テキストレンダリングモード不可視（Rendering Mode 3 / `alpha=0`）で透明テキストを配置する
  - 日本語フォントの埋め込み／文字幅・高さスケーリングにより、macOS プレビューや標準 PDF 抽出ツールで日本語文字列が正しく取得できることを検証する
  - `tests/core/test_searchable_pdf.py` に座標変換・マルチページ生成・日本語テキスト埋め込み抽出確認（macOS PDFKit 実環境条件テスト＋モックテスト）を追加する
- **提案コミットメッセージ**:

```text
feat(core): 検索可能PDFの透明テキスト埋め込みビルダーを実装

## 背景・目的
REQ-013 の基盤として、既存の日本語 OCR 結果（`OcrResult`）のテキストとレイアウト座標を PDF のページ画像上へ不可視テキストレイヤーとして正確に重ね、検索・コピー可能な PDF を生成できるようにするため。

## 変更内容
- `src/open_escl_client/core/searchable_pdf.py` を追加し、画像データと `OcrResult` から透明テキスト付き PDF を生成する `SearchablePdfBuilder` とバックエンド抽象化を実装
- 左上原点正規化座標（`OcrBoundingBox`）から PDF ポイント座標系（左下原点・DPI 基準）への変換および不可視テキスト配置ロジックを実装
- 単ページおよび複数ページ PDF に対するテキスト検索・抽出を検証する単体テスト（`tests/core/test_searchable_pdf.py`）を追加

## 影響範囲・備考
- 対象 OS: macOS 最優先で検証しつつ、バックエンド分離により Windows 環境向けクロスプラットフォーム実装とも両立可能な構造
- 既存の `ImageExporter` は本タスク時点では変更せず、次タスクで統合する
```

---

### タスク 2: `ImageExporter`・スキャンパイプライン・連続スキャンセッションへの統合

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/core/exporter.py` の `ExportOptions` に `searchable_pdf: bool = False` を追加し、`ImageExporter.export_pages` でオプションの `ocr_results: Sequence[OcrResult | None] | None = None` を受け取れるようにする
  - 保存形式が `"PDF"` かつ `searchable_pdf=True` で有効な `OcrResult` がある場合は `SearchablePdfBuilder` を用いて検索可能 PDF を出力し、ビルダー失敗時や OCR 結果なしの場合は既存の `img2pdf` 画像 PDF 出力へ安全にフォールバックする
  - `src/open_escl_client/imaging/pipeline.py` の `MultiCropPipelineConfig` / `ProcessedDocument` に `ocr_result` 保持（または `ocr_searchable_pdf_enabled` に応じた OCR 実行保証）を追加し、`src/open_escl_client/core/continuous_scan.py` の `ScanSaveProfile`・`ContinuousScanSession` で単ページ保存およびマルチページ PDF 確定保存時に各ページの `OcrResult` を引き継ぐ
  - `tests/core/test_exporter.py`、`tests/core/test_continuous_scan.py`、`tests/imaging/test_scan_save_pipeline.py` に検索可能 PDF エクスポートおよびフォールバックテストを追加する
- **提案コミットメッセージ**:

```text
feat(scan,export): エクスポーターと保存パイプラインへ検索可能PDF出力を統合

## 背景・目的
タスク 1 で構築した検索可能 PDF 生成処理を実際の単ページ保存・切り出し一括保存・マルチページ連続スキャン保存のパイプラインに接続するため（REQ-013 タスク2）。

## 変更内容
- `ExportOptions`（`src/open_escl_client/core/exporter.py`）に `searchable_pdf` オプションを追加し、`ImageExporter.export_pages` でページごとの `OcrResult` を受け取って透明テキスト埋め込み PDF を生成できるよう拡張（失敗時は `img2pdf` へ自動フォールバック）
- `MultiCropScanPipeline`（`src/open_escl_client/imaging/pipeline.py`）で `ocr_result` を `ProcessedDocument` に保持し、OCR 命名無効時でも検索可能 PDF 有効時は OCR を実行するよう調整
- `ContinuousScanSession`（`src/open_escl_client/core/continuous_scan.py`）にマルチページ PDF 蓄積時のページ別 `OcrResult` 保持と保存連携を実装
- エクスポーター・パイプライン・連続スキャンセッションの結合テストを追加

## 影響範囲・備考
- 対象機能: 単ページ PDF 保存、マルチクロップ一括 PDF 保存、マルチページ PDF 連続保存
- `searchable_pdf=False`（既定）時および PDF 以外の保存形式（JPEG/PNG/TIFF/WebP/BMP/GIF）では従来と同一の挙動を維持
```

---

### タスク 3: `UserConfig`・`AppViewModel`・QML 保存設定 UI への対応

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/config/user_config.py` の `UserConfig` に `searchable_pdf_enabled: bool = False` を追加し、設定ファイルへの永続化・復元・検証に対応する
  - `src/open_escl_client/ui/viewmodel.py` の `AppViewModel` に `searchablePdfEnabled` プロパティ（`saveSettingsChanged` 連携）・`resetAllSettingsToDefaults` 初期化・`_build_current_save_profile` およびパイプライン設定への連携を追加する
  - `src/open_escl_client/ui/qml/SaveSettingsPanel.qml` に「検索可能 PDF を生成（透明テキスト埋め込み）」チェックボックスを配置し、保存形式が `PDF` のときに選択可能（非 PDF 選択時は補足説明を表示して非活性化）にする
  - `tests/config/test_user_config.py` および `tests/ui/test_qml_load.py` に設定永続化・QML バインディング・PDF 保存連携テストを追加する
- **提案コミットメッセージ**:

```text
feat(ui,config): 保存設定UIとUserConfigへ検索可能PDF生成オプションを追加

## 背景・目的
ユーザーが画面上の保存設定から「検索可能 PDF（透明テキスト埋め込み）」の ON/OFF を切り替え、設定を永続化しながらスキャン保存に適用できるようにするため（REQ-013 タスク3）。

## 変更内容
- `UserConfig`（`src/open_escl_client/config/user_config.py`）に `searchable_pdf_enabled`（既定 `False`）を追加し保存・復元に対応
- `AppViewModel`（`src/open_escl_client/ui/viewmodel.py`）に `searchablePdfEnabled` プロパティ、初期化リセット、保存プロファイル／パイプラインへの連携を追加
- `SaveSettingsPanel.qml` に検索可能 PDF トグルと保存形式（PDF 以外選択時）のガイド表示を追加
- `tests/config/test_user_config.py` と `tests/ui/test_qml_load.py` に永続化および QML 連携テストを追加

## 影響範囲・備考
- 対象 OS: macOS / Windows 共通 UI・設定レイヤー（既定値 `False` のため既存設定ユーザーの挙動は不変）
```
