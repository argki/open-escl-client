# REQ-008: 設定の永続化・初期化リセット・プレビュー解像度指定とデフォルト値見直し

- **ステータス**: `完了`
- **優先ターゲット**: Brother × macOS（クロスプラットフォーム・汎用eSCL拡張を考慮）
- **元要件（原案）**: [`docs/requirements/drafts/20261120.md`](./drafts/20261120.md)
- **依存する先行要件**: [`REQ-001`](./REQ-001-project-foundation-and-declarative-ui.md), [`REQ-003`](./REQ-003-fast-preview-scan.md), [`REQ-007`](./REQ-007-bleed-through-removal.md)

---

## 1. 背景・目的

現在、`UserConfig` に保存されている設定は保存フォルダ・ネーミングルール・形式・本保存解像度など一部に限られており、用紙サイズ・給紙元・高速プレビュー解像度・トリミング／回転／検出パターン・裏写り除去などの設定がアプリ再起動ごとに初期状態へ戻ってしまう。また、設定を一度に初期値へ戻す手段や、高速プレビュー時の解像度（DPI）をユーザーが明示指定する手段がない。

本要件では、ユーザーが前回調整したスキャン・画質・UI設定を次回起動時にも自動復元できるようにし、あわせて初期状態へのリセット機能、高速プレビュー解像度の選択機能、および実運用に合わせたデフォルト値（ネーミングルールへの `{time}` 追加、裏写り除去の初期 ON）を整備する。

## 2. 要件概要とスコープ

### 実施すること（In Scope）
- `UserConfig` の永続化対象を拡張し、給紙元・用紙サイズ・高速プレビュー DPI・自動トリミング各種設定（有効/複数分割/自動向き/検出パターン/検出感度/検出比率/余白）・裏写り除去設定（有効/強度/白化）・最後の接続スキャナを自動保存・復元する
- アプリ上の全ユーザー設定を既定値へ戻す「設定を初期状態にリセット」機能（確認ダイアログ付き）を実装する
- 高速プレビュー時の解像度（例: `75` / `100` / `150` / `200` dpi 等、スキャナ対応範囲内で選択可能）を指定できる UI と `PreviewScanService` 連携を実装する
- 既定のネーミングルールを `Scan_{date}_{time}_{seq:03d}`（`{time}` を含む形式）へ変更する
- 画質・裏写り（裏透け）除去設定のデフォルト値（`bleed_through_enabled=True`, `bleed_through_whiten_background=True`）を既定 ON に変更する

### 今回のブランチでは実施しないこと（Out of Scope / 別要件へ分割）
- プレビューキャンバスの拡大縮小・スクロール・切り出し枠ドラッグ修正（`REQ-009` で実施）
- 左サイドバー項目の折りたたみ UI 化（`REQ-010` で実施）
- 保存ファイル形式の拡充（`REQ-011` で実施）および日本語 OCR ネーミング抽出（`REQ-012` で実施）

## 3. 完了条件（Acceptance Criteria）

- [x] 給紙元・用紙サイズ・DPI・プレビューDPI・トリミング設定・裏写り設定を変更して再起動した際、前回の状態がそのまま復元されること
- [x] 「初期状態にリセット」操作で全設定が既定値（ネーミングルール `{time}` 含む、裏写り除去・背景白化とも ON）に戻り、永続化ファイルも更新されること
- [x] 高速プレビュー解像度を UI で変更し、指定した DPI でプレビューが実行されること
- [x] Brother × macOS 環境（またはモック検証）で意図通り動作すること
- [ ] Windows や汎用 eSCL 機器への将来対応を阻害するハードコードが含まれていないこと

---

## 4. 提案 Git ブランチ名

```text
feature/config-persistence-and-defaults
```

---

## 5. タスク一覧（1タスク ＝ 1コミット）

### タスク 1: `UserConfig` の永続化項目拡張と既定値（`{time}`・裏写りON）の見直し

- **状態**: `[x] 完了`
- **実装内容**:
  - `src/open_escl_client/config/user_config.py` の `UserConfig` に給紙元・用紙サイズ・プレビュー DPI・トリミング関連設定（自動トリミング、複数分割、自動向き、検出パターン、検出感度、検出比率、余白 mm）・裏写り除去設定（有効、強度、背景白化）・ウィンドウ状態等を追加し、`to_dict` / `from_dict` / `reset_to_defaults` を実装する
  - `src/open_escl_client/core/naming.py` の `DEFAULT_NAMING_TEMPLATE` および `UserConfig.naming_rule` のデフォルト値を `Scan_{date}_{time}_{seq:03d}` に変更する
  - `BleedThroughConfig` / `UserConfig` における裏写り除去と背景白化のデフォルト値をどちらも `True` に更新する
  - `tests/config/test_user_config.py` と `tests/core/test_naming.py` に新フィールドのシリアライズ・バリデーション・後方互換ロード・デフォルト値テストを追加する
- **提案コミットメッセージ**:

```text
feat(config): 設定永続化項目を拡張しネーミングルール既定値({time})と裏写り除去初期ONを反映

## 背景・目的
スキャン設定やトリミング・画質調整の設定を次回起動時にも保持できるようにするとともに、初期設定でのファイル名重複回避（`{time}` 付与）および薄い紙でも扱いやすい既定値（裏写り除去・紙白化ON）へ改善するため（REQ-008 タスク1）。

## 変更内容
- `src/open_escl_client/config/user_config.py` の `UserConfig` に給紙元・用紙サイズ・プレビューDPI・トリミング各種設定・裏写り除去設定の永続化フィールドとバリデーションを追加
- `src/open_escl_client/core/naming.py` および `UserConfig` の既定ネーミングルールを `Scan_{date}_{time}_{seq:03d}` に更新
- 裏写り除去と背景白化のデフォルト設定を初期有効（`True`）に変更し、`tests/config/test_user_config.py` 等に単体テストを追加

## 影響範囲・備考
- 既存の `config.json`（旧フォーマット）を読み込んだ場合も不足キーは安全に既定値へフォールバックする後方互換性を維持
- macOS / Windows 共通のユーザー設定ディレクトリ配下へ保存
```

---

### タスク 2: `AppViewModel` と `PreviewScanService` への全設定保存・復元およびプレビュー解像度指定の統合

- **状態**: `[x] 完了`
- **実装内容**:
  - `src/open_escl_client/core/preview_service.py` に、ユーザー指定の希望プレビュー DPI をスキャナの `supported_resolutions` と照合して決定する解決ロジックを追加する
  - `src/open_escl_client/ui/viewmodel.py` の起動時ロード・各プロパティ変更時の自動保存・`resetAllSettingsToDefaults()` スロットを実装する（接続先スキャナ能力取得後にも保存済み用紙サイズ・給紙元を優先復元する）
  - プレビュー解像度プロパティ `previewDpiChoices` / `selectedPreviewDpi` を `AppViewModel` へ追加し、`requestPreview()` で選択中のプレビュー解像度を使用するよう接続する
  - `tests/core/test_preview_service.py` と `tests/ui/test_qml_load.py` に再起動復元・初期化リセット・プレビュー解像度指定のテストを追加する
- **提案コミットメッセージ**:

```text
feat(core,ui): ViewModelの全設定自動保存・初期化リセットと高速プレビューDPI指定を実装

## 背景・目的
UI上で変更したすべてのスキャン・補正設定を即座に永続化して再起動時に復元できるようにし、あわせてユーザーが高速プレビュー時の解像度（DPI）や初期状態リセットを制御できるようにするため（REQ-008 タスク2）。

## 変更内容
- `src/open_escl_client/core/preview_service.py` にユーザー指定プレビューDPIとスキャナ対応解像度の照合・選択ロジックを追加
- `src/open_escl_client/ui/viewmodel.py` で各種設定変更時の `UserConfigStore` 自動保存・起動時復元（スキャナ接続後の用紙/給紙元反映含む）・`resetAllSettingsToDefaults` スロット・`selectedPreviewDpi` プロパティを実装
- `tests/core/test_preview_service.py` および `tests/ui/test_qml_load.py` に永続化復元・初期化リセット・プレビューDPI変更テストを追加

## 影響範囲・備考
- Brother × macOS の実機能力取得後も保存していた用紙サイズ・給紙元・DPI設定がリセットされず維持される
```

---

### タスク 3: 高速プレビュー解像度選択および設定初期化リセットの QML UI 実装

- **状態**: `[x] 完了`
- **実装内容**:
  - `src/open_escl_client/ui/qml/ScannerSidebar.qml` の「2. 読み取り設定」内に「高速プレビュー解像度 (DPI)」コンボボックス（`previewDpiCombo`）を追加する
  - `ScannerSidebar.qml` またはフッター近辺に「設定を初期状態にリセット」ボタンと誤操作防止の確認ダイアログ（`Dialog`）を追加し、`viewModel.resetAllSettingsToDefaults()` を呼び出す
  - `src/open_escl_client/ui/qml/PreviewArea.qml` のプレビューボタン／ヘッダー表示にも選択中のプレビュー DPI が動的に反映されることを確認する
  - `tests/ui/test_qml_load.py` に新規 QML 要素のロード確認とリセット操作テストを追加する
- **提案コミットメッセージ**:

```text
feat(ui): 高速プレビュー解像度選択UIと設定初期化リセットボタンをQMLに実装

## 背景・目的
ユーザーが画面上で高速プレビュー時の解像度（DPI）を切り替えられ、設定を変更しすぎた場合にもワンクリックで初期状態へ戻せるようにするため（REQ-008 タスク3）。

## 変更内容
- `src/open_escl_client/ui/qml/ScannerSidebar.qml` に高速プレビュー解像度（DPI）選択コンボボックスと、確認ダイアログ付きの設定初期化リセットボタンを追加
- `tests/ui/test_qml_load.py` に QML コンポーネントのロードおよび UI からの設定リセット動作確認テストを追加

## 影響範囲・備考
- 対象OS: macOS / Windows 共通（QML レイアウト幅制約を維持し右ペインへの侵食なし）
```
