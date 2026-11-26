# 進捗状況（進捗の正本）

> 文書の状態：**DRAFT**　最終更新：2026-11-26
>
> この文書は、**進捗の正本**である（CP-6）。次のセッションでは、まずこの文書を読む。
> - 予定の正本は `docs/design/phase-0/ROADMAP.md`。
> - 資料が食い違うときに、どれを正本とするかの決まりは、`E_INTEGRATION.md` の28章。
> - 以前の版にあった、各資料の残存課題の一覧は、`RESOLUTION_MATRIX.md` の8章（最終の一覧）にまとめた。各資料の本文にも残っている。

## 1. 現在の段階（次のセッションへの申し送り）

| 項目 | 内容 |
|---|---|
| 今の段階 | **Phase 1 の開始を、オーナーが承認した**（2026-11-26。`docs/decisions/PHASE_1_APPROVAL.md`）。今の作業は **WP-01（プロジェクトの構成と CI）だけ**。WP-02 以降は、まだ認められていない |
| 次の作業 | WP-01 を `impl/phase-1-foundation` で実装し、ChatGPT による独立したレビューを待つ |
| Phase 1 の開始の条件 | **満たした**。Codex の監査（CONDITIONAL_PASS のまま）と、修正の後の ChatGPT の審査を、オーナーが受け入れた。承認の時点の設計の SHA は `0d930d6` |
| 作業ブランチ | 設計：`docs/phase-0-design`（Draft PR #1。マージしない）。実装：`impl/phase-1-foundation`（承認の記録を含むコミットから作る）。`main` は、空の初期コミットだけ |
| 禁止事項（変わらない） | 承認されていない WP の実装、承認されていない依存関係、有料 API と有料のサービス、サブエージェント、holdout のリポジトリの作成と接続、`OWNER_DECISIONS.md` を無断で変えること、`main` への直接の Push、Force Push、マージ（全文は `AGENTS.md`） |
| 参照する資料 | `AGENTS.md`、`docs/decisions/OWNER_DECISIONS.md`、`docs/decisions/PHASE_1_APPROVAL.md`、`ROADMAP.md`、`E_INTEGRATION.md`、`RESOLUTION_MATRIX.md`、`PHASE_1_IMPLEMENTATION_PLAN.md`、`docs/adr/`、`docs/reviews/PHASE_0_REVIEW.md`、`docs/reviews/FINAL_AUDIT_CHECKLIST.md`、`docs/reviews/CODEX_PHASE_0_AUDIT.md` |

## 2. 作業単位の進捗

| 単位 | 範囲 | 状態 | 文書 |
|---|---|---|---|
| A | アーキテクチャ・Runtime・障害復旧 | 設計段階で合格（98/100） | `docs/design/phase-0/A_RUNTIME.md` |
| B1 | Context Engine | 設計段階として条件付き合格（95/100） | `docs/design/phase-0/B1_CONTEXT.md` |
| B2 | Memory Engine | 設計段階として条件付き合格（96/100） | `docs/design/phase-0/B2_MEMORY.md` |
| C1 | 権限・ツールの実行・Sandbox・外部通信 | 設計段階として条件付き合格（95/100） | `docs/design/phase-0/C1_SAFETY.md` |
| C2 | 機密・暗号化・削除・監査・評価環境 | 設計段階として条件付き合格（94/100） | `docs/design/phase-0/C2_DATA_SECURITY.md` |
| D | 評価・統計・Cost Engine | 設計段階として条件付き合格（95/100） | `docs/design/phase-0/D_EVALUATION_COST.md` |
| E1 | 課題の棚卸しと、共通のアーキテクチャ | 統合の準備として条件付き合格（92/100） | `E_INTEGRATION.md` の第1部 |
| E2 | 実行の経路・Safety・Cost・共通の接続口 | 設計段階として条件付き合格（94/100） | `E_INTEGRATION.md` の第1部 |
| E3 | 保存・削除・バックアップ・鍵・復元 | 設計段階として条件付き合格（92/100） | `E_INTEGRATION.md` の第2部 |
| E4 | ロードマップ・残存課題の解決・最終の整合 | 89/100（Phase 0 の全体は 94/100）。条件付き合格 | `E_INTEGRATION.md` の第3部、`ROADMAP.md`、`PHASE_1_IMPLEMENTATION_PLAN.md`、`docs/reviews/FINAL_AUDIT_CHECKLIST.md`、`RESOLUTION_MATRIX.md` の7〜9章 |

| 最終修正 | F01〜F07（`RESOLUTION_MATRIX.md` の10章） | Codex の監査で CONDITIONAL_PASS | `E_INTEGRATION.md` の第4部、`ROADMAP.md` の0.1・0.2、`PHASE_1_IMPLEMENTATION_PLAN.md`、`docs/adr/`、`AGENTS.md` |
| **Codex の監査への対応** | AUDIT-01〜04（`RESOLUTION_MATRIX.md` の11章） | **DRAFT。確認を待っている** | `E_INTEGRATION.md` の第5部（38〜42章）、`docs/reviews/CODEX_PHASE_0_AUDIT.md` |

- 合格は、どれも設計段階についてのものである。実装、実 API への接続、マージを承認したものではない。
- 「実装による検証待ち」の課題は、実装して試験するまでは、解決したとは言わない。

## 3. オーナーの方針（2026-11-26）

1. Agent Core は、オーナー自身が使うための、専用の AI の基盤である。
2. 優先する順は、品質、安全性、信頼性、検証可能性、性能、費用の効率、自律性、拡張性。
3. 商用化、課金の仕組み、一般への販売、企業向けの大規模な仕組みは、必須の目標ではない。
4. 初期は、単一のホスト、単一のオーナー、単一のエージェント、モジュラーモノリスにする。ただし、安全のためのプロセスの分離と、独立した評価の環境は保つ。
5. スマートフォンからの状態の確認、承認、停止と、長時間の稼働を重視する。最小限の遠隔操作は、Phase 4 で先に入れる。認証、権限、通信の保護が前提。
6. Service Harness は、本体が完成した後の、独立したサブ機能とする。本体の完成の条件には含めない。

## 4. オーナーの承認を待っている、主な事項

| 時期 | 事項 |
|---|---|
| Phase 1 を始める前 | **承認済み**（`docs/decisions/PHASE_1_APPROVAL.md`）。ADR-0003・0004 と、Docker、FastAPI、Next.js は、承認されていない |
| 実 API を使う前 | 次の事項。<br>- 予算の管理に使うタイムゾーン（AUDIT-02）<br>- D1 の補足の書き換え（循環を解く。文案は `E_INTEGRATION.md` の31.3）<br>- D5（Run ごとの承認と、月額の上限。文案は同じ）<br>- I10 の書き換え<br>- LiveProvider を有効にする条件<br>- 費用の照合の許容誤差<br>- Anthropic のデータの扱いの方針の確認<br>- 評価の基準と、候補の値<br>- 評価の側の環境 |
| Memory を実装する前 | バックアップの保存の期間、RPO、RTO、独立した複製と証人の置き場所、暗号のライブラリ |

- 全体の一覧は、`RESOLUTION_MATRIX.md` の9章にある。

## 5. 元資料の状況

- 初回 Phase 0 報告書の**全文は、Claude Code の側では受け取っていない。** 使ったのは、独立審査者が伝えた部分だけ。具体的には次のとおり。
  - Context と Memory の章の要約
  - 評価の環境の方針
  - 第7章の Engine の一覧
  - Planning、Model Gateway、Improvement、Orchestration の方針
  - Run ごとの承認と、月額の上限についての、オーナーの決定
- **全文との照合は、行っていない**（A-P6。未完了。延期をオーナーが承認した）。

## 6. リポジトリの状況

- `main`：空の初期コミットだけ（`aa089ae`）。GitHub の既定のブランチ。
- `docs/phase-0-design`：設計資料の作業ブランチ。Draft PR #1 で、`main` への変更として提案している。マージはしない。
- `impl/phase-1-foundation`：Phase 1 の実装のブランチ。Phase 1 の承認の記録を含む、設計のコミットから作る。
- holdout のリポジトリ `ken1700-mizui/agent-core-holdout`：作っておらず、接続もしていない。
