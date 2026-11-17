# open-escl-client

**Brother iPrint&Scan** に代わる、**eSCL (AirScan) プロトコル**を用いた汎用スキャナクライアントアプリケーションです。
**macOS** および **Windows** のクロスプラットフォーム動作を前提とし、特に **「Brother 製スキャナ/複合機 × macOS」** の組み合わせを最優先ターゲットとして設計されています。

---

## 1. 技術スタックの特徴

- **再現性の高い Python 環境管理 (`uv` + `pyproject.toml`)**:
  - `uv` と `pyproject.toml`（および `uv.lock`）により、macOS でも Windows でもコマンド1つで同一の Python 実行環境・依存パッケージを再現できます。
- **人間が理解・編集しやすい宣言的 UI (`PySide6` + `QML`)**:
  - UI レイアウトは Python コード内にハードコードせず、XAML と同様に階層構造と配置を直感的に記述できる **QML (`.qml`)** で分離しています。
  - 「画面のどの位置にどのボタン・設定欄・プレビュー枠があるか」が `.qml` ファイルを見るだけで把握でき、人間が直接レイアウトや文言を編集しやすい構成です。

---

## 2. セットアップと実行方法

### 前提条件
- [`uv`](https://docs.astral.sh/uv/) がインストールされていること

### 1. 依存パッケージの同期（仮想環境の構築）
```bash
uv sync
```

### 2. アプリケーションの起動
```bash
uv run open-escl-client
```

### 3. テスト・Lint の実行
```bash
# 単体テスト・UIロード検証テストの実行
uv run pytest

# コードフォーマット・静的解析チェック
uv run ruff check .
```

---

## 3. ディレクトリ構成と UI（`.qml`）編集ガイド

```text
open-escl-client/
├── pyproject.toml                         # uv パッケージ・依存関係・ツール設定
├── docs/requirements/                     # 要件定義ファイル (REQ-XXX-*.md)
├── src/open_escl_client/
│   ├── main.py                            # CLI / GUI エントリポイント
│   ├── core/                              # 用紙サイズ判定・プレビュー・保存・命名規則ロジック
│   ├── escl/                              # eSCL (mDNS探索・Capabilities解析・ScanJobs通信)
│   ├── imaging/                           # 自動トリミング・角度補正・色キャリブレーション・裏透け除去
│   ├── config/                            # ユーザー設定の保存・OS別パス抽象化
│   └── ui/
│       ├── app.py                         # QML エンジン初期化
│       ├── viewmodel.py                   # Python と QML を繋ぐ ViewModel (AppViewModel)
│       └── qml/                           # 【宣言的 UI 定義ファイル群】
│           ├── Main.qml                   # メインウィンドウ全体のペイン配置
│           ├── ScannerSidebar.qml         # 左ペイン: スキャナ・サイズ・解像度・保存設定
│           ├── PreviewArea.qml            # 中央ペイン: プレビュー画像・トリミング枠表示キャンバス
│           └── ActionToolbar.qml          # 下部バー: 高速プレビュー・連続スキャン保存ボタン
└── tests/                                 # 自動テストスイート
```

### UI を編集したいとき
画面の配置やラベル、デザインを変更したい場合は、`src/open_escl_client/ui/qml/` 配下の `.qml` ファイルを直接編集してください：
- **全体の左右・上下レイアウト比率を変えたい場合**: [`Main.qml`](./src/open_escl_client/ui/qml/Main.qml)
- **設定項目（サイズ・DPI・保存先・命名規則）の配置を変えたい場合**: [`ScannerSidebar.qml`](./src/open_escl_client/ui/qml/ScannerSidebar.qml)
- **プレビューキャンバスの表示を変えたい場合**: [`PreviewArea.qml`](./src/open_escl_client/ui/qml/PreviewArea.qml)
- **下部ボタンやステータスバーを変えたい場合**: [`ActionToolbar.qml`](./src/open_escl_client/ui/qml/ActionToolbar.qml)
