# WP2-01 型・契約・評価の記録の基盤と Journal v4（実装の記録）

> 文書の状態：**DRAFT**（実装と試験の記録。レビュー待ち）　作成：2026-11-28
> 承認：D16（Phase 2 の開始。今回の実装の範囲は WP2-01・02 だけ。O-3、O-4）
> 設計：`PHASE_2_EVALUATION_CONTRACT.md`（2章、2.4、2.5、3.2、3.2.4、7.8）

## 1. 実装したもの

| 対象 | ファイル | 内容 |
|---|---|---|
| ID | `types/ids.py` | `TrialId`（`trl`）、`HomeId`（`hom`）、`BrokerInstanceId`（`brk`。WP2-02 で使う） |
| 評価の型 | `types/evaluation.py` | `TaskSpec`、`ConfigSpec`、`ClassificationRules`、`ExperimentBudget`、`ExperimentSpec`、`TrialRecord`、`CostReconciliation`、`ScoreRecord`、`AnalysisResult`、`Decision`、集計（`cost_summary`、`safety_summary`、`success_counts`）、接続口の要求の型（`GradingRequest`、`PlanRequest`、`PlanProposal`） |
| provenance の型 | `types/provenance.py` | `RunProvenance`、`StepProvenance`、`AttemptProvenance`、`OutcomeProvenance`（呼び出し側が渡す `*Fields` と、実体の ID に結び付けた記録） |
| Journal v4 | `types/transitions.py`、`types/events.py`、`runtime/state_machine.py` | `RuntimePayload.provenance`（空なら直列化に現れない）。版 4 を読み書きする。v3 に provenance があれば拒否。Rebuild で v4 の後の v3 を拒否 |
| provenance の索引 | `runtime/provenance.py` | 派生の索引（正本ではない、`RuntimeState` に入れない）。書き込みの側の検査（`check`）、値のない記録の自動の付加（`markers`）、読み取りの側の記録（`add`、`describe`） |
| Runtime | `runtime/service.py` | `start_run`・`create_step`・`advance_attempt` に `provenance` の引数。版 4 で書く。評価の Run で必要な記録が欠ければ、何も書かずに `PROVENANCE_REQUIRED`。`load` で索引を作り直す |
| 接続口 | `ports/evaluation.py`、`ports/sandbox.py`、`types/sandbox.py` | `EvaluationStorePort`、`GraderPort`、`PlannerPort`、`SandboxPort`（`prepare`・`start`・`status`・`stop`・`collect`・`reap`）と要求・結果の型 |
| Registry と Results Store | `adapters/sqlite_results.py` | 別の SQLite。追記だけ（更新・削除はトリガーで拒否）、ハッシュの連鎖、開くときの全件の再検査。`freeze`（`spec_hash`）、dev のタスクだけ、凍結した規則による記録の検査 |
| import の契約 | `pyproject.toml` | 新しい契約 `evaluation-records-off-the-agent-path`：Agent の経路（runtime、tool、safety、cost、context、identity、observability、CLI）は、評価の記録の保存と接続口を import しない（D.V4、D.V8）。契約は 5 → 6 |

## 2. 評価の記録の仕様（要点）

- **費用：** 試行の費用は、確定の状態で1回だけ分ける（`settled_xtu` と、予約ごとの `held`）。有効性（`INVALID_ENV` か）とやり直し（`retry_of`）は、試行の別の属性。2.4 の4つの区分は**派生の集計**（`cost_summary`）で、足し合わせられるのは確定の合計と保留の合計だけ。`retry_xtu` は有効・無効の区分の部分集合で、合計に足さない（二重の計上を防ぐ）。
- **後からの照合：** `CostReconciliation` を追記する（試行の記録は書き換えない）。`settled_xtu + released_xtu` が保留の額と等しいこと、`(trial_id, reservation_ref)` ごとに1回だけを確かめる。
- **結果不明の費用：** 保留として残り、確定の 0 にはならない。上限の見積もり ＝ 確定 ＋ 保留。
- **安全：** 違反は `INVALID_ENV` の試行も含めて数える。`INVALID_ENV` が外れるのは成功率の分母からだけ。
- **成功：** `objective_success`（採点の結果）と `safe_completion`（成功、違反 0、未解決 0、Run の正常な終わり）は派生の値で、書き込めない。
- **分類の規則：** `INVALID_ENV` は `ENV` か `PROVIDER_OUTAGE_BLOCK` の帰属だけ（Agent の障害を `INVALID_ENV` にしない）。`invalid_reason` は凍結した規則の一覧の内だけ。`TIMEOUT` と `BUDGET_EXCEEDED` は対応する上限の記録が要る。`NOT_GRADABLE` は `GRADING_IMPOSSIBLE` か Agent の `FAIL`。
- **ホームの数：** 費用ではなくイベントの数から決める（`required_homes`）。`max_homes` が足りない ExperimentSpec は作れない。費用 0 の試行でもホームの数は有限。
- **Results Store：** 凍結した規則のハッシュ、タスク、構成、試行の上限、実験の上限（保留を含む）、やり直しの規則（無効な試行だけ、同じ枠、1回、`max_retries` まで）を確かめる。dev 以外のタスクは入れない。

## 3. Journal v4 の互換性（試験と確かめた結果）

| 項目 | 結果 |
|---|---|
| 実際の旧版の形式 | `tests/fixtures/phase1_v3/`：Phase 1 のコード（`92c18c4`）が書いた実際の Journal（4つの筋書き）の SQL の写しと、そのコードが計算した要約。今のコードで開くと、意味・費用の側・head が一致し、イベントは変わらない |
| 意図的な不整合 | 上の実際の形式から作る（provenance の書き換え、版の逆行、provenance の削除）。実際の旧版の形式とは別の試験 |
| v3 と v4 の混在 | 実際の v3 の Journal の上に新しい作業と Recovery を行うと、v4 で続く。v3 の行は同じバイト。ハッシュの連鎖は版をまたいで続き、再起動で同じ状態 |
| Rebuild の同一性 | 同じ遷移を v3 と v4 で書いた場合に同じ状態。P-7 の同一性の道具（`tools/perf/state_identity.py`）：Phase 1 のコードで作った7つの筋書きを、Phase 1 のコードと今のコードで要約し、**すべて一致** |
| Recovery | 混在の Journal で Recovery（`consumed_and_recover`）と `assess` が動く。評価の Run の中止・結果不明・照合は、値のない記録を Runtime が付けるので、Recovery の経路は変わらない |
| 読み取りだけの状態の確認 | `describe`：以前の版の Run は「not recorded (earlier journal version)」、評価の Run は実験と試行と完全さを示す。読んでもファイルは変わらない |
| 未知の版 | 0、5、99、−1 は `unsupported_schema_version`。版 2 は従来どおり `superseded_schema_version` |
| 旧版のプログラムによる拒否 | **実際の Phase 1 のプログラム（`92c18c4`）で確かめた**（2026-11-28、この環境、手作業）：v4 のイベントを含む Journal を開くと `JournalCorruptionError`（原因 `unsupported_schema_version`）で拒否し、ファイルは変わらない。自動の試験では、読める版を {1, 3} にした読み手で同じことを確かめる（CI は浅い clone のため、古いコミットを自動の試験で使えない） |
| 評価の Run の必須の項目の欠け | 書き込み：Step の作成、内容の確定、結果のどれで欠けても `PROVENANCE_REQUIRED` で何も書かない。呼び出しの種類と Step の種類の不一致、Run と違う試行も拒否。読み取り：作った Journal で記録を消しても開け、状態は同じで、索引がその Run を「不完全」と示す |

## 4. 既存の試験の変更（削除・無効化はない）

v4 で書くことは承認された方式（O-4、設計の 3.2.3「新しい版は v4 だけを書く」）なので、**書く版を v3 と決めていた既存の試験の期待値を v4 に改めた**。

| 試験 | 変更 |
|---|---|
| `test_event_versions.py::test_versions_in_use` | 書く版 3 → 4、読める版 {1, 3} → {1, 3, 4} |
| `test_event_versions.py::test_current_runtime_writes_version_3_with_the_cancel_intent` | 名前を `..._version_4_...` に改め、書く版 {3} → {4}（Run の形の確認は同じ） |
| `test_runtime_service.py::test_runtime_events_survive_the_sqlite_journal_verification` | 保存の本文の版 `"schema_version":3` → `4` |
| `test_types_serialization.py` の1つの場合 | 「未知の版」の例を 4 → 5（4 は実在の版になったため。意図は同じ） |

## 5. 確かめていないこと・制限

- provenance の値（Model Profile、Context のハッシュなど）を Agent のループが実際に渡す経路は、まだない（評価の Run は WP2-12 の Controller が作る）。今は Runtime の API と規則だけ。
- 生きた結果の `wall_ms`（単調な時計）を実行の経路で計って渡すのは、Sandbox の経路（WP2-02 以後）。
- Results Store のハッシュの連鎖は、変更を**見つける**もので、防ぐものではない（ファイルに書ける者は、全体を作り直せる）。予算の正本は台帳（7.1）。
- 複数のホームの台帳の操作（締め、開始、解決）は WP2-08。今回は型の整合（`ExperimentBudget`、`JournalRef.home_id`）だけ。
