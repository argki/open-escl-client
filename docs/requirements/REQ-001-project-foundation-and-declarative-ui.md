# REQ-001: プロジェクト基盤（uv + pyproject.toml）と宣言的UI（QML）のベース構築

- **ステータス**: `完了`
- **優先ターゲット**: Brother × macOS（Windows クロスプラットフォーム対応を考慮）
- **元要件（原案）**: [`docs/requirements/drafts/20261117.md`](./drafts/20261117.md)
- **依存する先行要件**: なし

---

## 1. 背景・目的

`docs/requirements/drafts/20261117.md` の「実行方法」で定義された要件に基づき、以下の条件を満たす技術スタックとプロジェクト骨格を整備する。

1. **AI・人間双方にとって開発しやすく、環境の再現性が高いこと**:
   - 画像処理（OpenCV / NumPy 等）や eSCL 通信のライブラリ資産が最も豊富な **Python** を採用し、パッケージ・仮想環境管理に **`uv` + `pyproject.toml`（および `uv.lock`）** を用いることで、macOS / Windows どちらでも `uv sync` / `uv run` のみで同一環境を完全再現できるようにする。
2. **UI の構造が分かりやすく、人間が直感的に編集しやすいこと（XAML ライクな宣言的記述）**:
   - Python コード内に UI 配置をベタ書きするのではなく、**PySide6 (Qt for Python) + QML (`.qml`)** を採用する。
   - QML は XAML と同様に UI の階層構造・配置・プロパティを宣言的に記述でき、「画面のどの位置にどのインターフェース（プレビュー枠、設定パネル、ボタン等）があるか」が一目で分かり、人間が直接編集しやすい。

## 2. 要件概要とスコープ

### 実施すること（In Scope）
- `uv` と `pyproject.toml` による Python プロジェクト初期化および主要依存パッケージ（`PySide6`, `httpx`, `zeroconf`, `lxml`, `opencv-python-headless`, `numpy`, `Pillow`, `img2pdf`, `pytest`）の定義
- コアロジック層（`src/open_escl_client/core/`）と宣言的 UI 層（`src/open_escl_client/ui/qml/`）を分離したパッケージ構成の作成
- QML（`.qml`）によるメイン画面レイアウト骨格（左/右の設定サイドバー、中央のプレビューキャンバス領域、下部のスキャン・保存アクションバー）の作成と起動確認

### 今回のブランチでは実施しないこと（Out of Scope / 別要件へ分割）
- 実際の eSCL スキャナ通信処理（`REQ-002`, `REQ-003` で実施）
- 画像処理アルゴリズムの実装（`REQ-005`, `REQ-006`, `REQ-007` で実施）

## 3. 完了条件（Acceptance Criteria）

- [x] `uv sync` で依存関係がエラーなくインストールできること
- [x] `uv run open-escl-client` で QML ベースのメインウィンドウが macOS 上で起動すること
- [x] UI のレイアウト定義が `.py` ファイルではなく `.qml` ファイルに宣言的に分離されており、構造が人間にとって読みやすいこと
- [x] `uv run pytest` で初期テストスイートが実行・パスすること

---

## 4. 提案 Git ブランチ名

```text
chore/setup-uv-and-declarative-ui
```

---

## 5. タスク一覧（1タスク ＝ 1コミット）

### タスク 1: uv と pyproject.toml によるプロジェクト初期化とディレクトリ骨格の作成

- **状態**: `[x] 完了`
- **実装内容**:
  - `pyproject.toml` を作成し、Python バージョン制約、依存ライブラリ、スクリプトエントリポイント（`open-escl-client`）、および `pytest` / `ruff` 設定を定義する
  - `src/open_escl_client/` 配下に `core/`, `escl/`, `imaging/`, `config/`, `ui/` のモジュール骨格と `tests/` ディレクトリを作成する
- **提案コミットメッセージ**:

```text
chore(config): uvとpyproject.tomlによるPythonプロジェクト基盤を構築

## 背景・目的
開発環境の再現性を高め、macOSおよびWindowsの双方で同一の依存関係をコマンド一つで構築できるようにするため、`uv` と `pyproject.toml` を用いたプロジェクト管理基盤を整備する（REQ-001 タスク1）。

## 変更内容
- `pyproject.toml` を新規作成し、eSCL通信・画像処理・UI構築に必要な依存パッケージとエントリポイントを定義
- `src/open_escl_client/` 配下に機能別パッケージ（`core`, `escl`, `imaging`, `config`, `ui`）の初期ディレクトリ構造を作成
- `tests/` ディレクトリと初期スモークテストを追加

## 影響範囲・備考
- 対象OS: macOS（最優先）および Windows 共通のプロジェクト構成
- `uv sync` および `uv run pytest` で環境構築とテスト実行が可能になる
```

---

### タスク 2: QML による宣言的メインウィンドウ UI と ViewModel ブリッジの実装

- **状態**: `[x] 完了`
- **実装内容**:
  - `src/open_escl_client/ui/qml/Main.qml` およびパネル別コンポーネント（`ScannerSidebar.qml`, `PreviewArea.qml`, `ActionToolbar.qml`）を宣言的に記述する
  - Python 側の状態・操作を QML にバインディングする `AppViewModel`（`QObject` ベース）とアプリケーション起動処理を実装する
- **提案コミットメッセージ**:

```text
feat(ui): QMLによる宣言的メイン画面レイアウトとViewModel基盤を実装

## 背景・目的
Pythonコード内にUI配置をハードコードすると画面構造の把握や人間の手による編集が困難になるため、XAMLと同様に階層構造を宣言的に記述できるQML（PySide6 / Qt Quick）を用いてUI層を構築する（REQ-001 タスク2）。

## 変更内容
- `src/open_escl_client/ui/qml/Main.qml` を追加し、設定サイドバー・プレビュー表示領域・スキャン操作バーの3ペイン構成を宣言的に定義
- 各UI領域を `ScannerSidebar.qml`, `PreviewArea.qml`, `ActionToolbar.qml` としてコンポーネント分割し、人間が位置と役割を直感的に編集しやすい構造に整理
- Pythonコア層とQMLを接続する `AppViewModel` およびアプリ起動エントリポイントを実装

## 影響範囲・備考
- 対象OS: macOS（最優先確認）、Windows（Qt Quickによるクロスプラットフォーム描画）
- UIの見た目・配置の変更は `.qml` ファイルの編集のみで完結する構成とした
```

---

### タスク 3: UI 起動・ViewModel バインディングの単体テストと開発手順ドキュメントの追加

- **状態**: `[x] 完了`
- **実装内容**:
  - ヘッドレス（オフスクリーン）環境で QML エンジンのロードと `AppViewModel` のプロパティバインディングを検証するテストを追加する
  - `README.md` に `uv` を使ったセットアップ手順・起動方法・`.qml` ファイルの編集ガイドを記載する
- **提案コミットメッセージ**:

```text
test(ui): QMLロード検証テストとuv開発環境ドキュメントを追加

## 背景・目的
宣言的UI（QML）の構文エラーやViewModelとのバインディング不整合を自動テストで検知できるようにし、開発者が迷わず環境構築とUI編集を行えるようにするため（REQ-001 タスク3）。

## 変更内容
- `tests/ui/test_qml_load.py` を追加し、`QT_QPA_PLATFORM=offscreen` 環境下で `Main.qml` がエラーなくロードされることを検証するテストを実装
- `README.md` を作成し、`uv sync` / `uv run` による実行手順とQMLファイルの構成ガイドを記載

## 影響範囲・備考
- 対象OS: macOS / Windows 共通
- CIやローカル環境でGUIウィンドウを実際に表示せずにQMLの整合性を自動テスト可能
```
