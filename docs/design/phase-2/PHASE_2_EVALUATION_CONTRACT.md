# Phase 2 評価の契約（D.V19 の記録の形式、A.I11 の Step の記録、公平な比較、統計）

> 文書の状態：**DRAFT**（設計案）　作成：2026-11-28　改訂：2026-11-28（オーナーの設計の審査 CONDITIONAL_PASS への対応）
>
> - オーナーの承認（D15）：D.V19 の拡張の項目（`unresolved`、`grading_status`、`limits_hit`）の追加の方針、Journal v4 による provenance の記録の方針、実験を複数のホームに分ける方針、Task Catalog の作り方（dev と validation）。この改訂の最終の受入は承認待ち。
>
> - 正本：`D_EVALUATION_COST.md`（3章のデータ構造、4章の比較の相手と公平性、6章の分割、7章の採点、8章の統計、13章の再現性、15章の不変条件、16章のゲート）、`E_INTEGRATION.md` の10章、`A_RUNTIME.md` の2章（A.I11）。
> - **D.V19 と D の7.1 の正本の定義は変えない。** この文書は、その定義に従う具体的なデータの形式の提案。正本にない項目は「拡張」と書き、オーナーの承認を求める。
> - **Fake の上での成功を、実モデルの性能の向上の証明として扱わない**（ROADMAP の性能の追跡、D の14章）。

## 1. 記録の全体（D の3章）

| 記録 | 書く主体 | 置き場所（Phase 2） | 変えられる主体 |
|---|---|---|---|
| TaskSpec | オーナー（Task Catalog） | 開発のリポジトリ（dev、validation だけ） | オーナーだけ（D.V8。AI は変えない） |
| ConfigSpec | 評価の側（Runner） | Experiment Registry | 登録の後は変えない |
| ExperimentSpec | オーナーが承認、評価の側が登録 | Experiment Registry（`spec_hash` で固定） | 固定の後は変えない（D.V1） |
| TrialRecord | 評価の側（Controller） | Results Store | 追記だけ |
| ScoreRecord | 採点のプログラム（評価の側） | Results Store | 追記だけ |
| AnalysisResult、Decision | Statistics Engine、オーナー | Results Store | 追記だけ |

- **Results Store と Experiment Registry** は、Agent の Journal とは**別の SQLite のファイル**（評価の側の領域）にする。Agent の Run は、自分の評価の記録を書けない（D.V4、D.V8）。各記録は、対応する Agent の Journal の head（`tenant_seq`、`head_hash`）と `run_id` を参照する。
- 評価の記録も追記だけで、ハッシュの連鎖を持つ（Phase 1 の Journal の方式を再利用する）。削除と切り詰めはしない。

## 2. D.V19：タスクの結果と安全の結果の記録の形式

### 2.1 TrialRecord（D の3.4 の項目に、型を付けたもの）

```
TrialRecord {
  trial_id, experiment_id, config_id, task_id, task_version,
  repo_cluster, sequence_id?, stratum, time_block, repeat_index, order_index, seed,
  run_id, journal_ref {home_id, tenant_seq, head_hash},          # Agent の Journal の位置
  started_at_ms, ended_at_ms, wall_ms,
  task_outcome: SUCCESS | PARTIAL | FAIL | TIMEOUT | BUDGET_EXCEEDED | INVALID_ENV,   # D の7.1。変えない
  safety {
    violations: [ {code, invariant_ref, evidence_ref} ],         # 安全性の違反（D の7.1）
    blocked_attempts: [ {deny_reason, attempt_ref} ]             # 通常の認可の拒否（違反ではない）
  },
  failure_attribution: ENV | AGENT | PROVIDER_TRANSIENT | PROVIDER_OUTAGE_BLOCK,   # D の7.2
  invalid_reason?: <評価の側の固定の符号>,                        # task_outcome = INVALID_ENV のときだけ（D.V18）
  tokens {input, output, total}, cost_entries[], cost_at_success?, # D の10.2、XTU
  human_minutes,                                                   # Phase 2 は 0
  # --- 拡張（正本にない項目。オーナーの承認を求める）---
  unresolved { count, attempt_refs[] },                            # 結果不明の Attempt
  grading_status: GRADED | NOT_GRADABLE,                           # 採点できたか
  limits_hit: [ TIME | BUDGET | STEPS | CALLS | CONTEXT | EVENTS ], # どの上限で止まったか
  # --- 改訂で加えた派生の値と費用の区分（2.4。評価の側が計算する）---
  objective_success: bool,                                         # 採点の結果（task_outcome = SUCCESS）
  safe_completion: bool,                                           # 成功 かつ 違反 0 かつ 未解決 0 かつ 正常な終わり
  cost { settled_xtu, held_unknown_xtu, invalid_trial_xtu, retry_xtu },
  retry_of?: trial_id,                                             # やり直しの元の試行
  classification_rules_hash                                        # 分類の規則の版（spec_hash に含まれる）
}
```

- `task_outcome` の値は、D の7.1 の6つだけ。新しい値は作らない。
- 分類（`task_outcome`、`failure_attribution`、`invalid_reason`）は、**評価の側**（Controller と採点のプログラム）が、事前に決めた規則で付ける。Agent は付けられない（D の7.2、D.V4）。

### 2.2 9つの区分の扱い

| 区分 | 記録のしかた | 成功率での扱い |
|---|---|---|
| タスクの成功 | `task_outcome = SUCCESS` かつ `safety.violations = []` | 成功 |
| タスクの失敗 | `FAIL` または `PARTIAL`（`PARTIAL` は品質の指標で部分の点数を使う） | 失敗 |
| 正常な安全上の拒否 | `safety.blocked_attempts` に記録する。`task_outcome` とは独立 | 成功率に影響しない（安全の副指標。比較の相手より増えていないかを見る。D の9.2） |
| 安全性の違反 | `safety.violations` に記録する | 同じ試行が `SUCCESS` でも**失敗**として数える（D の7.1）。1件でもあれば Safety Gate に落ちる |
| 評価の環境の異常 | `INVALID_ENV` と `invalid_reason`（例：`GRADER_CRASH`、`RUNNER_FAILURE`、`SANDBOX_HOST_FAILURE`、`SELF_DIAGNOSIS_FAILED`、`PROVIDER_OUTAGE_BLOCK`） | 集計から外す。件数、理由、**費用**を報告する（D.V18） |
| 評価不能 | `grading_status = NOT_GRADABLE`。原因で分ける：評価の側なら `INVALID_ENV`（`invalid_reason = GRADING_IMPOSSIBLE`）、Agent が作業ツリーを壊したなどなら `FAIL`（`failure_attribution = AGENT`） | 評価の側なら外す。Agent なら失敗（D の7.2。Agent の障害を `INVALID_ENV` にしない） |
| 結果不明 | `unresolved.count > 0`（試行の終わりに OUTCOME_UNKNOWN の Attempt が残った）。`task_outcome` は採点の結果で決める。原因が評価の側（Sandbox のホストの障害）なら `INVALID_ENV` | 採点の結果のとおり。別に件数を報告する。結果不明を成功にも失敗にも**推定で**寄せない |
| 時間の上限 | `TIMEOUT`、`limits_hit` に `TIME` | 失敗。件数を別に報告する |
| 予算の上限 | `BUDGET_EXCEEDED`、`limits_hit` に `BUDGET` | 失敗。件数を別に報告する |

### 2.3 集計との接続（D の8章）

- **成功率：** 分子は「`SUCCESS` かつ違反 0」の試行。分母は `INVALID_ENV` を除いた試行。タスクごとの成功の確率を出してから、層の重みで平均する（D の8.2。試行を独立な標本にしない、D.V2）。
- **無効な試行：** 事前に決めた回数（候補 2 回）までやり直す。残った分は欠けたデータとして報告し、最悪の場合の感度の分析を行う（D の8.6）。構成の間で無効の割合が閾値（候補：差 5 ポイントか比 2 倍）を超えたら、その比較は正式な判定に使わない（D の7.5）。
- **除外：** 除外できるのは `INVALID_ENV` だけ。事前登録の規則にない除外はしない。
- **安全：** `violations` の件数は構成ごとに報告し、0 件が Safety Gate の条件。`blocked_attempts` は維持すべき指標として比べる。
- **費用：** 無効な試行の費用も実際の支出に数える（D の7.2）。成功1件あたりの費用は、失敗を含めた総費用で計算する（D.V9）。

### 2.4 分類の規則の明確化（改訂。事前に固定し、結果を見た後に変えない）

1. **タスクの成功と安全性の違反は、別の項目に記録する。** `task_outcome` は採点の結果だけで決め、`safety.violations` は安全の確認だけで決める。一方から他方を書き換えない。成功率の計算のときだけ「違反のある `SUCCESS` は失敗」と数える（D の7.1）。
2. **安全性の違反のある試行は、`INVALID_ENV` として除外しても、安全の評価から消えない。** 安全の集計（違反の件数、拒否の件数）は、`INVALID_ENV` を含む**すべての試行**で行う。`INVALID_ENV` で外すのは、タスクの成功率の分母からだけ。違反のある試行を `INVALID_ENV` にする場合は、その原因が評価の側にあることの証拠（`invalid_reason` と根拠の参照）が要り、違反の記録は残す。
3. **未解決の操作が残る試行：** 2つの派生の値を分けて記録する。
   - `objective_success`：採点のプログラムの結果（`task_outcome = SUCCESS`）。
   - `safe_completion`：`objective_success` かつ `violations = []` かつ `unresolved.count = 0` かつ Run が正常に終わった。
   - 主な成功率は事前登録で決める（推奨：`objective_success` かつ違反 0。D の7.1 のとおり）。`safe_completion` の率を別に報告する。未解決の試行の件数を必ず示す。
4. **結果不明の費用を、確定の費用 0 として扱わない。** 保留が残る費用は `held_unknown_xtu` に入れ、確定の費用（`settled_xtu`）に含めない。費用の指標は、確定の費用と、保留を上限として足した値（上限の見積もり）を両方報告する。照合で確定したら、確定の費用に移す（重複して数えない）。
5. **Agent 自身の障害を、評価の環境の異常に分類しない**（D の7.2、D.V18）。`INVALID_ENV` にできる原因の一覧（`invalid_reason` の固定の符号）を ExperimentSpec に含め、それ以外は Agent の側の結果にする。
6. **費用を4つに分けて追跡する：**

| 区分 | 内容 | 予算と集計での扱い |
|---|---|---|
| `settled_xtu` | 確定した費用（有効な試行） | 実験の予算から引く。費用の指標に使う |
| `held_unknown_xtu` | 結果不明などで保留が残る費用 | 実験の予算の**残りから引いたまま**にする（解放しない）。確定するまで費用の指標では上限の見積もりとしてだけ示す |
| `invalid_trial_xtu` | `INVALID_ENV` の試行の費用 | 実際の支出として予算から引く。費用の指標とは別に報告する |
| `retry_xtu` | 無効な試行のやり直しの費用 | 実際の支出として予算から引く。別に報告する |

- 分類の規則の版のハッシュ（`classification_rules_hash`）を ExperimentSpec の `spec_hash` に含める。固定の後に変えた実行は拒否する（D.V1）。

## 3. A.I11：Step の記録の項目

### 3.1 3つの区分

| 項目 | 今（Phase 1）の記録の場所 | Phase 2 で加えるもの | 後の Phase |
|---|---|---|---|
| Journal の位置（`run_id`、`run_seq`、`tenant_seq`、`prev_hash`、`schema_version`、`owner_epoch`） | **Journal の全イベント**（記録済み） | — | — |
| Runtime の版（`engine`） | Journal の全イベント（記録済み） | — | — |
| ポリシーの版（`policy_version`） | Journal の全イベント（記録済み）、RunGrant | — | — |
| Task と Run と Step の対応 | Journal（`run_steps`、`task.runs`。記録済み） | — | — |
| ツールの版（`tool_id@version@manifest_hash`、`artifact_hash`） | 台帳の `ActionDescriptor`（記録済み、`action_hash` に結び付く） | Sandbox のツールの `ToolRegistration` を同じ形で | MCP（Phase 4） |
| Model Profile | RunGrant の `model_profiles`、モデルの呼び出しの `request_hash` | Step ごとの `model_profile_ref` | 実モデルの ID、版、日付（Phase 3B） |
| Context の識別子・ハッシュ | Context の組み立ての結果（`context_hash`。WP-14）。Step のイベントには**ない** | **Step ごとの `context_hash`** | 記憶の参照（Phase 5） |
| Prompt の識別子 | なし | **`prompt_template_ref`（テンプレートの版のハッシュ）** | — |
| 構成の識別子 | なし | **`config_hash`**（ConfigSpec） | 3A で固定 |
| Sandbox の設定 | なし | **`sandbox_profile_hash`**、イメージのダイジェスト | OS のレベルの隔離（3A） |
| 評価のデータと規則の版 | なし | **`task_ref`（TaskSpec の版のハッシュ）、`eval_set_version`、`grader_version`、`experiment_id`、`trial_id`** | holdout の版（3D。開発の側には返さない） |
| 使用量と XTU | 台帳の使用量の報告と予約（`spent_micro` など。記録済み） | — | 実際の請求との照合（3B） |
| 処理の時間 | 各イベントの `recorded_at_ms`（記録済み。人のための時刻） | **Attempt の `wall_ms`（単調な時計）** | — |
| 結果と検証の結果 | 台帳の `operation_results`、Verification の証拠（記録済み） | Sandbox の実行の終了の状態、出力の要約のハッシュ | — |

### 3.2 Journal v4 の記録の形式（方針はオーナーが承認、D15。形式の最終の受入は承認待ち）

**原則：** 確定した値だけを、それが確定する時点の遷移に記録する。まだ決まっていない将来の値を、Step を作るときに記録しない。過去のイベントを書き換えない。

#### 3.2.1 3つの時点と配置（既存の設計と照合して決めた）

| 時点 | 記録する値 | 置き場所 | 既存の設計との関係 |
|---|---|---|---|
| **① Step の作成**（Step の CREATE の遷移） | 構成の版（`config_hash`）、Task の参照（`task_ref`：TaskSpec の版のハッシュ）、評価の版（`eval_set_version`、`grader_version`、`experiment_id`、`trial_id`）、計画と設定の参照（`plan_ref`、`strategy_refs`：Context・Planning・Verification の Strategy の版） | v4 の `StepProvenance`（Step の CREATE の遷移の任意の区画） | Step の作成のときに確定している値だけ |
| **② モデルの呼び出し・Attempt**（Attempt の `CONTENT_FIXED` の遷移） | 実際に使った Model Profile（`model_profile_ref`）、Context のハッシュ（`context_hash`）、Prompt Template の参照（`prompt_template_ref`）、ツールの版（`tool_ref` ＝ `tool_id@version@manifest_hash`）、Sandbox の設定（`sandbox_profile_hash`、イメージのダイジェスト） | v4 の `AttemptProvenance`（`CONTENT_FIXED` の遷移の証拠の任意の区画）。内容は台帳の `ActionDescriptor` と一致させる | `CONTENT_FIXED` は `action_hash` を確定する時点（Phase 1 の WP-09）。台帳の `ActionDescriptor` は、すでにツールの版、モデルの参照、`request_hash`、費用の根拠を持つ。v4 はその参照を Journal にも残し、A.I11 を満たす |
| ②' **Permit との対応** | `permit_id`、`receipt_id` | **既存**（Attempt の `PERMIT_ISSUED`、`PERMIT_CONSUMED` の遷移と証拠） | 追加しない |
| **③ 実行の終わり**（Attempt の結果の遷移：`COMPLETED`、`FAILED_DEFINITE`、`OUTCOME_UNKNOWN`、`ABORTED_BEFORE_DISPATCH`） | 処理の時間（`wall_ms`、単調な時計）、Sandbox の終了の状態の参照（`exit_ref`）、検証の証拠の参照（`verification_ref`） | v4 の `OutcomeProvenance`（結果の遷移の証拠の任意の区画） | 使用量と XTU は**既存**の台帳（使用量の報告、予約の `spent_micro`、照合）。結果は既存の `OutcomeEvidence`・`report_ref`・`operation_results` |

- `OUTCOME_UNKNOWN` の時点では、使用量と結果は確定していない。確定は照合（`ReconcileOutcome`）の時点で、既存の経路で記録する（未確定の値を先に書かない）。

#### 3.2.2 必須と任意

| 区分 | 扱い |
|---|---|
| Phase 2 の評価の試行（`experiment_id` のある Run） | ①②③ の provenance を**必須**にする。v4 で書く。欠けた試行は、評価の側が実装の不具合として扱い、試験で防ぐ |
| Phase 2 の評価でない Run（CLI の見本の作業など） | v4 で書くが、評価の項目（`experiment_id` など）は任意 |
| 以前の Journal（v3、v1） | provenance は**ない**（任意）。読み取り、Rebuild、Recovery、状態の確認はそのまま。評価の試行としては使わない |

#### 3.2.3 互換性の設計

| 項目 | 設計 |
|---|---|
| v3 と v4 の混在 | 1つの Journal の中で、v3 の後に v4 が続いてよい（ホームを更新した後）。v4 の後に v3 を書かない（新しい版は v4 だけを書く） |
| ハッシュ | イベントのハッシュは、それぞれの版の正規の本文で計算する（Phase 1 の `event_hash` の方式）。v3 のハッシュは**変わらない**（書き換えも計算し直しもしない）。v4 のハッシュは provenance を含む本文を覆う。ハッシュの連鎖は版をまたいで続く |
| Rebuild | v3 と v4 を同じ規則で畳み込む。provenance は状態の意味（Task、Run、Step、Attempt の状態と遷移の規則）を**変えない**。Phase 1 の同一性の試験（S1、`state_identity.py`）を保つ |
| provenance の置き場所 | `RuntimeState` の意味の表には入れない（同一性を保つ）。問い合わせのための派生の索引（正本ではない）にする |
| Recovery | provenance に依らない（既存の経路のまま） |
| 読み取りだけの状態の確認 | provenance があれば示し、なければ「記録なし（以前の版）」と示す |
| 旧版での安全な拒否 | 以前の版のコード（v4 を知らない）は、知らない `schema_version` を**拒否する**（Phase 1 の `_check_schema_version` の動作：開かない、壊さない）。新しい版の試験で、v5 などの未知の版を拒否することを確かめる |
| 書き換えの禁止 | 過去のイベントを v4 に書き換えない。変換の仕組みは作らない |

- **記録しないもの：** プロンプトの本文、ファイルの内容、モデルの出力の本文、認証情報、環境変数の値。識別子とハッシュだけ（C2、A の2章）。機密を含みうる値のハッシュは、Phase 1 の `keyed_hashes` の方式。

## 4. 公平な比較（D の4章）

### 4.1 構成（Phase 2 は Fake の上で）

| 構成 | Phase 2 の内容 | 判定に使うか |
|---|---|---|
| Baseline A（Minimal） | モデルの呼び出し、ツールの実行、履歴の保持（古い順に切り捨て。上位の命令と安全の制約は残す）、終了の条件 | 参考、下限の確認 |
| Baseline B（Competent） | `rg` に当たる検索、範囲を指定した読み込み、編集のツール、基本の文脈の管理（B1.ST1・ST2）、一時的な失敗の再試行、完了を報告する前のテストの実行 | **主な比較の相手。構成は独立審査の対象**（ROADMAP の Phase 2 のオーナーの承認） |
| Agent Core | Context v1、Planning v1、Verification と修正のループ | 評価の対象 |
| Ablation／FeatureComparator | Agent Core から機能を1つだけ変えた構成（E の10.3） | 機能ごとの効果 |

- **安全のために必須の機能は、どの構成でも外さない**（D.V3、E の11章）：PDP と PEP、Permit、Sandbox（自己診断）、予算の強制、B1 の権限の階層、ProtectedFact の安全の制約。
- 3つの構成は、**同じ Runtime、同じ安全の仕組み、同じ Sandbox、同じ Task Catalog、同じ採点のプログラム**の上で動かす。違うのは、文脈の管理、計画、検証、ツールの「設計」の部分だけ（できることの範囲は同じ）。

### 4.2 そろえるものと、記録するもの

- そろえる：モデル（Phase 2 は同じ Fake のモデルとその版、乱数の種の規則）、推論の設定、タスクの入力と完了の条件と始めのファイル、ツールの能力の範囲、Sandbox の設定（`sandbox_profile_hash`）、時間の上限、トークンの予算、XTU の予算、人間の介入の条件（Phase 2 は介入なし）。
- dev で調整に使える手間（試行の回数と費用）を、Baseline B と Agent Core で同じにし、記録する。
- そろえられなかった条件は `condition_deviations` に残す。
- 2つの比べ方を分ける：固定の予算での成功率と品質、品質を達成するまでの費用と時間（達しなかった試行は実際の費用で打ち切り。上限の額を払ったことにしない。D.V20）。

### 4.3 Fake のモデルの扱い

- Phase 2 の「同じモデル」は、**決定的な合成のモデル**（Phase 1 の Fake Provider を広げたもの）。タスクごとに台本（ScriptedProvider）か、入力に応じて決まった規則で応答する方針（例：受け取った文脈に必要な情報があるときだけ正しい修正を返す、決めた確率で誤りを入れる。乱数の種は記録する）。
- これは**評価の仕組みが正しく動くこと**を確かめるためのもので、Agent Core の機能が実際のモデルで効くことの証拠ではない。Phase 2 の結果を性能の向上として報告しない。
- Fake のモデルの方針は、構成ごとに変えない（同じモデル）。方針の版を ConfigSpec の `model_profile@version` に記録する。

## 5. 採点（D の7章）

- コーディングのタスクは、**客観的なテスト**で採点する：隠しテスト、後退していないかのテスト、ビルド。`assurance = blackbox`。
- 採点は、Agent の Run が終わり、反映（5章のファイルの4区分の④）が終わった後の作業ツリーに対して、**別の採点用の Sandbox** で行う。Agent の Sandbox に隠しテストを入れない。
- **置き場所（改訂）：** 開発のリポジトリは公開されている。開発のリポジトリには、dev のタスク（その隠しテストを含む。dev は盲検ではない）、公開してよい型、合成の試験のフィクスチャだけを置く。**validation の本体と隠しテストは置かない**（8章）。
- 採点のプログラムの版（`grader_version`）は、評価セットの版ごとに固定する。1つの実験の中では、すべての構成で同じ採点のプログラムを使う。変える場合はオーナーの承認と、評価セットの新しい版が要る（D の7.3）。
- Agent の自己申告（「完了した」）は採点に使わない（D.V4）。実行中の Verification の結果も採点に使わない（E の7.3）。
- AI による採点（`rubric_ai`）は Phase 2 では使わない（実モデルがないため）。

## 6. Statistics Engine（D の8章、16.1）

### 6.1 方法（事前登録で選ぶ）

- 対応のある比較：タスクごとの差 d_t を求め、層の重みで平均する。
- 信頼区間：クラスタが 30 以上ならクラスタの単位のブートストラップ、少ないならクラスタの単位の並べ替えの検定（D の8.2）。もう一方で感度の分析。
- 判定：3つの判定（方向、非劣性、実用上の効果）をすべて出す（D の8.4）。規則 A と B の両方を登録した場合は Holm の補正（E の10.1）。
- 0 の扱い：E の10.3 の表（両方の成功が 0 なら `INCONCLUSIVE`、品質の指標がなければ `NOT_APPLICABLE`）。

### 6.2 Technical Gate のシミュレーション（D の16.1）

- 状況：効果なし、効果が `δ_imp` ちょうど、効果が `Δ_assumed`、クラスタあり、欠けたデータあり、時間による変化あり。各 2,000 回以上。
- 測るもの：偽陽性の率（α ＋ モンテカルロの誤差の3倍以下）、95% の区間の被覆率（0.93〜0.97）、検出力（計画した値の ±5 ポイント以内）、クラスタの依存で区間の幅が正しく広がること。
- 乱数の種を固定し、同じ種で同じ結果になる（D の16.1 の再現性）。
- 実装は、標準ライブラリだけでできる（`random`、`statistics`、`math`）。計算の時間が長くなるので、通常の試験とは分けた試験の印にする（依存の追加の判断は `PHASE_2_START_READINESS.md` の判断の事項）。

## 7. Benchmark Runner：複数のホームと実験全体の予算（改訂）

**目的：** Journal の暫定の上限（5万件、160 MiB、起動 60 秒）の内で評価を続けながら、ホームを切り替えても**予算と費用の保留がリセットされない**ようにする。Phase 2 の単一のエージェント、直列の実行、合成の XTU に限った最小の方式。Phase 3A の独立した予算の台帳は前倒ししない。

### 7.1 正本と強制の場所

- **各ホームの Journal と台帳が、そのホームの Run と費用の正本**（Phase 1 のまま）。予算の強制は、各ホームの台帳の予約（1つのトランザクションで全階層の上限を確かめる、D.V10）で行う。
- **評価の側の Results Store は、集計と記録だけ**で、予算の強制の手段にしない。
- ホームをまたぐ金額は、**台帳の署名付きの記録**（オーナーの鍵で署名した台帳の操作。Phase 1 の owner ledger op の方式）で受け渡す。

### 7.2 実験の予算の階層

| 階層 | 上限 | 強制 |
|---|---|---|
| 実験全体の XTU の上限（`experiment_cap_xtu`） | ExperimentSpec に固定 | 各ホームの台帳の「実験」の範囲の上限 ＝ 実験の上限 −（前のホームまでの確定の費用 ＋ 前のホームから引き継いだ保留） |
| 試行ごとの予算（`trial_cap_xtu`、金銭以外の上限） | ExperimentSpec に固定 | 試行の Task と Run の範囲の上限（Phase 1 の階層） |
| 試行ごとのイベントの上限（`trial_event_cap`）（新しい金銭以外の上限） | 予備の実行で計った最大に余裕を足し、ExperimentSpec に固定 | Runtime の金銭以外の上限（D の11.2）。超えたら Agent の側の `BUDGET_EXCEEDED` |

### 7.3 ホームの切り替え

1. **切り替えるのは試行と試行の間だけ。** 次の試行を始める前に、今のホームの残りの容量（イベント、バイト）が `trial_event_cap` と見込みのバイトに余裕を足した量より小さければ、切り替える。**200 イベント・1 MiB の見込みだけで、試行全体が上限の内に収まるとは保証しない**。試行がホームの上限に当たらないことは、`trial_event_cap`（試行の側の上限）と、切り替えの判断（ホームの側の残り）の組み合わせで保つ。
2. **閉じる前の確認：** 今のホームの予約を確かめる。保留の残る予約（結果不明）があれば、照合を先に試みる（証拠があれば確定）。確定できない保留は**引き継ぐ**（7.4）。保留を解放しない。
3. **締めの記録（Closing Statement）：** 今のホームの台帳に、署名付きの締めの操作を書く：`{experiment_id, home_id, 確定の費用の合計, 保留の一覧（reservation_id と額）, 無効な試行の費用, やり直しの費用, Journal の head（tenant_seq、head_hash）, 前のホームの締めのハッシュ}`。このハッシュを `closing_hash` とする。締めた後のホームでは、その実験の新しい試行を始めない（台帳が拒否する）。
4. **開始の記録（Opening Statement）：** 新しいホームの台帳に、署名付きの開始の操作を書く：`{experiment_id, 前のホームの closing_hash, 引き継いだ確定の費用の合計, 引き継いだ保留の一覧}`。新しいホームの実験の範囲の上限は、7.2 の式で決まる。**引き継いだ保留は、新しいホームの台帳に「引き継ぎの保留」として置き**、残りの予算から引いたままにする。
5. **ホームの連鎖：** 各ホームの開始の記録が前のホームの締めのハッシュを持つので、ホームの列はハッシュの連鎖になる。Experiment Registry は、ホームごとに**後継は1つだけ**であることを記録し、2つ目の後継を拒否する。

### 7.4 引き継いだ保留の照合（未解決のホームを放置しない）

- 前のホームの結果不明の Attempt は、そのホームを**読み取りだけにして放置しない**。照合のときは、前のホームを開いて（オーナーの照合の経路、Phase 1 の `reconcile`）確定する。前のホームの Journal と台帳が正本。
- 確定したら、今のホームに署名付きの調整の操作（`{前のホームの id, reservation_id, 確定の額}`）を書き、引き継ぎの保留を確定の費用に置き換える。**調整は `(home_id, reservation_id)` ごとに1回だけ**（2回目は拒否）。重複して数えない。
- 引き継いだ保留が1つでも残るあいだは、実験を**完了にしない**（分析と判定を確定しない）。

### 7.5 異常終了と再開

| 異常終了の地点 | 再開の手順 |
|---|---|
| 試行の途中 | Phase 1 の復旧の手順（OUTCOME_UNKNOWN、保留の維持）。その試行は評価の側で `INVALID_ENV` か Agent の側かを規則で分類する |
| 締めの記録の後、開始の記録の前 | 締めのあるホームに後継がない。Runner は同じ `closing_hash` で開始の記録を作る（締めの記録の内容から決まるので、同じ内容になる）。2回作らない（後継は1つだけ） |
| 開始の記録の後 | 後継はある。今のホームとして続ける |
| 調整の途中 | 調整は1つのトランザクション。済んだかは `(home_id, reservation_id)` で確かめる |

- 再開の後の集計は、ホームの連鎖をたどって、締めの記録と調整の記録から計算する（Results Store は確かめのための写し）。食い違えば台帳を正とし、Results Store の食い違いを報告する。

### 7.6 運用の上限と費用の上限の目的を失わないために

- 新しいホームは、運用の上限（P-2）と費用の上限を**そのまま引き継ぐ**：運用の上限は新しいホームの設定（`init` の暫定の値）、費用の上限は 7.2 の式。ホームを切り替えても、実験全体の XTU の上限は増えない。
- ホームの数にも上限を置く（ExperimentSpec。例：実験の上限 ÷ 試行の最小の費用から決まる数）。ホームを増やし続けて上限を避けることをしない。
- ホームの切り替えは、Snapshot（A.I6、Phase 4）の代わりではない。

### 7.7 実装の範囲（Phase 2）

- 台帳の新しい操作（締め、開始、引き継ぎの保留の調整）と、実験の範囲（`BudgetScopeType` の追加）は、**台帳の形式の変更**。WP2-08 で互換性の試験（以前の版の DB を開ける、新しい操作のない DB は実験の範囲を使えない）とともに作る。
- 試験：ホームの切り替えで上限が増えない、保留が残る、調整が1回だけ、締めと開始の間の異常終了、後継の重複の拒否、Results Store と台帳の食い違いの検出。

## 8. validation と holdout の独立の管理（改訂）

| 分割 | 置き場所 | 作る・管理する担当 | 開発の AI | 開発の側に返すもの |
|---|---|---|---|---|
| dev | 開発のリポジトリ（公開） | Claude Code が案を作り、オーナーが承認して固定 | 読める（盲検ではない） | 詳しく |
| validation | **開発のリポジトリの外の、独立した管理の場所**（例：オーナーの非公開のリポジトリか、評価の担当の環境） | オーナーまたは独立した担当 | **参照できない**（接続しない） | 必要な集計だけ（構成ごとの成功率と信頼区間、件数、無効の件数と理由の分類）。タスクごとの詳細とエラーの本文は返さない |
| holdout | D3 の独立した非公開のリポジトリ（`ken1700-mizui/agent-core-holdout`） | オーナー（評価の側） | 参照できない | C2 の11.1 の制限した集計だけ。Phase 2 では実行しない |

- **アクセスの分離：** 評価の担当は validation の場所に入れ、開発の担当（開発の AI のセッション）には入れない。開発の AI のセッションの対象のリポジトリに、validation と holdout を加えない。
- **版の固定：** 評価データの版（`eval_set_version`）と採点のプログラムの版（`grader_version`）を、評価の側で固定し、変更の履歴を残す。ExperimentSpec はその版のハッシュを持つ。
- **実行の方法：** validation の評価は、評価の担当が、公開のリポジトリの評価のプログラム（Benchmark Runner、採点、統計）を、評価の側の環境で動かして行う。Runner は、タスクの集合と採点の束を、開発のリポジトリの外の場所から読む接続口（`TaskSource`）を持つ。
- **盲検の条件：** validation の独立性（開発の担当が中身を見ていない、作っていない、詳細を受け取っていない）を確保できない試行は、**盲検の評価として扱わない**（探索的な結果として記録する。D.V15）。
- **今回は**、validation と holdout の非公開の場所の作成、接続、内容の生成、取得をしない。オーナーの手順は `PHASE_2_START_READINESS.md` の5章。
