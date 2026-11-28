# Phase 2 評価の契約（D.V19 の記録の形式、A.I11 の Step の記録、公平な比較、統計）

> 文書の状態：**DRAFT**（設計案。オーナーの承認の前）　作成：2026-11-28
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
  limits_hit: [ TIME | BUDGET | STEPS | CALLS | CONTEXT ]          # どの上限で止まったか
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

### 3.2 保存の方法の案（**保存の形式の変更。オーナーの承認が要る**）

| 案 | 内容 | 互換性 | 推奨 |
|---|---|---|---|
| **A** | 新しいイベントの `schema_version = 4` を作り、Step の作成の遷移に任意の `provenance` の区画（3.1 の「Phase 2 で加えるもの」の識別子とハッシュだけ）を持たせる | `schema_version` 3 のイベントはそのまま読める。3 と 4 の混在を Rebuild で受け入れる。同一性の試験（Phase 1 の S1）と、以前の版の Journal を読む試験を加える | **推奨** |
| B | Journal は変えず、台帳に Step ごとの表を加える | Journal と台帳の二つの正本になる。A.I11（Step のイベントに版の項目）を満たさない | 推奨しない |
| C | 評価の記録（TrialRecord）だけに持つ | Step の単位の原因の分析ができない | 推奨しない |

- **記録しないもの：** プロンプトの本文、ファイルの内容、モデルの出力の本文、認証情報、環境変数の値。識別子とハッシュだけを記録する（C2、A の2章）。
- 機密を含みうる値のハッシュは、Phase 1 の `keyed_hashes` の方式（鍵付きのハッシュ）を使う。Phase 2 は合成のデータだけなので、鍵の運用は Phase 3A の SAG と合わせて見直す。

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

## 7. Benchmark Runner と Journal の暫定の上限（5万件、160 MiB、起動 60 秒）

**Journal を消さずに評価を管理する方式（推奨）：**

1. **評価のホームを区切る：** Runner は、実験（または実験の区切り）ごとに、新しい合成のホーム（自分の Journal）を作る。1つのホームのイベントの数が警告（3万件）に近づく前に、次のホームに移る（試行の途中では移らない。試行の始まりで判断する）。
2. **見込みで判断する：** 試行を始める前に、P-2 の判断（見込み 200 件と 1 MiB、開始の全体）で、その試行がホームの上限に入るかを確かめる。入らなければ、新しいホームで始める。上限で止まった試行を作らない。
3. **前のホームは残す：** 使い終わったホームは読み取りだけにして残す（削除も切り詰めもしない）。Results Store の TrialRecord は、ホームの ID と Journal の head を参照する。
4. **集計は Results Store から：** 統計は Results Store の記録から計算し、古いホームを開き直さない（起動の時間を増やさない）。
5. **試行ごとの見込みの計測：** 予備の実行で、試行あたりのイベントの数を計り、ExperimentSpec に記録する（例：1試行 300 件なら、1つのホームで約 100 試行）。

- これは Snapshot（A.I6、Phase 4）の代わりではない。費用の台帳の集計の性能（`_totals`）は、ホームを区切ることで上限の内に保つ。運用の上限を上げる場合は、その前に再計測する（D13）。

## 8. holdout（D3）

- Phase 2 では holdout を実行しない（ROADMAP の Phase 2 の「実装しない範囲」）。
- 開発のリポジトリには dev と validation だけを置く。holdout の接続口（評価セットの版、使用の回数の台帳、集計の制限）は型だけを作り、実装と接続は Phase 3A／3D（評価の側の環境）にする。
- オーナーの手順は `PHASE_2_START_READINESS.md` の5章。
