# Phase 2 の実装計画（Work Package）

> 文書の状態：**DRAFT**（全体の構成と WP2-01〜15 の分割はオーナーが受け入れる方針（2026-11-28、D15）。この改訂の最終の受入と、製品のコードの実装の開始は承認されていない）　作成：2026-11-28　改訂：2026-11-28（設計の審査 CONDITIONAL_PASS への対応、Batch の計画）
>
> - ブランチ：`impl/phase-2-evaluation`（`cfb31bf` から作成済み。Phase 2 の変更はこのブランチだけ）
>
> - Phase 2：無料の評価の基盤、コーディングエージェント v1、Baseline、統計、契約テスト（ROADMAP の Phase 2、0.1、0.2）。費用は $0（D1）。Fake Provider の上で完成させる。
> - 関連：`PHASE_2_START_READINESS.md`（開始の条件と判断の事項）、`PHASE_2_EVALUATION_CONTRACT.md`（評価の契約）、`PHASE_2_SANDBOX_DESIGN.md`（Sandbox と P-4）、`PHASE_2_AGENT_INSTRUCTIONS.md`（実装する AI への指示、英語）。
> - WP の ID は Phase 1 の WP-01〜15 と区別して **WP2-01〜WP2-15** とする。

## 1. 順序と依存関係

```
WP2-01 型・契約・評価の記録の基盤
  ├─ WP2-02 P2 Broker と SandboxPort（偽の Sandbox）
  │    └─ WP2-03 Docker の Sandbox（自己診断、ファイルの4区分、回収）
  │         └─ WP2-04 P-5：A.I17 の監視と時間の上限
  │              └─ ◆SG-1：Sandbox の独立した安全性の確認 ＋ オーナーの承認（信頼しないコードの実行）
  │                   ├─ WP2-09 Sandbox のツールと Tool Selection
  │                   │    └─ WP2-10 Verification と修正のループ（Sandbox のテスト）
  │                   └─ WP2-12 Benchmark Runner と客観的な採点（WP2-07、08、11 にも依存）
  ├─ WP2-05 Context Engine v1 ─────────────┐
  ├─ WP2-06 Planning Engine v1 ─────────────┤（SG-1 の確認の待ちと並べて進める。直列の実装）
  ├─ WP2-07 Statistics Engine とシミュレーション ┤
  ├─ WP2-08 Cost Engine（XTU、評価の予算） ──┘
  └─ WP2-11 Task Catalog（dev。validation は外部の接続口だけ）
WP2-13 Baseline A・B と Fake のモデルの方針（WP2-05、06、09、10、12 に依存）→ ◆Baseline B の独立審査
WP2-14 FeatureComparator と契約テスト（FakeMemory を含む）
WP2-15 最終統合試験と Technical Gate・Safety Gate
```

**実装の順序（推奨。1つずつ直列）：** WP2-01 → 02 → 03 → 04 →（SG-1 の確認を依頼）→ 05 → 06 → 07 → 08 → 11 →（SG-1 の合格とオーナーの承認）→ 09 → 10 →（Baseline B の設計の審査）→ 12 → 13 →（Baseline B の実装の後の審査）→ 14 → 15。承認の単位は 2.5 の Batch

- **安全の順序：** 信頼しないコードを実行する WP（WP2-09 以後）は、WP2-02〜04 と SG-1（独立した確認とオーナーの承認）の**後**にだけ始める。WP2-03・04 の試験で Sandbox の中で動かすのは、**自分たちの固定の診断のプログラムと固定の試験の筋書きだけ**（信頼しないコードではない）。
- 候補の順序（指示の 1〜13）からの変更：Context、Planning、統計、Cost、Task Catalog を SG-1 の前に置いた（Sandbox に依存せず、SG-1 の確認を待つ間に進められるため）。Task Catalog を Benchmark Runner の前にした（Runner の試験に要るため）。

## 2. Work Package の一覧

### WP2-01 Phase 2 の型・契約・評価の記録の基盤

| 項目 | 内容 |
|---|---|
| 目的 | 評価の記録（D の3章）、Step の版の記録（A.I11）、新しい接続口を、型と試験で先に固める |
| 実装の範囲 | TaskSpec、ConfigSpec、ExperimentSpec、TrialRecord、ScoreRecord、AnalysisResult、Decision の型（`PHASE_2_EVALUATION_CONTRACT.md` の2章）。Experiment Registry（`spec_hash` で固定、固定の後の変更を拒否）と Results Store（追記だけ、ハッシュの連鎖、別の SQLite）。A.I11 のイベントの `schema_version` 4 と `provenance` の区画（承認された場合）。接続口の型：`SandboxPort`、`EvaluationStorePort`、`PlannerPort`、`GraderPort` |
| 実装しない範囲 | Sandbox、採点の実行、統計の計算、holdout の接続 |
| 依存 | Phase 1 の完了 |
| 既存コードへの影響 | `types/`（新しい型）、`ports/`（新しい接続口）、イベントの型（v4 を加え、v3 を読み続ける）、Rebuild（v3 と v4 の混在） |
| 新しいインターフェース | 上の型と接続口。`ExperimentRegistry.freeze(spec) -> spec_hash`、`ResultsStore.append(record)` |
| 試験の計画 | 型の検証、`spec_hash` の決定性、固定の後の変更の拒否（D の16.1：100%）、Results Store の連鎖の改ざんの拒否、v3 の Journal を読む互換性の試験、Phase 1 の同一性の試験（S1）の維持 |
| 受入の条件 | 既存の 2340 件と新しい試験が成功。v3 の Journal の状態が変わらない |
| 安全の条件 | 評価の記録を Agent の経路から書けない（D.V4、D.V8） |
| ロールバックの条件 | v4 で Phase 1 の同一性の試験が変わる場合は、v4 を入れない |
| 保守性 | 評価の型は `types/evaluation.py` に集め、Runtime の型と分ける |
| 必要な承認 | A.I11 の保存の形式（v4、評価の契約の3.2 の案 A） |
| 変更予定のファイル | `src/agent_core/types/evaluation.py`（新）、`types/events.py`、`ports/evaluation.py`（新）、`ports/sandbox.py`（新）、`adapters/sqlite_results.py`（新）、`runtime/state_machine.py`（v4 の受け入れ）、`tests/unit/test_wp2_01_*.py` |

### WP2-02 P2 Broker のプロセスと SandboxPort（偽の Sandbox）

| 項目 | 内容 |
|---|---|
| 目的 | C1 の2.1 のプロセスの分離（P1 と P2）を、Docker なしで先に作る |
| 実装の範囲 | `agent_core_broker` のパッケージ（P2、別の OS 利用者）、AF_UNIX の IPC（Sandbox の設計の7.6 の契約：両側の `SO_PEERCRED`、要求の種類の限定、`request_id`・`attempt_id` の対応、`receipt_id` ごとに最大1回の `start`、古い `owner_epoch` の拒否、要求の MAC、大きさの上限）、`SandboxPort` の P1 の側の client、試験用の偽の Sandbox（決定的）、import の契約の変更（AF_UNIX の client だけ） |
| 実装しない範囲 | Docker の操作、コードの実行 |
| 依存 | WP2-01 |
| 既存コードへの影響 | `pyproject.toml` の import の契約、CLI の起動（P2 を起動する方法。オーナーの承認） |
| 新しいインターフェース | `SandboxPort.prepare / start / status / stop(grace_ms) / collect / reap` |
| 試験の計画 | IPC の契約の各項目（P-5 の G）、Receipt の MAC のない `start` の拒否、AF_INET を開こうとすると失敗、P2 が止まったときに新しい実行が拒否される（P-5 の D） |
| 受入の条件 | 偽の Sandbox で、Phase 1 のゲートの経路（Permit、Receipt、報告）が P2 を通って動く |
| 安全の条件 | P1 は Docker の API を持たない。P2 は Journal に書かない |
| ロールバックの条件 | import の契約の変更が承認されない場合は、この WP を止める |
| 保守性 | IPC のメッセージの型は1か所。P2 は小さく保つ |
| 必要な承認 | import の契約の変更（S-5、承認待ち）。P1 と P2 の OS 利用者は別（S-6、承認済み D15）。P2 の起動の方法（7.4.1 の条件） |
| 変更予定のファイル | `src/agent_core_broker/`（新）、`src/agent_core/adapters/broker_client.py`（新）、`pyproject.toml`、`tests/unit/test_wp2_02_*.py` |

### WP2-03 Docker の Sandbox

| 項目 | 内容 |
|---|---|
| 目的 | C1 の8章の Sandbox を、自己診断とコンテナごとの照合とともに作る |
| 実装の範囲 | Engine API の client（AF_UNIX、標準ライブラリ）、`sandbox_profile` と `sandbox_profile_hash`、起動の自己診断（9項目）、コンテナごとの inspect の照合、資源の上限、ファイルの4区分（写しの作成と取り出しの検査、原子的な反映）、回収（Reaper）、試験の印 `sandbox`（Docker がなければ失敗、スキップしない） |
| 実装しない範囲 | 信頼しないコードの実行（固定の診断のプログラムだけ）、A.I17 の監視、ネットワークのある Sandbox |
| 依存 | WP2-02、オーナーの承認（Docker の採用と daemon の起動。S-3） |
| 既存コードへの影響 | Phase 1 の File Broker（`dir_fd` の解決）を写しと反映に再利用 |
| 新しいインターフェース | `SandboxProfile`、`SelfDiagnosisReport`、`ContainerInspection` |
| 試験の計画 | 自己診断の各項目を、わざと誤った設定（ソケットのマウント、root、capabilities、ネットワーク）で落とす（C1 の12.2：起動された件数 0）。取り出しの検査（リンク、特殊なファイル、範囲の外、setuid、反映の前の差し替え）。回収（残ったもの 100%） |
| 受入の条件 | 自己診断が1つでも落ちると、コードを実行するツールがすべて拒否される。ホストでの実行の経路がない |
| 安全の条件 | `--network none`、`--cap-drop=ALL`、`no-new-privileges`、読み込み専用のルート、非 root、ソケットなし、ダイジェストの固定 |
| ロールバックの条件 | 自己診断を満たせない環境では、Sandbox を「使えない」のままにする（条件を下げない） |
| 保守性 | Docker の差は P2 の中のアダプターに閉じる |
| 必要な承認 | S-3（Docker と daemon）、S-4（AppArmor のない環境の扱い）、S-7（イメージ） |
| 変更予定のファイル | `src/agent_core_broker/docker_api.py`、`sandbox.py`、`self_diagnosis.py`、`reaper.py`（新）、`tests/sandbox/`（新） |

### WP2-04 P-5：A.I17 の監視と時間の上限

| 項目 | 内容 |
|---|---|
| 目的 | 信頼しないコードを止める仕組み（D11、P-4 の確定案） |
| 実装の範囲 | **P2 の外からの期限の停止（主な強制）**、P1 の監視（期限の検査、停止の要求、停止の確認、生存の記録の1行。古い `owner_epoch` と別の監視の実行の ID を信じない）、補助の PID 1 の監督（保護の根拠にしない）、再起動の後の回収と OUTCOME_UNKNOWN（Sandbox の設計の7章） |
| 実装しない範囲 | Process Supervisor（Phase 3A）、監視による承認・解放・確定・再実行 |
| 依存 | WP2-03、オーナーの承認（P-4、値） |
| 既存コードへの影響 | `runtime/recovery.py`（期限の昇格を監視から呼ぶ。新しい遷移は作らない）、台帳（生存の記録の行。形式の変更と互換性の試験） |
| 新しいインターフェース | `Monitor.tick(now)`（状態を持たず、記録から計算） |
| 試験の計画 | P-5 の **18項目**（既存の13項目 ＋ D〜H。Sandbox の設計の7.7） |
| 受入の条件 | 18項目がすべて成功（条件に合う環境で）。障害ごとの保証の表（7.4）の各項目を「試験で確認」か「保証しない」に更新し、確かめていないものを保証済みと記録しない |
| 安全の条件 | 停止の確認までは保留を維持し、新しい Permit を出さない |
| ロールバックの条件 | どれかの試験に落ちたら、SG-1 に進まない |
| 保守性 | 監視は1つの小さなモジュール。時計と Sandbox は接続口で受け取る |
| 必要な承認 | P-4 の最終の受入（基本の方針は承認済み D15）。値は試験用の候補（D15） |
| 変更予定のファイル | `src/agent_core/runtime/monitor.py`（新）、`runtime/recovery.py`、`adapters/sqlite_ledger.py`（生存の記録）、`src/agent_core_broker/deadline.py`（新）、`tests/sandbox/test_p5_*.py` |

### ◆SG-1：Sandbox の独立した安全性の確認（WP2-04 の後、WP2-09 の前）

- C1 の12.2 のうち Sandbox に関わる試験（脱出を想定した隔離、子プロセスの残り、資源の上限、設定の不備、ファイルの境界、緊急停止）と、P-5 の18項目を、**AppArmor か SELinux のある条件に合う環境**で実行する（Sandbox の設計の S-4）。
- 開発者と独立した確認（D14：Phase 2 で信頼しないコードを実行する前に、Sandbox に関する独立した安全性の確認を別に行う）。
- オーナーが、信頼しないコードの実行の開始を承認する。

### WP2-05 Context Engine v1

| 項目 | 内容 |
|---|---|
| 目的 | B1 の v1（ST1・ST2 の置き換えと切り詰め、取り直し、引き継ぎ、構造化した要約の仕組み）（ROADMAP の0.1） |
| 実装の範囲 | 古いツールの結果の置き換え（ST1）、切り詰め（ST2）、ハンドルによる取り直し、Run の間の引き継ぎ、構造化した要約（Fake のモデル）、`context_hash` を Step の記録へ（A.I11） |
| 実装しない範囲 | 記憶（Phase 5）、実モデルでの要約の品質の主張 |
| 依存 | WP2-01 |
| 既存コードへの影響 | `context/engine.py`、`runtime/context_assembly.py`（Phase 1 の最小のものを広げる。Phase 1 の試験を保つ） |
| 新しいインターフェース | `ContextStrategy`（構成ごとに選べる。Baseline A・B と Agent Core で使い分け） |
| 試験の計画 | 上位の命令と安全の制約が切り詰められない（B1）、決定性、予算の推定、取り直しの正しさ |
| 受入の条件 | B1 の v1 の項目の試験の合格（0.1 の表） |
| 安全の条件 | B1 の権限の階層、ProtectedFact を保つ |
| ロールバックの条件 | Phase 1 の Context の試験が変わる場合 |
| 保守性 | 方式（Strategy）を差し替えられる形にし、FeatureComparator で比べられるようにする |
| 必要な承認 | なし（設計の範囲の中） |
| 変更予定のファイル | `src/agent_core/context/`、`runtime/context_assembly.py`、`tests/unit/test_wp2_05_*.py` |

### WP2-06 Planning Engine v1

| 項目 | 内容 |
|---|---|
| 目的 | 難易度の判定、`complex` の計画、上限付きの計画の立て直し（E の7.1） |
| 実装の範囲 | 規則による判定（規則で決まらないときだけ Model Call）、Plan の型（権威を持たない D4）、立て直しの条件と上限（暫定 10 回／Run）、計画の費用を予算に数える |
| 実装しない範囲 | 複数のエージェント（Phase 7） |
| 依存 | WP2-01 |
| 既存コードへの影響 | `runtime/agent_loop.py`（`simple` だけの今の動作を保ち、`complex` を加える） |
| 新しいインターフェース | `PlannerPort.classify / plan / replan` |
| 試験の計画 | 判定の規則、計画の上限、立て直しの上限、不要な計画を作らない（`simple`）、計画が権限を持たない |
| 受入の条件 | 0.1 の Planning v1 の試験の合格 |
| 安全の条件 | 計画は提案だけで、実行の権限は PDP と Permit だけ |
| ロールバックの条件 | `simple` の今の動作が変わる場合 |
| 保守性 | Planner は Strategy として差し替えられる |
| 必要な承認 | なし |
| 変更予定のファイル | `src/agent_core/planning/`（新）、`runtime/agent_loop.py`、`tests/unit/test_wp2_06_*.py` |

### WP2-07 Statistics Engine とシミュレーション

| 項目 | 内容 |
|---|---|
| 目的 | D の8章の推定と判定、16.1 のシミュレーション（Technical Gate） |
| 実装の範囲 | 対応のある比較、層の重み、クラスタの単位のブートストラップと並べ替えの検定、3つの判定、Holm、0 の扱い、シミュレーション（6状況、各 2,000 回以上） |
| 実装しない範囲 | 実データの分析（3D）、混合効果のモデル（必要なら別の承認） |
| 依存 | WP2-01 |
| 既存コードへの影響 | なし（新しいモジュール） |
| 新しいインターフェース | `analyze(trials, plan) -> AnalysisResult`、`simulate(scenario, n, seed)` |
| 試験の計画 | 既知の値との照合、決定性（同じ種で同じ結果）、偽陽性の率・被覆率・検出力（D の16.1 の範囲） |
| 受入の条件 | D の16.1 の4項目の合格 |
| 安全の条件 | 分析の計画は固定の後に変えない（D.V1） |
| ロールバックの条件 | シミュレーションに合格しなければ、統計の部分を直す（ROADMAP） |
| 保守性 | 純粋な関数。標準ライブラリだけ（判断の事項 O-6） |
| 必要な承認 | 依存の方針（O-6） |
| 変更予定のファイル | `src/agent_core/evaluation/statistics.py`（新）、`tests/unit/test_wp2_07_*.py`、`tests/simulation/`（新、時間の長い試験） |

### WP2-08 Cost Engine（XTU と評価の予算）

| 項目 | 内容 |
|---|---|
| 目的 | 評価で使う XTU の予算と、予算の階層の試験（ROADMAP の Technical Gate） |
| 実装の範囲 | 実験と試行の予算の範囲、試行ごとのイベントの上限（`trial_event_cap`）、費用の4区分（確定、結果不明の保留、無効な試行、やり直し）、**ホームをまたぐ予算の記録**（締め、開始、引き継ぎの保留、調整。評価の契約の7章）、BillingScheme のフィクスチャ、金銭以外の上限と合計、打ち切りの処理（D の12.2）、0 の扱い（12.3） |
| 実装しない範囲 | 実際の価格表（3A）、独立した台帳（3A）、`_totals` の高速化（上限を上げる前。D13） |
| 依存 | WP2-01 |
| 既存コードへの影響 | `cost/rules.py`、`adapters/sqlite_ledger.py`（新しい範囲の種類。形式の変更なら互換性の試験） |
| 新しいインターフェース | 実験と試行の範囲（`BudgetScopeType` の追加の候補） |
| 試験の計画 | D の16.1 の費用の試験（予約の階層、状態遷移、BillingScheme、整数の計算、金銭以外の上限、照合、打ち切り、0）。ホームの切り替えで上限が増えない、保留が残る、調整が1回だけ、締めと開始の間の異常終了、後継の重複の拒否、以前の版の DB を開ける |
| 受入の条件 | 各試験の合格の目安（D の16.1） |
| 安全の条件 | 試験用の価格表を本番のモードで使わない（D.V14） |
| ロールバックの条件 | Phase 1 の費用の試験が変わる場合 |
| 保守性 | 費用の規則は `cost/` に、保存は台帳に |
| 必要な承認 | 台帳の形式の変更（新しい操作と範囲。承認待ち） |
| 変更予定のファイル | `src/agent_core/cost/`、`adapters/sqlite_ledger.py`、`types/cost.py`、`tests/unit/test_wp2_08_*.py` |

### WP2-09 Sandbox のツールと Tool Selection（**SG-1 の後**）

| 項目 | 内容 |
|---|---|
| 目的 | Sandbox の中のシェルの実行と、ツールの種類の追加（0.1） |
| 実装の範囲 | `sandboxed_command`（マニフェスト、実行物の固定、`effect_class`、時間の上限）、検索（`rg` に当たるもの）、範囲を指定した読み込み、編集のツール、規則による選択（RunGrant の中だけ） |
| 実装しない範囲 | ネットワークのあるツール、MCP |
| 依存 | WP2-04、SG-1、オーナーの承認（信頼しないコードの実行） |
| 既存コードへの影響 | `tool/` の登録簿、`runtime/execution_gate.py`（Sandbox の経路） |
| 新しいインターフェース | `ToolSelector.select(task, grant)`（提案だけ） |
| 試験の計画 | 権限のない操作の拒否、Permit の経路、出力の上限、Sandbox の時間の上限 |
| 受入の条件 | 選択の結果が権限を持たない（E の7.2）。すべての実行が Permit と Sandbox を通る |
| 安全の条件 | ホストでの実行の経路がない |
| ロールバックの条件 | Sandbox の自己診断の失敗のときに、ツールが拒否されない場合 |
| 保守性 | ツールはマニフェストで宣言する |
| 必要な承認 | 信頼しないコードの実行の開始（SG-1 の後） |
| 変更予定のファイル | `src/agent_core/tool/`、`runtime/execution_gate.py`、`tests/sandbox/test_wp2_09_*.py` |

### WP2-10 Verification と修正のループ

| 項目 | 内容 |
|---|---|
| 目的 | Sandbox の中のテストを使う検証と、上限付きの修正（E の7.3） |
| 実装の範囲 | `VERIFYING` で Sandbox のテストを実行、失敗のフィードバック、修正の上限（暫定 3 回）、上限で `FAILED` |
| 実装しない範囲 | 評価の採点（WP2-12。Verification の結果を採点に使わない） |
| 依存 | WP2-09 |
| 既存コードへの影響 | `runtime/verification.py`、`runtime/agent_loop.py` |
| 新しいインターフェース | `VerificationPlan`（完了条件とテスト） |
| 試験の計画 | 自己申告では完了にならない、修正の上限、テストの結果の検証 |
| 受入の条件 | 0.1 の Verification の試験の合格 |
| 安全の条件 | 検証も Sandbox の中だけ |
| ロールバックの条件 | Phase 1 の完了の条件の試験が変わる場合 |
| 保守性 | 検証の方式は Strategy |
| 必要な承認 | なし |
| 変更予定のファイル | `src/agent_core/runtime/verification.py`、`runtime/agent_loop.py`、`tests/sandbox/test_wp2_10_*.py` |

### WP2-11 Task Catalog（dev と、validation の接続口）

| 項目 | 内容 |
|---|---|
| 目的 | 評価に使う合成のタスクと小さなリポジトリ（D の5章、6章） |
| 実装の範囲 | **dev のタスクだけ**（Claude Code が案を作り、オーナーが承認して固定。最初は 20〜50 件。統計の検出力の保証には使わない）、始めの作業ツリー、dev の隠しテスト、`provenance`、`contamination_notes`、版、`grader_version`。validation のための**接続口だけ**（`TaskSource`：開発のリポジトリの外の場所から、評価の担当がタスクと採点の束を読ませる）と、公開してよい型と合成の試験のフィクスチャ |
| 実装しない範囲 | validation の本体と隠しテスト（**開発のリポジトリに置かない**。オーナーか独立した担当が別の場所で作り管理する）、holdout（D3）、実データ |
| 依存 | WP2-01 |
| 既存コードへの影響 | Phase 1 の `SyntheticTaskDefinition` と Policy の `trusted_synthetic_tasks`（P-6）を、Task Catalog の参照に広げる |
| 新しいインターフェース | `TaskCatalog.load(split, version)` |
| 試験の計画 | 分割の間の重複の検査（D の6.3）、版の固定、AI からの書き換えの拒否（D の16.2） |
| 受入の条件 | タスクの集合の版が固定され、採点のプログラムと結び付く |
| 安全の条件 | タスクの集合と採点のプログラムは AI が変えられない（D.V8）。変更はオーナーの承認と新しい版 |
| ロールバックの条件 | 分割の重複が見つかった場合 |
| 保守性 | タスクはデータとして置き、コードと分ける |
| 必要な承認 | タスクの作り方（O-7、承認済み D15）、dev の集合の固定（オーナー） |
| 変更予定のファイル | `eval/tasks/dev/`（新）、`src/agent_core/evaluation/catalog.py`、`task_source.py`（新）、`tests/fixtures/eval/`（合成の試験用。validation の内容ではない） |

### WP2-12 Benchmark Runner と客観的な採点

| 項目 | 内容 |
|---|---|
| 目的 | 登録 → 実行 → 採点 → 記録の流れ（D の2章、7章、13章） |
| 実装の範囲 | 無作為の順番とブロック、乱数の種の記録、試行ごとの新しい Run、別の採点用の Sandbox での隠しテスト、TrialRecord と ScoreRecord（2.4 の分類の規則、費用の4区分）、無効な試行のやり直し（2 回まで）、ホームの連鎖（評価の契約の7章。WP2-08 の台帳の操作を使う）、外部のタスクの元（`TaskSource`）からの実行と集計だけの返却（validation） |
| 実装しない範囲 | holdout、評価の側の別のホスト（3A）、AI による採点 |
| 依存 | WP2-10、WP2-11、WP2-07、WP2-08（ホームをまたぐ予算） |
| 既存コードへの影響 | CLI に評価の命令を加える（承認） |
| 新しいインターフェース | `BenchmarkRunner.run(experiment_spec)` |
| 試験の計画 | 再現性（同じ仕様と種で同じ結果、D の16.1）、分類の規則（Agent の障害を `INVALID_ENV` にしない、違反のある試行が安全の集計から消えない、結果不明の費用を 0 にしない、D の16.2）、ホームの連鎖でイベントも保留も消えない、実験の上限が増えない |
| 受入の条件 | D の16.1・16.2 の Runner に関わる試験の合格 |
| 安全の条件 | Agent は採点と分類に触れない |
| ロールバックの条件 | 分類の誤りが見つかった場合 |
| 保守性 | Runner、Grader、Controller を分ける |
| 必要な承認 | CLI の命令の追加 |
| 変更予定のファイル | `src/agent_core/evaluation/runner.py`、`grader.py`、`controller.py`（新）、`src/agent_core_cli/`、`tests/sandbox/test_wp2_12_*.py` |

### WP2-13 Baseline A・B と Fake のモデルの方針

| 項目 | 内容 |
|---|---|
| 目的 | 公平な比較の相手（D の4章） |
| 実装の範囲 | Baseline A と B の構成（同じ Runtime と安全の仕組みの上）、決定的な Fake のモデルの方針（評価の契約の4.3）、ConfigSpec の登録、安全の必須の機能を外す構成の拒否（D の16.2） |
| 実装しない範囲 | Baseline C（実際の外部のエージェント） |
| 依存 | WP2-05、06、09、10、12 |
| 既存コードへの影響 | 構成の選択（Strategy の組み合わせ） |
| 新しいインターフェース | `ConfigRegistry.register(config_spec)` |
| 試験の計画 | 安全の必須の機能が全構成で有効、そろえる条件の照合、`condition_deviations` の記録 |
| 受入の条件 | **Baseline B の構成の独立審査**（ROADMAP の Phase 2 のオーナーの承認） |
| 安全の条件 | D.V3 |
| ロールバックの条件 | 独立審査で妥当でないとされた場合は直す |
| 保守性 | 構成はデータ（ConfigSpec）で表す |
| 必要な承認 | Baseline B の構成の承認 |
| 変更予定のファイル | `src/agent_core/evaluation/configs.py`（新）、`adapters/fake_model.py`（方針の追加）、`tests/unit/test_wp2_13_*.py` |

### WP2-14 FeatureComparator と契約テスト

| 項目 | 内容 |
|---|---|
| 目的 | 機能ごとの比較の枠組み（E の10.3）と、後の Engine との契約（FakeMemory を含む） |
| 実装の範囲 | FeatureComparator（1つの機能だけを変えた構成）、B2 の8章のイベントと要求のスキーマ、`MemoryQuery` の接続口、FakeMemory、Model Gateway の共通の型の契約テスト |
| 実装しない範囲 | Memory Engine の本体（Phase 5） |
| 依存 | WP2-13 |
| 既存コードへの影響 | `ports/`（Memory の接続口） |
| 新しいインターフェース | `MemoryPort`（偽の実装だけ）、`FeatureComparator.build(base, feature)` |
| 試験の計画 | 契約テスト（同じ契約を偽の実装が満たす）、FeatureComparator の構成が1つの機能だけ違う |
| 受入の条件 | 契約テストの合格 |
| 安全の条件 | 記憶は D4（権威を持たない） |
| ロールバックの条件 | 契約が既存の型と矛盾する場合 |
| 保守性 | 接続口だけを固め、実装は後の Phase |
| 必要な承認 | なし |
| 変更予定のファイル | `src/agent_core/ports/memory.py`（新）、`adapters/fake_memory.py`（新）、`src/agent_core/evaluation/comparator.py`（新）、`tests/contract/`（新） |

### WP2-15 最終統合試験と Gate

| 項目 | 内容 |
|---|---|
| 目的 | Phase 2 の Technical Gate と Safety Gate の確認 |
| 実装の範囲 | 評価の流れの端から端までの試験（Fake）、統計のシミュレーションの合格、予算の階層の試験、コーディングエージェント v1 の完成の条件（0.1）、C1 の12.2 の Sandbox の試験、CI（オーナーの承認） |
| 実装しない範囲 | 実 API、holdout |
| 依存 | WP2-01〜14 |
| 既存コードへの影響 | なし（試験と文書） |
| 新しいインターフェース | なし |
| 試験の計画 | 全試験の連続2回、一般の利用者、特権、Sandbox の試験（条件に合う環境）、ネットワークのない環境 |
| 受入の条件 | 3章の完了の条件 |
| 安全の条件 | 違反 0 件 |
| ロールバックの条件 | Gate に落ちた場合は、該当の WP に戻る |
| 保守性 | — |
| 必要な承認 | CI の実行、Phase 2 の完了 |
| 変更予定のファイル | `tests/integration/`、文書 |

## 2.5 Batch による承認の単位（改訂）

依存関係を確かめた結果、提案の4つの Batch の中の順序を次のとおりにする。Batch の中の WP は直列に実装し、WP ごとに試験を先に書き、WP ごとにコミットする。

| Batch | WP（順序） | 開始の条件 | 停止の条件（どれかに当たれば止めて報告） | 承認の事項 | 品質の検査 |
|---|---|---|---|---|---|
| **Batch 1**：型、契約、Broker、Sandbox、安全の条件 | WP2-01 → 02 → 03 → 04 | オーナーの Batch 1 の承認。import の契約の変更（S-5）の承認。環境の操作 E-1〜E-4（Sandbox の設計の11章）の承認（WP2-03 の前） | 既存の 2340 件のどれかが落ちる。v3 の Journal の同一性が変わる。自己診断の条件を満たせない（条件を下げない）。P-5 の18項目のどれかが条件に合う環境で落ちる | Batch 1 の開始、S-5、E-1〜E-4、P2 の起動の方法 | 8種類の検査（`sandbox` の印を除く既存の手順）＋ `sandbox` の試験を Docker の動く環境で実行（実行していないものを成功に数えない） |
| **SG-1**（Batch 1 の後） | — | Batch 1 の完了 | 独立した確認で不合格 | 担当と環境（E-5、E-6）、確認の結果の受入、**信頼しないコードの実行の開始** | C1 の12.2 の Sandbox の試験と P-5 の18項目を、AppArmor か SELinux のある環境で |
| **Batch 2**：Context、Planning、統計、費用、Task Catalog（**SG-1 の確認と並べて進める**） | WP2-05 → 06 → 07 → 08 → 11 | Batch 1 の完了（WP2-01 の型を使う）。Sandbox の中の実行を要しない | 既存の試験が落ちる。統計のシミュレーションが D の16.1 の範囲を満たさない（統計を直す）。台帳の形式の変更で以前の版の DB を開けない | Batch 2 の開始、台帳の形式の変更（WP2-08）、dev のタスクの集合の固定 | 8種類の検査。シミュレーションの試験（時間の長い印）を別に実行 |
| **Batch 3**：Sandbox のツール、検証、Runner、Baseline（**SG-1 の合格とオーナーの承認の後**） | WP2-09 → 10 → 12 → 13 | SG-1 の合格、信頼しないコードの実行の承認、Batch 2 の完了、承認済みの安全の条件を満たす環境（S-4） | Sandbox の自己診断の失敗の後にツールが拒否されない。分類の規則の誤り。Baseline B を意図的に弱くする変更 | Batch 3 の開始、CLI の評価の命令、Baseline B の構成の案 | 8種類の検査 ＋ `sandbox` の試験（条件に合う環境） |
| **Batch 4**：Baseline B の独立審査、FeatureComparator、最終統合 | Baseline B の独立審査（実装の後の2回目）→ WP2-14 → 15 | Batch 3 の完了。Baseline B の設計の段階の審査（1回目）は Batch 3 の WP2-13 の前に行う | 独立審査で妥当でない。Gate に落ちる | 審査の結果の受入、CI の実行、Phase 2 の完了 | 全試験の連続2回、一般の利用者、特権、`sandbox`、ネットワークのない環境、CI |

- **SG-1 の確認中に進められる WP：** Batch 2 のすべて（Sandbox の中の実行に依らない）。Batch 3 は SG-1 の合格まで始めない。
- **Baseline B の独立審査（O-11）：** 1回目は WP2-13 の設計の段階（Batch 3 の WP2-13 の前、構成の案）、2回目は実装の後（Batch 4 の最初）。Baseline B を意図的に弱くしない。
- **既定の品質の検査と `sandbox` の試験：** Docker を要する試験は `sandbox` の印にし、Docker のない環境では `SANDBOX_NOT_RUN` で失敗させる（スキップしない）。既定の8種類の検査は `-m "not sandbox"` で実行し、`sandbox` の試験は Docker の動く環境で別に実行して、両方の結果を報告する。実行していない試験を成功に数えない。

## 3. Phase 2 の完了の条件（ROADMAP の Phase 2）

1. **Technical Gate：**
   - 統計のシミュレーション（偽陽性の率、被覆率、検出力）の合格（D の16.1）。
   - 予算の階層の試験の合格。
   - **コーディングエージェント v1 の完成の条件**：0.1 の「Phase 2」の欄の機能が、すべて試験に合格（Context v1、Planning v1、Tool Selection、Sandbox の中のシェル、Verification と修正、Baseline B、評価の対象の構成の組み立て、Cost Engine）。
2. **Safety Gate：** C1 の12.2 のうち Sandbox に関わる試験の合格、P-5 の18項目の合格、SG-1 の独立した確認。
3. **オーナーの承認：** Baseline B の構成の確認（独立審査を含む）。
4. **品質：** 既存の 2340 件を含む全試験の成功、8種類の検査、CI。
5. **主張しないこと：** Fake の上の結果を、実モデルでの性能の向上としない。Docker の標準の機能で完全な隔離が得られたとしない。

## 4. 予定の作業の量と難易度（見積もり。Phase 1 の WP の実績との比較）

| WP | 難易度 | 量（Phase 1 の平均的な WP を 1 とする） | 主な危険 |
|---|---|---|---|
| WP2-01 | 中 | 1.0 | イベントの v4 と互換性 |
| WP2-02 | 中 | 1.0 | IPC と import の契約 |
| WP2-03 | **高** | 2.0 | 環境（daemon、AppArmor、cgroup）の未確認 |
| WP2-04 | **高** | 1.5 | 時間の上限の多層化、PID 1 の性質の検証 |
| WP2-05 | 中 | 1.0 | 既存の Context の試験の維持 |
| WP2-06 | 中 | 1.0 | 計画の上限と予算 |
| WP2-07 | 中〜高 | 1.5 | 標準ライブラリでの計算の速さ |
| WP2-08 | 中 | 1.0 | 台帳の形式 |
| WP2-09 | 中 | 1.0 | Sandbox の経路の一貫性 |
| WP2-10 | 中 | 0.8 | — |
| WP2-11 | 中 | 1.0 | タスクの作り手と validation の盲検性 |
| WP2-12 | **高** | 1.5 | 分類の規則、ホームの区切り |
| WP2-13 | 中 | 1.0 | 公平性（独立審査） |
| WP2-14 | 低〜中 | 0.7 | — |
| WP2-15 | 中 | 1.0 | 条件に合う環境での Sandbox の試験 |
| **合計** | — | **約 17**（Phase 1 の 15 の WP の約 1.1〜1.3 倍。修正の回を含まない） | — |
