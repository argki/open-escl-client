# REQ-014: オプションのクラウド OCR API バックエンド連携（明示オプトイン・プライバシー保護設計）

- **ステータス**: `未着手`
- **優先ターゲット**: Brother × macOS（クロスプラットフォーム・汎用eSCL拡張を考慮）
- **元要件（原案）**: [`REQ-012`](./REQ-012-high-accuracy-japanese-ocr-and-naming.md)（スコープ外事項からの切り出し）
- **依存する先行要件**: [`REQ-012`](./REQ-012-high-accuracy-japanese-ocr-and-naming.md)

---

## 1. 背景・目的

[`REQ-012`](./REQ-012-high-accuracy-japanese-ocr-and-naming.md) ではプライバシー保護とオフライン完結を重視し、Apple Vision Framework（macOS）および RapidOCR（Windows/Linux 向けオプション）によるローカル OCR 基盤を構築した。

一方で、手書き文字が多い書類・極めて複雑な帳票レイアウト・低画質スキャン、あるいはローカル OCR エンジン（RapidOCR 等）が未導入の Windows 環境などにおいては、ユーザーが自らの判断と API キーを用いて外部クラウド OCR API を併用したいケースが存在する。

本要件では**「既定ではローカル OCR のみ・外部通信ゼロ」を絶対原則**としつつ、ユーザーが明示的なプライバシー同意と API 設定を行った場合にのみ選択・併用できるオプションのクラウド OCR バックエンド連携を設計・実装する。

---

## 2. 要件概要とスコープ

### 実施すること（In Scope）
- `JapaneseOcrEngine` の既存 `OcrBackend` プロトコルに準拠した**オプションのクラウド OCR バックエンドアダプタ**（例: Google Cloud Vision API、汎用 REST/JSON エンドポイントアダプタ）の実装
- OCR 実行ポリシー（`local_only`〔既定〕／`local_first`〔ローカル失敗・空結果時のみクラウド補助〕／`cloud_preferred`〔クラウド優先・失敗時は即座にローカルへフォールバック〕）の制御機構
- **プライバシー・セキュリティ保護機能**：
  - 既定は完全無効（`local_only`、未同意状態では外部サーバーへ一切画像送信しない）
  - 有効化前にスキャン画像が外部サービスへ送信される旨を明示する同意ダイアログ
  - API キーを設定 JSON（`user_config.json`）へ平文保存しない秘密情報ストア抽象化（macOS キーチェーン連携＋クロスプラットフォームフォールバック）
- 設定永続化（非機密メタ設定のみ）、接続テストアクション、および QML UI での実行モード・API 設定・使用バックエンド表示の実装

### 今回のブランチでは実施しないこと（Out of Scope / 別要件へ分割）
- アプリケーション開発元による課金・プロキシサーバーを介した OCR API 提供（BYOK: ユーザー自身の API キー設定方式のみを対象とする）
- スキャン画像の自動クラウドストレージアップロード（Google Drive / Dropbox 保存等は別要件）

## 3. 完了条件（Acceptance Criteria）

- [ ] 初期設定（`local_only`）およびプライバシー未同意・API キー未設定状態では、いかなる場合も外部ネットワーク通信が発生しないこと
- [ ] 明示的同意と有効な認証情報を設定した場合に、クラウド OCR レスポンスが標準 `OcrResult` / `OcrTextBlock`（左上原点正規化座標＋視覚ストローク計測連携）へ変換され、既存のタイトル・日付抽出や検索可能 PDF（実装時）とそのまま連携できること
- [ ] ネットワーク不通・タイムアウト・認証エラー・レート制限発生時にアプリがクラッシュせず、ローカル OCR（macOS Vision 等）または安全なフォールバック値へ自動退避してスキャン・保存が継続できること
- [ ] API キーが `user_config.json` に平文出力されず、UI 上でもマスク表示／安全な更新・削除ができること
- [ ] Brother × macOS 環境（実キーチェーン連携またはモック HTTP トランスポート）およびクロスプラットフォーム構成で単体・UI テストがすべて通過すること

---

## 4. 提案 Git ブランチ名

```text
feature/optional-cloud-ocr-backends
```

---

## 5. タスク一覧（1タスク ＝ 1コミット）

### タスク 1: クラウド OCR 設定モデル・秘密情報ストア・プライバシーガードの実装

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/config/secret_store.py` を新設し、API キーを平文設定ファイルと分離管理する `SecretStore` 抽象化と macOS Keychain バックエンド（`security` コマンド等）およびメモリ/モックストアを実装する
  - `src/open_escl_client/config/user_config.py` の `UserConfig` に非機密設定（`ocr_execution_mode`: `"local_only" | "local_first" | "cloud_preferred"`、`cloud_ocr_provider`: `"none" | "google_vision" | "custom_http"`、`cloud_ocr_endpoint`: `""`、`cloud_ocr_consent_accepted`: `False`、`cloud_ocr_timeout_sec`: `15.0`）を追加し、バリデーションと正規化を実装する
  - `tests/config/test_secret_store.py` および `tests/config/test_user_config.py` に秘密情報の非露出・設定シリアライズ・不正値フォールバックの単体テストを追加する
- **提案コミットメッセージ**:

```text
feat(config): クラウドOCR設定モデルと安全な秘密情報ストアを実装

## 背景・目的
REQ-014 の前提として、クラウド OCR の利用モードやプロバイダ設定を安全に管理し、API キーを設定ファイル（`user_config.json`）へ平文保存しない秘密情報管理基盤を整備するため。

## 変更内容
- `src/open_escl_client/config/secret_store.py` を追加し、`SecretStore` インターフェースと macOS Keychain バックエンドおよびテスト用メモリストアを実装
- `UserConfig`（`src/open_escl_client/config/user_config.py`）に `ocr_execution_mode`（既定 `"local_only"`）、`cloud_ocr_provider`、`cloud_ocr_endpoint`、`cloud_ocr_consent_accepted`（既定 `False`）、`cloud_ocr_timeout_sec` を追加
- `tests/config/test_secret_store.py` と `tests/config/test_user_config.py` に保存・復元および機密情報分離の単体テストを追加

## 影響範囲・備考
- 対象 OS: macOS（Keychain 連携）／他 OS でも `SecretStore` プロトコル差し替えにより拡張可能
- 既定値は完全ローカル専用（`local_only` / 未同意）であり、既存ユーザー環境で外部通信は一切発生しない
```

---

### タスク 2: クラウド OCR バックエンドアダプタと実行ポリシーフォールバック制御の実装

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/imaging/cloud_ocr.py` を新設し、`OcrBackend` プロトコルを実装する `GoogleCloudVisionOcrBackend` および汎用 HTTP JSON バックエンド `CustomHttpOcrBackend` を `httpx` ベース（`httpx.Client` / トランスポート DI 対応）で実装する
  - 各 API の返却バウンディングボックスを左上原点正規化 `[0.0, 1.0]` の `OcrTextBlock` へ変換し、`OcrNamingExtractor` の視覚ストローク太さ計測とシームレスに連携させる
  - `src/open_escl_client/imaging/ocr.py` の `OcrConfig` および `JapaneseOcrEngine` を拡張し、`execution_mode`（`local_only` / `local_first` / `cloud_preferred`）と `cloud_consent_accepted` ガードに従ってバックエンド選択順とタイムアウト・エラー時のローカルフォールバックを制御する
  - `tests/imaging/test_cloud_ocr.py` に `httpx.MockTransport` を用いた応答パース・座標正規化・未同意時通信遮断・タイムアウト/HTTPエラー時のローカル OCR フォールバック検証テストを追加する
- **提案コミットメッセージ**:

```text
feat(imaging): クラウドOCRバックエンドアダプタとローカルフォールバック制御を実装

## 背景・目的
明示的オプトイン時に外部クラウド OCR API（Google Cloud Vision や汎用 REST OCR）を既存 `JapaneseOcrEngine` のバックエンドとして利用でき、通信障害や未同意時には確実にローカル OCR へ退避できるようにするため（REQ-014 タスク2）。

## 変更内容
- `src/open_escl_client/imaging/cloud_ocr.py` を追加し、`httpx`（トランスポート DI 対応）を用いた `GoogleCloudVisionOcrBackend` と `CustomHttpOcrBackend` を実装
- クラウド OCR レスポンスを行単位 `OcrTextBlock`（正規化座標）へ変換し、`JapaneseOcrEngine` の視覚ストローク計測および `OcrNamingExtractor` と統合
- `JapaneseOcrEngine`（`src/open_escl_client/imaging/ocr.py`）に `local_only` / `local_first` / `cloud_preferred` ポリシー制御と未同意・エラー時のローカル自動フォールバックを実装
- `tests/imaging/test_cloud_ocr.py` にモック HTTP 通信・座標正規化・プライバシー遮断・フォールバックテストを追加

## 影響範囲・備考
- 対象 OS: macOS / Windows 共通の純粋 `httpx` クライアント実装
- `local_only` または `cloud_consent_accepted=False` の場合はクラウドバックエンドの `recognize` 自体が呼び出されない安全設計
```

---

### タスク 3: `AppViewModel`・QML 設定 UI・プライバシー同意フローの統合

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `src/open_escl_client/ui/viewmodel.py` の `AppViewModel` にクラウド OCR 設定用プロパティ（`ocrExecutionMode`、`cloudOcrProvider`、`cloudOcrEndpoint`、`cloudOcrConsentAccepted`、`cloudOcrHasApiKey`、`cloudOcrConnectionStatus`）とスロット（`setCloudOcrApiKey`、`clearCloudOcrApiKey`、`acceptCloudOcrConsent`、`testCloudOcrConnection`）を実装する
  - 実行モードをクラウド利用モードへ変更しようとした際、未同意であれば即時に外部送信を有効化せず確認ダイアログ要求シグナルを発行するガードを組み込む
  - `src/open_escl_client/ui/qml/SaveSettingsPanel.qml`（および同意確認用 QML ダイアログ）に OCR 実行モード切替、プロバイダ／エンドポイント設定、API キー入力（マスク表示＋削除ボタン）、接続確認ボタン、プライバシー警告同意ダイアログを追加する
  - `tests/ui/test_qml_load.py` に未同意ブロック・API キー登録（`UserConfig` 非平文確認）・モック接続テスト・抽出連携の UI テストを追加する
- **提案コミットメッセージ**:

```text
feat(ui): クラウドOCR設定UI・秘密キー操作・プライバシー同意ダイアログを実装

## 背景・目的
ユーザーが安全かつ直感的にクラウド OCR の併用有無・API キー・実行ポリシーを設定でき、外部画像送信に対する明示的同意確認を行えるようにするため（REQ-014 タスク3）。

## 変更内容
- `AppViewModel`（`src/open_escl_client/ui/viewmodel.py`）に実行モード切替、API キー登録・削除（秘密情報ストア経由）、同意状態管理、非同期接続テストを実装
- `SaveSettingsPanel.qml` にクラウド OCR 実行ポリシー選択、API キーマスク管理、接続確認、および外部送信を明示警告するプライバシー同意ダイアログを追加
- `tests/ui/test_qml_load.py` に未同意ガード・設定保持・接続テストの統合テストを追加

## 影響範囲・備考
- 対象機能: 保存設定パネル UI、OCR エンジン構成、秘密情報ストア連携
- API キーの文字列自体は JSON 設定や状態メッセージに出力されない仕様をテストで担保
```
