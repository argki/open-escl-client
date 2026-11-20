# REQ-012: 高精度日本語 OCR の導入と OCR 結果からのファイルネーミング自動抽出

- **ステータス**: `未着手`
- **優先ターゲット**: Brother × macOS（クロスプラットフォーム・汎用eSCL拡張を考慮）
- **元要件（原案）**: [`docs/requirements/drafts/20261120.md`](./drafts/20261120.md)
- **依存する先行要件**: [`REQ-004`](./REQ-004-scan-save-and-continuous-workflow.md), [`REQ-005`](./REQ-005-multi-crop-and-rotation-correction.md)

---

## 1. 背景・目的

スキャンした書類（請求書、領収書、通知書、役所書類など）を保存する際、現在は日付や連番ベースのファイル名しか自動生成できないため、後から内容を探しやすいファイル名を付けるにはユーザーが手動でリネームする必要がある。

本要件では、まず最優先ターゲットである **macOS 上で極めて高い日本語認識精度を発揮する Apple Vision Framework（`VNRecognizeTextRequest` 高精度モード `ja-JP`）** を中核としつつ、将来の Windows 対応に向けたクロスプラットフォーム OCR バックエンド抽象化（例：ONNX ベースの日本語 OCR エンジンを差し替え可能なインターフェース）を整備する。さらに、OCR で認識したテキストから文書のタイトル・日付・発行元キーワードなどを自動抽出し、ネーミングルール（例：`{ocr_title}`, `{ocr_date}`）または保存時のファイル名候補として自動反映できる機能を提供する。

## 2. 要件概要とスコープ

### 実施すること（In Scope）
- 認識テキスト・バウンディングボックス・信頼度を返す汎用 OCR エンジン抽象化レイヤー（`OcrEngine` / `OcrBackend`）の設計・実装
  - **macOS バックエンド（最優先）**: `Vision` フレームワークの `VNRecognizeTextRequest`（`recognitionLevel=Accurate`, `recognitionLanguages=["ja-JP", "en-US"]`, `usesLanguageCorrection=True`）を用いて、縦書き・横書き混在の日本語書類を高精度に文字認識する
  - **クロスプラットフォームバックエンド**: Windows 等でも動作するローカル ONNX 日本語 OCR 連携用アダプタ（オプション依存パッケージ検出または軽量フォールバック）を分離定義する
- OCR テキストとレイアウト情報（文字サイズ・行位置・キーワードパターン）から、ファイル名に利用可能なメタデータ（`ocr_title`: 主要見出し／帳票名、`ocr_date`: 文書日付 `YYYYMMDD` 正規化、`ocr_summary`）を抽出する `OcrNamingExtractor` の実装
- `FileNamingEngine`（`naming.py`）への `{ocr_title}` / `{ocr_date}` トークン追加、およびプレビュー／保存パネルで OCR 抽出結果をファイル名候補として確認・編集・反映できる UI 連携

### 今回のブランチでは実施しないこと（Out of Scope / 別要件へ分割）
- 検索可能 PDF（透明テキスト埋め込み PDF）の生成（別要件で検討・実装）
- クラウド API（外部サーバー送信）への画像アップロード OCR（プライバシー保護のためローカル実行を基本とする）

## 3. 完了条件（Acceptance Criteria）

- [ ] Brother × macOS 環境で日本語文書（通知書・領収書・レシート等）をスキャンした際、高精度日本語 OCR によりテキストとレイアウト位置が正しく認識されること
- [ ] OCR テキストから文書タイトルや日付を自動抽出し、ネーミングルールの `{ocr_title}` / `{ocr_date}` 展開やファイル名候補提案として利用できること
- [ ] Windows 環境向けの拡張を阻害しない OS 抽象化（バックエンド分離・モックテスト可能）になっていること
- [ ] OCR 無効時やテキスト未検出時は安全なフォールバック値となり保存が失敗しないこと

---

## 4. 提案 Git ブランチ名

```text
feature/high-accuracy-japanese-ocr-and-naming
```

---

## 5. タスク一覧（1タスク ＝ 1コミット）

### タスク 1: 日本語高精度 OCR 抽象化レイヤーと macOS Vision (`ja-JP`) バックエンドの実装

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/imaging/ocr.py` を新設し、`OcrTextBlock`（テキスト・正規化バウンディングボックス・文字高さ・信頼度）、`OcrResult`、`OcrBackend` プロトコル、`JapaneseOcrEngine` を定義する
  - macOS 向けに既存の Vision 連携設計（`orientation.py` 参照）を踏襲し、`VNRecognizeTextRequest` で `recognitionLevel=Accurate`、`recognitionLanguages=["ja-JP", "en-US"]`、`usesLanguageCorrection=True` を用いて全文と領域情報を抽出する `MacOSVisionOcrBackend` を実装する
  - Windows / Linux 向けに外部ローカル OCR（RapidOCR 等、利用可能時に自動利用）または未導入時グレースフルフォールバックを行うクロスプラットフォームアダプタ基盤を用意する
  - `tests/imaging/test_ocr.py` にモックバックエンドおよび macOS Vision 実環境条件テストを追加する
- **提案コミットメッセージ**:

```text
feat(imaging): 日本語高精度OCRエンジン抽象化レイヤーとmacOS Vision(ja-JP)バックエンドを実装

## 背景・目的
日本語の書類・レシート等を高精度かつオフラインで文字認識し、自動リネーム機能等の基盤を構築するため（REQ-012 タスク1）。

## 変更内容
- `src/open_escl_client/imaging/ocr.py` を追加し、テキストブロック（座標・文字高・信頼度）を扱う `OcrBackend` インターフェースと `JapaneseOcrEngine` を実装
- macOS 向けに Apple Vision Framework の高精度日本語認識（`VNRecognizeTextRequest` / `ja-JP`）を用いた `MacOSVisionOcrBackend` を実装
- OS 固有処理をバックエンドへ分離し、Windows 等向けローカル OCR 拡張を容易にする構成と単体テスト（`tests/imaging/test_ocr.py`）を追加

## 影響範囲・備考
- Brother × macOS 最優先で追加モデルダウンロード不要・ローカル完結の高精度日本語 OCR を実現
- 既存 `AutoOrientationCorrector` と共存可能な疎結合設計
```

---

### タスク 2: OCR 結果からの文書タイトル・日付抽出ロジックと `FileNamingEngine` トークン拡張

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/core/ocr_naming.py` を追加し、`OcrResult` の行テキスト・文字高さ・位置・日付パターン（和暦 `令和X年Y月Z日` / 西暦 `YYYY年M月D日` / `YYYY/MM/DD` 等）から `ocr_title`（見出し・主要帳票キーワード）と `ocr_date`（`YYYYMMDD`）を抽出し、ファイル名禁止文字をサニタイズする `OcrNamingExtractor` を実装する
  - `src/open_escl_client/core/naming.py` の `FileNamingEngine` を拡張し、`{ocr_title}` や `{ocr_date}` トークン（未抽出時はフォールバックで空文字整理や既定値にフォールバックし二重アンダースコア `__` 等を整形）を展開できるようにする
  - `tests/core/test_ocr_naming.py` および `tests/core/test_naming.py` に和暦/西暦日付抽出・見出し優先抽出・フォールバックの単体テストを追加する
- **提案コミットメッセージ**:

```text
feat(core): OCRテキストからのタイトル・日付抽出ロジックとネーミングトークン拡張を実装

## 背景・目的
OCRで認識した日本語テキストからファイル名に有用な見出しや日付（和暦・西暦対応）を抽出し、ネーミングルールで自動命名できるようにするため（REQ-012 タスク2）。

## 変更内容
- `src/open_escl_client/core/ocr_naming.py` を追加し、文字サイズ・位置・日付正規表現（令和/平成等の和暦および西暦）を用いて `ocr_title` / `ocr_date` を抽出する `OcrNamingExtractor` を実装
- `src/open_escl_client/core/naming.py` の `FileNamingEngine` に `{ocr_title}` / `{ocr_date}` プレースホルダー展開と未抽出時の記号整形ロジックを追加
- `tests/core/test_ocr_naming.py` に多様な日本語帳票テキストからの抽出精度・サニタイズ検証テストを追加

## 影響範囲・備考
- `{ocr_title}` 等を含まない従来のネーミングルールには影響しない
```

---

### タスク 3: 保存パイプライン連携および QML UI での OCR 抽出候補提案機能の実装

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/imaging/pipeline.py`（および `AppViewModel`）に OCR ネーミング抽出の ON/OFF 設定とプレビュー時／保存時の非同期・自動抽出処理を組み込む
  - プレビュー完了時（各切り出し枠に対して）または保存設定パネルの「OCR でファイル名候補を提案」アクションにより、抽出されたタイトル・日付をプレビューや `次のファイル名` へ即時反映し、必要に応じてユーザーが上書き編集できるようにする
  - `src/open_escl_client/ui/qml/SaveSettingsPanel.qml` に「OCR 抽出結果をファイル名に使う（`{ocr_title}` / `{ocr_date}` トークン挿入ボタン＋抽出プレビュー）」UI を追加する
  - `tests/ui/test_qml_load.py` に OCR ネーミング連携と QML バインディングテストを追加する
- **提案コミットメッセージ**:

```text
feat(ui,scan): スキャン保存パイプラインとQML保存パネルへOCRネーミング抽出連携を実装

## 背景・目的
プレビューまたは本スキャン保存時に日本語OCRから自動抽出したタイトル・日付をファイル名へ自動反映し、ユーザーが確認・調整したうえで保存できるようにするため（REQ-012 タスク3）。

## 変更内容
- `src/open_escl_client/imaging/pipeline.py` と `src/open_escl_client/ui/viewmodel.py` に OCR テキスト抽出からネーミングコンテキストを生成・引き継ぐ処理を統合
- `src/open_escl_client/ui/qml/SaveSettingsPanel.qml` に OCR ネーミング有効化トグル、抽出タイトル／日付プレビュー、および `{ocr_title}` / `{ocr_date}` クイック挿入UIを追加
- `tests/ui/test_qml_load.py` に OCR 連携時のファイル名生成および UI ロードテストを追加

## 影響範囲・備考
- 対象OS: macOS（Vision OCR）/ Windows（拡張バックエンド対応）
```
