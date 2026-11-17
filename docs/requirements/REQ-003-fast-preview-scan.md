# REQ-003: 低解像度による高速プレビュースキャン機能とプレビューキャンバス表示

- **ステータス**: `進行中`
- **優先ターゲット**: Brother × macOS（汎用 eSCL 機器・Windows 対応を考慮）
- **元要件（原案）**: [`docs/requirements/drafts/20261117.md`](./drafts/20261117.md)
- **依存する先行要件**: [`REQ-002`](./REQ-002-escl-discovery-and-paper-size.md)

---

## 1. 背景・目的

`docs/requirements/drafts/20261117.md` の「プレビュー機能」における以下の要件を実現する。
- 色合いを調整したときにどのようなイメージになるかを保存前に把握したい
- 自動トリミング時に正しくトリミングされているかを確認したい
- **実際に保存するときよりも読み取り解像度（DPI）を低くするなどの手段で待ち時間を短くしたい**

そのために、本スキャン用の保存解像度（例: 300dpi / 600dpi）とは別に、プレビュー専用としてスキャナがサポートする低解像度（例: 75dpi または 100dpi）を自動選択して高速にスキャンを実行し、プレビューキャンバスへ即座に表示する仕組みを構築する。

## 2. 要件概要とスコープ

### 実施すること（In Scope）
- eSCL の `/eSCL/ScanJobs` エンドポイントへの `ScanSettings` XML 送信、ジョブ URL（`Location` ヘッダ）からの `/NextDocument` 画像データ取得、およびジョブ完了/キャンセル管理の実装
- プレビュー実行時に、スキャナが対応する解像度の中から **プレビューに最適な低解像度（75〜150dpi の最小値）** を自動選択してリクエストを組み立てる高速プレビューモードの実装
- 取得したプレビュー画像を QML の `PreviewArea.qml` 上に拡大・縮小・全体表示できるキャンバスとして描画する機能

### 今回のブランチでは実施しないこと（Out of Scope / 別要件へ分割）
- ファイルへの保存・命名規則・連続スキャン（`REQ-004` で実施）
- 自動トリミング枠の重畳表示や色合い補正の適用（`REQ-005`, `REQ-006` で実施）

## 3. 完了条件（Acceptance Criteria）

- [x] 「プレビュー」ボタンを押すと、本スキャン用の設定解像度に関わらず低解像度（75〜100dpi 等）で高速にスキャンが行われること
- [x] Brother × macOS 環境（およびモックサーバー）で `/eSCL/ScanJobs` → `/NextDocument` の一連の通信が正常に完了し、プレビュー画像が画面に表示されること
- [x] スキャン中の進行状態（スピナー・ステータスメッセージ）とキャンセル操作が機能すること
- [ ] 原稿台の置き方イメージ通りに原稿を置いた際、プレビュー結果の画像が正しい向き・領域で表示されること（およびプレビュー画面上で90度単位の回転調整も行えること）

---

## 4. 提案 Git ブランチ名

```text
feature/fast-preview-scan
```

---

## 5. タスク一覧（1タスク ＝ 1コミット）

### タスク 1: `/eSCL/ScanJobs` スキャンジョブ実行・画像取得クライアントの実装

- **状態**: `[x] 完了`
- **実装内容**:
  - `ScanSettings` XML ペイロード（解像度、読み取り領域 `ScanRegions`、カラーモード、フォーマット）を生成するビルダーを実装する
  - `POST /eSCL/ScanJobs` でジョブを作成し、`Location` ヘッダから `{JobUri}/NextDocument` を取得、完了または中断時に `DELETE {JobUri}` を行う `EsclScanClient` を実装する
- **提案コミットメッセージ**:

```text
feat(escl): ScanJobsエンドポイントによるスキャン実行・画像取得クライアントを実装

## 背景・目的
eSCLプロトコルを用いてBrother製スキャナおよび汎用eSCL機器へスキャン要求を送り、読み取った画像データを取得するため（REQ-003 タスク1）。

## 変更内容
- `src/open_escl_client/escl/scan_client.py` を追加し、`ScanSettings` XMLの生成と `POST /eSCL/ScanJobs`・`GET /NextDocument`・`DELETE` によるジョブ制御を実装
- Brother機器で要求されるXML名前空間（`pwg`, `scan`）およびステータスコード（201 Created / 503 Busy等）のハンドリングを実装
- モックHTTPレスポンスを用いたスキャンジョブ通信の単体テストを追加

## 影響範囲・備考
- 対象機器: Brother eSCL対応機（最優先）および汎用eSCLスキャナ
- プレビュースキャンと本スキャンの双方で使用する共通通信基盤となる
```

---

### タスク 2: 待ち時間を短縮する低解像度（低DPI）プレビュースキャン制御の実装

- **状態**: `[x] 完了`
- **実装内容**:
  - スキャナの `ScannerCapabilities` から取得した対応解像度リストを参照し、プレビュー用として最も高速に読み取れる低解像度（75dpi / 100dpi / 150dpi のうち利用可能な最小値）を自動決定する `PreviewScanService` を実装する
  - プレビュー結果の画像データと読み取り時の座標スケール情報（本スキャン時の座標変換やトリミング枠計算に使用）を保持する `PreviewSession` モデルを作成する
- **提案コミットメッセージ**:

```text
feat(scan): 低解像度(DPI)自動選択による高速プレビュースキャンサービスを実装

## 背景・目的
本保存時の高解像度（300dpiや600dpi等）のままプレビューを行うと読み取りと転送に時間がかかるため、プレビュー時はスキャナが対応する低解像度を自動選択して待ち時間を大幅に短縮する（REQ-003 タスク2）。

## 変更内容
- `src/open_escl_client/core/preview_service.py` を追加し、スキャナ能力からプレビュー最適解像度（75〜150dpi）を自動選択してスキャンを実行するロジックを実装
- 低解像度プレビュー上の座標と高解像度本スキャン時の座標を相互変換できるよう、DPI比率と領域情報を保持する `PreviewSession` を実装
- 解像度選択とスケール変換の単体テストを追加

## 影響範囲・備考
- 後続のトリミング機能（REQ-005）や色合い調整（REQ-006）において、プレビュー上で確認した状態をそのまま本スキャンに適用するための基盤となる
```

---

### タスク 3: QML プレビューキャンバス（`PreviewArea.qml`）への画像描画と非同期ステータス表示

- **状態**: `[x] 完了`
- **実装内容**:
  - Python 側の画像（NumPy / Pillow / `QImage`）を QML へ高速に渡す `QQuickImageProvider` を実装する
  - `PreviewArea.qml` と `ActionToolbar.qml` を更新し、「プレビュー」ボタン押下による非同期スキャン実行、ローディング表示、プレビュー画像の表示（ズーム・フィット表示）を実装する
- **提案コミットメッセージ**:

```text
feat(ui): QMLプレビューキャンバスへのスキャン画像表示と進行状態UIを実装

## 背景・目的
低解像度で高速に読み取ったプレビュー画像を画面中央のキャンバスへ即座に表示し、ユーザーが色合いや配置を目視確認できるようにするため（REQ-003 タスク3）。

## 変更内容
- メモリ上のスキャン画像をQMLへ受け渡す `PreviewImageProvider`（`QQuickImageProvider`）を実装
- `PreviewArea.qml` にプレビュー画像の表示・ウィンドウフィット制御・スキャン中インジケータを宣言的に追加
- `ActionToolbar.qml` の「プレビュー」ボタンから非同期でプレビュースキャンを実行するバインディングを実装

## 影響範囲・備考
- 対象OS: macOS / Windows 共通（UIスレッドをブロックしない非同期スキャン実行）
```

---

### タスク 4: ScanRegion が無視され見切れる問題の修正および原稿台の置き方とスキャン画像の向きを一致させる回転・座標補正の実装

- **状態**: `[ ] 未完了`
- **実装内容**:
  - **用紙サイズや「最大サイズ」に変えても同じサイズ（`1680×2195 px`）になり見切れていた問題の修正**:
    - PWG / eSCL の XML スキーマ（`xs:sequence`）では `<pwg:ScanRegion>` 内の要素順序が `<pwg:ContentRegionUnits>` → `<pwg:Height>` → `<pwg:Width>` → `<pwg:XOffset>` → `<pwg:YOffset>` と定義されているが、従来は `XOffset` / `YOffset` を先に配置し `pwg:MustHonor` 属性を付与していたため、Brother MFC-J6580CDW の XML パーサが `ScanRegion` のサイズ指定を無視してデフォルトサイズ（`1680×2195 px`）にフォールバックしていた問題を修正する
    - スキャナが報告する `ScannerCapabilities` の `<pwg:Version>` やスキーマ順序に厳密に準拠した `ScanSettings` XML を生成するよう `build_scan_settings_xml` を修正する
  - **原稿台の置き方（縦置き/横置き/最大サイズ）とプレビュー画像の向きの整合**:
    - フラットベッド原稿台では eSCL の主走査軸（画像のX軸）が物理的な「奥行き方向（奥→手前）」、副走査軸（画像のY軸）が物理的な「横方向（左→右）」に対応しているため、正面から見て置いた原稿の向きおよび選択した用紙サイズ（縦置き・横置き・最大サイズ）に合わせて画像を自動回転・領域正規化する処理を `PreviewScanService` に実装する
  - あわせて、プレビュー画面上でワンクリックで90度単位の回転調整（左90°回転・右90°回転）を行える機能を `AppViewModel` および `PreviewArea.qml` に追加する
- **提案コミットメッセージ**:

```text
fix(escl,scan,ui): ScanRegionが無視され見切れる不具合を修正し原稿台の置き方とプレビュー画像の向きを整合

## 背景・目的
Brother MFC-J6580CDW実機において、用紙サイズや向きを「A4 縦置き」や「最大サイズ」に変更しても常に同じデフォルトサイズ（1680×2195 px）でスキャンされて端が見切れ、かつスキャナの主走査・副走査の物理構造により画像が90度傾いて表示される問題を解消するため（REQ-003 タスク4）。

## 変更内容
- `src/open_escl_client/escl/scan_client.py` の `build_scan_settings_xml` を修正し、Brother等のeSCLパーサが `ScanRegion` を無視しないようPWG XMLスキーマ順（`ContentRegionUnits` → `Height` → `Width` → `XOffset` → `YOffset`）に準拠させ、余分な属性を除去
- `src/open_escl_client/core/preview_service.py` を更新し、原稿台（Platen）でのスキャン結果を選択された用紙サイズ・向き（縦置き / 横置き / 最大サイズ）に合わせて正しい向きへ自動回転・領域正規化する処理を実装
- `src/open_escl_client/ui/viewmodel.py` および `src/open_escl_client/ui/qml/PreviewArea.qml` に、プレビュー画像を90度単位で左右に回転できる機能（`rotatePreviewLeft` / `rotatePreviewRight`）を追加
- `tests/escl/test_scan_client.py`, `tests/core/test_preview_service.py`, `tests/ui/test_qml_load.py` に単体テストを追加し、`REQ-003` を完了に更新

## 影響範囲・備考
- 対象機器: Brother MFC-J6580CDW等のeSCL対応フラットベッドスキャナ全般
```
