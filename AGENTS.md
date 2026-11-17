# open-escl-client プロジェクトガイドライン (AGENTS.md)

本ドキュメントは、Antigravity エージェントが本リポジトリ（`open-escl-client`）で開発・設計・タスク管理を行う際の基本原則を定義します。

---

## 1. プロジェクトの目的と優先順位

- **目標**: **Brother iPrint&Scan** に代わる、モダンで使いやすいスキャナクライアントアプリケーションを開発する。
- **プロトコル**: 特定メーカーの独自仕様に縛られず、**eSCL (AirScan) プロトコル**を用いた汎用的なクライアントアプリケーションを目指す。
- **対象プラットフォーム**: **macOS** および **Windows** の双方で動作するクロスプラットフォーム設計とする。
- **最優先ターゲット**: **「Brother 製スキャナ/複合機 × macOS」** の組み合わせを最優先とする。
  - 初期実装・動作確認・UX最適化は **Brother × macOS** を第一基準として進める。
  - ただし、将来の Windows 対応や他社製 eSCL 対応スキャナへの拡張を妨げないよう、OS 固有処理やベンダー固有挙動はインターフェースで適切に分離・抽象化する。

---

## 2. ディレクトリ構成と要件管理

### 要件定義ディレクトリ (`docs/requirements/`)

開発する機能や改善の要件はすべて [`docs/requirements/`](./docs/requirements/) 配下で管理します。

- [`docs/requirements/drafts/`](./docs/requirements/drafts/)（または `docs/requirements/` 直下）:
  - ユーザーが考えた初期の要件・アイデア・メモを配置する場所です。
- [`docs/requirements/`](./docs/requirements/) 直下（`REQ-XXX-<slug>.md`）:
  - タスク分解スキル（`decompose-requirements`）によって整理・分割された、**「1ブランチ ＝ 1要件ファイル」** 単位の要件定義書を格納します。
  - 各要件ファイルの内部には、**「1タスク ＝ 1コミット」** となる粒度で分解されたタスク一覧、切るべき Git ブランチ名（1種類）、および各タスクのコミットメッセージ案（1種類）を記載します。

---

## 3. 開発ワークフローとカスタムスキル

本リポジトリでは、要件定義から実装・コミット・プルリクエスト作成までを一貫してサポートする以下のスキルとルールを備えています。

### 常時適用ルール (`.agents/rules/`)
- [`project-context.md`](./.agents/rules/project-context.md): アーキテクチャ方針、eSCL プロトコル実装指針、Brother × macOS 最優先の設計ルール
- [`git-conventions.md`](./.agents/rules/git-conventions.md): 日本の開発現場のベストプラクティスに準拠した Git ブランチ命名規則、PR 本文へそのまま転用できる構造化コミットメッセージ規約、PR テンプレート規約

### ワークスペーススキル (`.agents/skills/`)
1. **`decompose-requirements`** ([`.agents/skills/decompose-requirements/SKILL.md`](./.agents/skills/decompose-requirements/SKILL.md))
   - ユーザーが記述した要件を読み込み、1つのブランチで実施すべきかを判定します。
   - 複数ブランチに分割すべき場合は、ブランチ単位で適切な数の要件ファイル（`docs/requirements/REQ-XXX-<slug>.md`）に分解します。
   - 各要件ファイル内にコミット単位のタスクを定義し、**ブランチ名（1種類）** と **各タスクのコミットメッセージ（1種類）** を記載します。
   - また、**分解して作成した要件ファイル自体をコミットするためのブランチ名（1種類）とコミットメッセージ（1種類）** をチャット画面に提案します。
2. **`propose-git-commit`** ([`.agents/skills/propose-git-commit/SKILL.md`](./.agents/skills/propose-git-commit/SKILL.md))
   - 指定された要件タスクの実施時や変更差分（`git diff` / `git status`）に基づき、切るべき **Git ブランチ名（1種類）** と、プルリクエストの内容をそのまま記載できるレベルの **コミットメッセージ（1種類）** をチャット画面に提案します。
3. **`propose-pull-request`** ([`.agents/skills/propose-pull-request/SKILL.md`](./.agents/skills/propose-pull-request/SKILL.md))
   - 対象ブランチの要件ファイル、コミット履歴（`git log`）、変更差分から、日本の開発現場で定評のあるテンプレート（[`.github/pull_request_template.md`](./.github/pull_request_template.md)）に準拠した **プルリクエストのタイトルと本文（1種類）** をチャット画面に提案します。

### チャット出力の原則
- ファイルとして出力した内容は、改めてチャット画面に再掲・要約表示せず、ファイルへのリンクのみを提示します。
- 一方で、**ブランチ名・コミットメッセージ・プルリクエストのタイトルと本文** は必ずチャット画面に表示します。
