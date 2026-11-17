# REQ-002: eSCL スキャナ探索・能力取得と対応用紙サイズ（A4/B5等）の自動検知

- **ステータス**: `進行中`
- **優先ターゲット**: Brother × macOS（汎用 eSCL 機器・Windows 対応を考慮）
- **元要件（原案）**: [`docs/requirements/drafts/20261117.md`](./drafts/20261117.md)
- **依存する先行要件**: [`REQ-001`](./REQ-001-project-foundation-and-declarative-ui.md)

---

## 1. 背景・目的

`docs/requirements/drafts/20261117.md` の「サイズ設定（一般的な A4 B5 などに対応。スキャナーが対応する最大サイズを検知して選択を用意する想定）」およびプロジェクト基本方針（Brother × macOS 最優先、eSCL 汎用プロトコル採用）を実現するため、ネットワーク上の eSCL 対応スキャナを検出し、スキャナの能力（`/eSCL/ScannerCapabilities`）から最大サイズ・対応解像度・給紙方式を読み取って、選択可能な用紙サイズリストを動的に構築する。

## 2. 要件概要とスコープ

### 実施すること（In Scope）
- mDNS / Bonjour（`_uscan._tcp.local.`, `_uscans._tcp.local.`）を用いたローカルネットワーク上の eSCL スキャナ探索機能
- `/eSCL/ScannerCapabilities` XML の取得・パース処理（Brother 製スキャナ特有の XML 名前空間や要素構造にも対応するベンダープロファイル抽象化を含む）
- スキャナが対応する最大読み取りサイズ（`MaxWidth`, `MaxHeight`：1/300 inch 単位の ThreeHundredthsOfInches）の検知
- 検知した最大サイズ以下に収まる定型用紙サイズ（A4, B5, A5, レター, ハガキ, 名刺, および「スキャナ最大サイズ / 自動検知」）をフィルタリングして UI のサイズ選択肢として提供する機能

### 今回のブランチでは実施しないこと（Out of Scope / 別要件へ分割）
- 実際のスキャンジョブ送信と画像データの取得（`REQ-003` で実施）
- 画像解析による複数用紙の自動切り出し（`REQ-005` で実施）

## 3. 完了条件（Acceptance Criteria）

- [ ] macOS 上で同一ネットワーク内の Brother 製 eSCL スキャナ（および汎用 eSCL スキャナ / モック）を検出できること
- [ ] `/eSCL/ScannerCapabilities` から原稿台（Platen）・ADF の最大読み取り幅・高さを正確に取得できること
- [ ] スキャナの最大サイズに応じて、利用可能な定型サイズ（A4, B5 等）が自動的に判定され、UI のドロップダウンで選択できること

---

## 4. 提案 Git ブランチ名

```text
feature/escl-discovery-and-capabilities
```

---

## 5. タスク一覧（1タスク ＝ 1コミット）

### タスク 1: mDNS (Bonjour) による eSCL スキャナ探索モジュールの実装

- **状態**: `[x] 完了`
- **実装内容**:
  - `zeroconf` を用いて `_uscan._tcp.local.`（HTTP）および `_uscans._tcp.local.`（HTTPS）サービスを探索する `EsclScannerDiscovery` クラスを実装する
  - TXT レコード（`ty`, `rs`, `uuid`, `note` 等）からモデル名・エンドポイント URL・Brother 機器判定フラグを抽出するデータモデルを定義する
- **提案コミットメッセージ**:

```text
feat(discovery): mDNSによるeSCLスキャナ自動探索モジュールを実装

## 背景・目的
ユーザーがIPアドレスを手入力することなく、macOSおよびWindows環境からネットワーク上のBrother製スキャナや汎用eSCL対応機を自動検出できるようにするため（REQ-002 タスク1）。

## 変更内容
- `src/open_escl_client/escl/discovery.py` を追加し、`_uscan._tcp` および `_uscans._tcp` を探索する `EsclScannerDiscovery` を実装
- mDNSのTXTレコードからスキャナ名、ベースURL、メーカー情報を保持する `DiscoveredScanner` モデルを定義
- モックサービスを用いた探索ロジックの単体テストを追加

## 影響範囲・備考
- 対象OS: macOS（Bonjour互換）、Windows（mDNSマルチキャスト対応）
- 対象機器: Brother製スキャナおよびすべてのeSCL (AirScan) 準拠スキャナ
```

---

### タスク 2: `/eSCL/ScannerCapabilities` XML パーサと Brother 対応アダプタの実装

- **状態**: `[x] 完了`
- **実装内容**:
  - `/eSCL/ScannerCapabilities` から取得した XML をパースし、原稿台（`Platen`）および ADF（`AdfSimplex` / `AdfDuplex`）ごとの最小/最大サイズ（1/300 inch 単位）、対応解像度（`DiscreteResolutions` / `ResolutionRange`）、カラーモード、サポート形式（JPEG / PDF）を構造化するパーサを実装する
  - Brother 製スキャナの実機 XML サンプルを含むテストフィクスチャを作成し、名前空間の揺れやベンダー固有要素があっても確実にパースできるようアダプタ層を整える
- **提案コミットメッセージ**:

```text
feat(escl): ScannerCapabilitiesのXMLパーサとベンダー差異吸収層を実装

## 背景・目的
スキャナごとに異なる最大読み取りサイズや対応解像度（DPI）、給紙方式を動的に把握し、最優先ターゲットであるBrother製機器のXMLレスポンス仕様でも確実に動作させるため（REQ-002 タスク2）。

## 変更内容
- `src/open_escl_client/escl/capabilities.py` を追加し、`ScannerCapabilities` XMLからPlaten/ADFの最大幅・最大高さ・対応DPIリスト・カラーモードを抽出するパーサを実装
- Brother製eSCL機器と汎用eSCL機器のXMLフィクスチャを用いた単体テストを追加

## 影響範囲・備考
- 対象機器: Brother eSCL対応機（最優先検証）および汎用eSCLスキャナ
- eSCL標準単位（1/300 inch）からミリメートル（mm）への相互変換ユーティリティを含む
```

---

### タスク 3: スキャナ最大サイズに基づく定型用紙サイズ（A4・B5等）判定ロジックの実装

- **状態**: `[x] 完了`
- **実装内容**:
  - JIS / ISO 定型サイズ（A4: 210×297mm, B5 JIS: 182×257mm, A5: 148×210mm, レター: 215.9×279.4mm, ハガキ: 100×148mm 等）の定義テーブルを作成する
  - `ScannerCapabilities` で検知した `MaxWidth` / `MaxHeight` と照合し、そのスキャナで読み取り可能な定型サイズ一覧＋「最大サイズ（ガラス面全体）」を算出する `PaperSizeResolver` を実装する
- **提案コミットメッセージ**:

```text
feat(scan): スキャナ最大サイズから選択可能な定型用紙サイズ(A4/B5等)を算出する機能を実装

## 背景・目的
スキャナが対応する最大読み取りサイズを自動検知し、A4やB5などの一般的な用紙サイズの中からその機器で利用可能な選択肢だけを安全にユーザーへ提示できるようにするため（REQ-002 タスク3）。

## 変更内容
- `src/open_escl_client/core/paper_size.py` を追加し、A4・B5（JIS）・A5・レター・ハガキ等の定型寸法とeSCL座標単位（1/300 inch）の変換定義を実装
- スキャナの `MaxWidth` / `MaxHeight` に収まる定型サイズのみを抽出し、「最大サイズ（複数枚配置・自動トリミング用）」とあわせて返す `PaperSizeResolver` を実装
- 最大サイズがA4の機器・A3の機器・コンパクトスキャナそれぞれの境界値テストを追加

## 影響範囲・備考
- 汎用eSCLスキャナの能力に応じた動的なサイズリスト生成が可能になる
```

---

### タスク 4: スキャナ選択・用紙サイズ選択・解像度選択の QML UI 連携

- **状態**: `[ ] 未完了`
- **実装内容**:
  - `ScannerSidebar.qml` に「スキャナ検索・選択コンボボックス」「給紙元（原稿台 / ADF）」「用紙サイズ選択（A4, B5, 最大サイズ等）」「解像度（DPI）選択」を宣言的に配置し、`AppViewModel` と双方向バインディングする
- **提案コミットメッセージ**:

```text
feat(ui): スキャナ検出結果と対応用紙サイズ・解像度のQML選択UIを連携

## 背景・目的
検出したスキャナを選択した際に、そのスキャナがサポートする用紙サイズ（A4、B5、最大サイズ等）や解像度（DPI）が即座にUIの選択肢に反映されるようにするため（REQ-002 タスク4）。

## 変更内容
- `ScannerSidebar.qml` にスキャナ一覧、給紙方式、用紙サイズ、読み取り解像度の選択コントロールを追加
- `AppViewModel` にスキャナ選択時の `ScannerCapabilities` 非同期取得と用紙サイズリスト更新処理を実装

## 影響範囲・備考
- 対象OS: macOS / Windows 共通（QML UI）
```
