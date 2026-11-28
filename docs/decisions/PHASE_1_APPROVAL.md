# Phase 1 の開始の承認

> 文書の状態：**APPROVED**（オーナーの承認を記録したもの）　記録日：2026-11-26
>
> - この文書は、オーナーから伝えられた承認の内容を、Claude Code が記録したものである。
> - 内容を変えるには、オーナーの承認が必要である。
> - `OWNER_DECISIONS.md` の D1〜D4 は、変えていない。

## 1. 承認の時点

| 項目 | 内容 |
|---|---|
| 承認の時点の設計の SHA | `0d930d643eba327c398c47a069cb47cf0ac3a87c`（ブランチは `docs/phase-0-design`。Draft PR #1） |
| 伝えられた経路 | ChatGPT が書いた開発の指示（オーナーの正式な承認として伝えられた） |

## 2. 承認された事項

### 2.1 Phase 1 の開始

- Agent Core の Phase 1 の実装を始めることが、承認された。
- 進め方は、`docs/design/phase-0/PHASE_1_IMPLEMENTATION_PLAN.md` の WP の単位とする。1回の指示で実装するのは、その指示が認めた WP だけとする。

### 2.2 技術の構成

| 項目 | 承認された内容 |
|---|---|
| OS | Linux を優先する。Windows は後から対応する |
| 言語 | Python 3.12 |
| パッケージの管理 | uv |
| データベース | SQLite（WAL）。Python の標準ライブラリの `sqlite3` を使う |
| データの検証 | Pydantic v2 |
| 試験 | pytest |
| Lint とフォーマット | ruff |
| 型の検査 | mypy |
| アーキテクチャの検査 | import-linter |
| CI | GitHub Actions（無料の枠の中だけ） |
| アーキテクチャ | モジュラーモノリス、Ports & Adapters |

- 依存関係は、上の表の直接の依存関係（実行時は Pydantic v2、開発時は pytest、ruff、mypy、import-linter）と、それらに必要な、通常の推移的な依存関係だけが承認された。
- 具体的な版、ライセンス、互換性は、`docs/adr/0006-toolchain.md` に記録する。

### 2.3 Phase 1 の実 API の費用

- **0 ドル。** 実 API の呼び出し、認証情報、外部のサービスへの接続は、Phase 1 では使わない（D1 と同じ方向）。

### 2.4 監査と審査の結果の受け入れ

- **Codex による独立監査の判定は、CONDITIONAL_PASS のままである**（対象は `e533c4c`。CRITICAL 0、HIGH 0、MEDIUM 4、LOW 0）。
- 指摘 AUDIT-01〜04 への修正（`0d930d6`）と、その後の ChatGPT による審査を、オーナーが受け入れた。
- **Codex が再び監査して合格したのではない。**
- AUDIT-01〜04 は、設計で解決し、実装による検証を待っている状態のままである（`docs/reviews/CODEX_PHASE_0_AUDIT.md`）。

### 2.5 A-P6 の延期

- 初回 Phase 0 報告書の全文との照合（A-P6）を、延期することが承認された。
- **A-P6 は、解決していない。延期が承認された、未完了の事項として残す。**

### 2.6 開発の体制

| 役割 | 担当 |
|---|---|
| 技術の調査、設計、実装、試験、デバッグ、修正、コミット、Push | Claude Code |
| 開発の指示、設計の審査、実装のレビュー | ChatGPT |
| 重要な段階での、独立した監査だけ | Codex（実装や修正は委任しない） |
| 最終の意思決定と承認 | オーナー |

### 2.7 ブランチの構成

- 設計：`docs/phase-0-design`（Draft PR #1。マージしない）。
- 実装：`impl/phase-1-foundation`。この承認の記録を含むコミットから作る。
- 実装の Draft PR を作るときは、ベースを `docs/phase-0-design` にする。
- `main` への直接の Push、Force Push、無断のマージは、禁止のまま。

## 3. 承認されていない事項

次の事項は、今回の承認に**含まれない**。それぞれ、別にオーナーの承認が必要である。

- D1 の補足、D5、I10 の書き換え（実 API に関するもの。文案は `E_INTEGRATION.md` の31.3）
- 実 API の予算、LiveProvider を有効にすること、実 API の認証情報
- 予算の管理に使うタイムゾーン（AUDIT-02）
- 上の 2.2 にない依存関係（例：Anthropic SDK、OpenAI SDK、LangChain、LiteLLM、MCP SDK、FastAPI、Next.js）
- Docker（Phase 2 の候補）、FastAPI と Next.js（Phase 8 の候補）の採用
- ADR-0003、ADR-0004 と、ADR-0005 のうち holdout の分離以外の部分
- 有料のサービスの利用、GitHub Actions の無料の枠を超える利用
- holdout のリポジトリの作成と接続
- Service Harness、複数のエージェント、Improvement Engine、Memory Engine の実装
- Phase 2 以降の開始
