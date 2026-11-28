# Phase 2 の開始の準備（開始の条件、6つの事項の推奨、オーナーの判断の事項）

> 文書の状態：**DRAFT**（設計案。オーナーの承認の前。**Phase 2 の開始は承認されていない**）　作成：2026-11-28
>
> - 開始の時点：`impl/phase-1-foundation` の `92c18c4756f1b927a27f72deced90b96d0595d44`（Phase 1 の正式な完了の記録。D12）。Phase 1 の最終の CI で検証した SHA は `93435c82e06a168794cf68c6ecf1fc598815d426`。`main` は `d0b8635`。
> - オーナーの決定 D1〜D14 を維持する。この文書は承認の記録を変えない（`OWNER_DECISIONS.md` は変えていない）。
> - 関連：`PHASE_2_IMPLEMENTATION_PLAN.md`、`PHASE_2_EVALUATION_CONTRACT.md`、`PHASE_2_SANDBOX_DESIGN.md`、`PHASE_2_AGENT_INSTRUCTIONS.md`。

## 1. Phase 2 の全体の設計（要約）

- **目的：** 同じモデル（Phase 2 は同じ Fake のモデル）を使ったときに、Agent Core の仕組みで、成功率、品質、費用、時間、安全がどう変わるかを、**公平に測れる仕組み**を費用 $0 で作る。
- **作るもの：** コーディングエージェント v1（Context v1、Planning v1、Tool Selection の拡張、Docker の Sandbox とその中のシェル、Verification と修正のループ）、Task Catalog（dev、validation）、Benchmark Runner と客観的な採点、Statistics Engine とシミュレーション、XTU の Cost Engine、Baseline A・B、FeatureComparator、契約テスト（FakeMemory）。
- **構成の考え方：** 3つの構成（Baseline A、B、Agent Core）は、同じ Runtime、安全の仕組み、Sandbox、Task Catalog、採点のプログラムの上で動き、違うのは文脈・計画・検証・ツールの設計の Strategy だけ。評価の記録（Results Store、Experiment Registry）は Agent の Journal と分ける。
- **主張しないこと：** Fake の上の成功を、実モデルでの性能の向上の証明にしない。Docker の標準の機能で完全な隔離が保証されるとしない。
- **WP と順序：** `PHASE_2_IMPLEMENTATION_PLAN.md`（WP2-01〜15、信頼しないコードを実行する前の SG-1）。

## 2. 開始の前の6つの事項への推奨

| # | 事項 | 推奨 | 詳細 |
|---|---|---|---|
| 1 | **P-4（A.I17）の設計** | 採用案 A（P1 の監視）＋ D（P2 とコンテナの側の強制）、C（Process Supervisor）は 3A。P-4 の中の6つの矛盾を直す（Docker の API は P2 だけなので P1 から P2 への IPC が要る、時間の上限の強制は P2 が主（C1 の8.6）、監督の処理は同じ利用者の PID 1、生存の記録は1行の上書き、今の値との違い、daemon の停止の扱い）。値は承認の前の候補。最新の10項目と9章を維持し、3項目を加える | Sandbox の設計の7章 |
| 2 | **D.V19 の記録の形式** | D の7.1 の `task_outcome` の6つの値と `safety{violations, blocked_attempts}` を変えずに使い、拡張として `unresolved`、`grading_status`、`limits_hit` を加える（承認が要る）。9つの区分の扱いと集計の規則を決めた | 評価の契約の2章 |
| 3 | **A.I11 の Step の記録の項目** | 記録済み・Phase 2 で加える・後の Phase の3つに分けた。保存は案 A（イベントの `schema_version` 4 に任意の `provenance` の区画、識別子とハッシュだけ、v3 を読み続ける） | 評価の契約の3章 |
| 4 | **Docker の Sandbox の自己診断の方針**（C1 の8.2） | 起動のたびに9項目を確かめ、1つでも落ちれば実行を拒否。コンテナごとの inspect の照合。ホストでの実行へのフォールバックなし。Docker を要する試験は `sandbox` の印で、ない環境では失敗（スキップしない）。AppArmor のない環境では信頼しないコードを実行しない（推奨） | Sandbox の設計の4章、6章、10章 |
| 5 | **holdout の独立の管理**（D3） | 開発のリポジトリには dev と validation だけ。holdout は別の非公開のリポジトリで、オーナー（または評価の担当）だけが管理し、開発の AI には接続しない。Phase 2 では holdout を実行しない（接続口の型だけ） | 5章 |
| 6 | **FINAL-R05 と並行実行** | Phase 2 は**単一のエージェント、直列の実行**（評価を正しく測ることを優先）。今の単一の Runtime の契約（OS のロック、1つのセッション）を保つ。並行実行は別の承認とし、その前に FINAL-R05 の5つの契約を満たす | 6章 |

## 3. 環境（Docker の必要な環境と、未確認の事項）

| 必要な条件 | この環境（2026-11-28、読み取りだけ） | 状態 |
|---|---|---|
| Linux のホスト | Ubuntu 24.04.5、Linux 6.18.44（VM） | 満たす |
| Docker Engine | 29.9.3 の `dockerd` と `docker`、containerd v2.4.2、runc 1.5.3 が導入済み | **daemon は動いていない**（起動はオーナーの承認が要る） |
| 非 root の実行、userns-remap か rootless | `max_user_namespaces` 64271 | **未確認** |
| seccomp | カーネルは対応 | 効くかは**未確認** |
| AppArmor／SELinux | **なし** | **満たさない**（Sandbox の設計の S-4） |
| cgroup の上限 | v1（memory、pids あり） | 効くかは**未確認** |
| イメージの取得 | レジストリへの通信 | **未確認** |
| P1 と P2 の別の OS 利用者 | 今は root だけ | 利用者を作る必要がある |

- 代替の検証の環境：GitHub Actions の標準の runner（Docker、AppArmor、cgroup v2。実行ごとに承認）、オーナーの専用の Linux の VM。**安全の条件を下げた代替の実行はしない。**

## 4. 必要な依存関係の候補（**今回は導入しない**）

| 候補 | 必要性 | 推奨 |
|---|---|---|
| Docker Engine（既存の 29.9.3） | Sandbox に必須 | 使う（ADR-0004 の承認と daemon の起動の承認） |
| Sandbox のイメージ（ダイジェストで固定） | テストの実行 | 用意の方法は通信の確認の後 |
| Python の `docker` SDK | 不要 | 使わない（Engine API を標準ライブラリで呼ぶ） |
| `numpy`、`scipy` | 統計の計算を速くできる | **使わない**（標準ライブラリで実装する。遅い場合は別に承認を求める。O-6） |

## 5. holdout の独立の管理：オーナーの手順（**今回は作成・接続・取得をしない**）

1. オーナーが、別の非公開のリポジトリ `ken1700-mizui/agent-core-holdout`（D3）を作る。開発のリポジトリとは別のアクセスの権限にし、開発の AI のセッションに接続しない（Claude Code のセッションの対象のリポジトリに加えない）。
2. holdout のタスクは、オーナーか独立した担当が作る（開発の AI は中身を見ない）。非公開か、新しく作ったか、モデルの知識の時点より後のタスクを優先する（D の5.3）。canary の文字列を入れる（D の6.3）。
3. 評価セットの版（`eval_set_version`）を、変更の履歴（コミット）とともに管理する。採点のプログラムの版も同じリポジトリで固定する。
4. 使用の回数の台帳（版ごとに累積。主な判定に1回、確認の再評価に1回まで。D の6.2、V7）を、holdout の側に置く。
5. 確認の再評価の条件は、最初の holdout の実行の**前に**事前登録する。結果を見てから評価の基準を変えない（V1、V7）。
6. holdout の評価は、評価の側の環境（Phase 3A で準備、3D で使う）で行い、開発の側には制限した集計だけを返す（C2 の11.1）。
7. dev と validation との重複は、評価の側で照合し、結果だけを開発の側に知らせる（D の6.3）。

## 6. FINAL-R05 と並行実行

- **Phase 2 の推奨：** 単一のエージェント、直列の実行。Benchmark Runner も試行を1つずつ実行する。理由：評価の正しさ（時間の変化、資源の競合、予算の階層）を先に確かめる。Phase 1 の単一の Runtime の契約（OS のロック、1つのセッション、`session.continues`）をそのまま使える。
- **並行実行を入れる場合に必要なもの**（別の承認）：
  1. FINAL-R05 の5つの契約（再入の防止を Run の単位の Lease に、生きている Run の判定を Journal の Lease から、照合のトランザクションの再検査、復旧と新しい Step の競合の防止、期限の検査を停止とタイムアウトの経路から呼べること）。
  2. 予算の階層の同時の予約（D の16.1 の「同時の実行や障害があっても、上限を超えた件数 0」）。
  3. Sandbox の同時実行の上限（C1 の8.6、Orchestrator が強制）。
  4. 評価への影響：試行の間の資源の競合、時間のブロックの設計（D の8.3）、無効な試行の偏り（D の7.5）。

## 7. Phase 1 から引き継ぐ条件（維持する）

- 暫定の運用の上限：5万イベント、160 MiB、起動 60 秒（D13）。Benchmark Runner はホームを区切って上限の内に保つ（評価の契約の7章）。
- 制限のない旧ホームでの新しい作業の禁止。Journal の削除と切り詰めの禁止。
- 運用の上限を上げる前に、費用の台帳の集計（`_totals`）の性能を再評価する。
- A.I6（Snapshot）と A.I12 は Phase 4 まで条件付きの延期。P-3 は未実装（Phase 4 までに対応）。M6 は未実装。
- Phase 3A の追加の安全の条件（OS のレベルの隔離、Gateway、Process Supervisor、独立の予算の台帳など）と、Technical Gate・Safety Gate を省略しない。
- Phase 3B の前の包括的なセキュリティ監査（D14）。
- 既存の 2340 件の試験を保つ（削除、スキップ、無効化をしない）。

## 8. 開発の管理

- **ブランチ：** `impl/phase-2-evaluation` を、`92c18c4756f1b927a27f72deced90b96d0595d44` から作る（**今回は作らない**。作成はオーナーの承認の後）。`impl/phase-1-foundation` は Phase 1 の正式な完了の時点として保ち、Phase 2 の変更を加えない。
- **目印：** Phase 1 の完了の時点に、注釈付きのタグ（例：`phase-1-complete` → `92c18c4`）を付けることを提案する（作成はオーナーの承認が要る）。
- **PR：** 作る場合は Draft とし、マージしない（AGENTS.md）。対象は `impl/phase-1-foundation`。
- **CI：** 既存の手動の dispatch だけ。Sandbox の試験の手順（`sandbox` の印）を加える場合は、`ci.yml` の変更としてオーナーの承認が要る。
- **モジュールの責任：** 評価（`agent_core.evaluation`）、計画（`agent_core.planning`）、Broker（`agent_core_broker`、別のプロセス）を分け、import の契約で方向を強制する。モデルの交換は ModelPort と ModelProfile の Adapter で行い、評価の側に影響させない。
- **文書の言語：** 人向けは日本語、AI 向けの実装の指示は英語（`PHASE_2_AGENT_INSTRUCTIONS.md`）。

## 9. オーナーの判断が要る事項（選択肢、推奨、理由）

| # | 事項 | 選択肢 | 推奨 | 理由 |
|---|---|---|---|---|
| O-1 | Phase 2 の開始 | ① この計画で開始。② 計画を直してから | ① の後、WP2-01 から | 安全の順序（SG-1）を含む |
| O-2 | P-4 の方針と修正（Sandbox の設計の S-1、S-2） | ① 確定案で承認、値は候補のまま。② 別の案 | ① | C1 の正本と整合 |
| O-3 | D.V19 の拡張の項目（`unresolved`、`grading_status`、`limits_hit`） | ① 承認。② 正本の値だけで記録 | ① | 正本の値を変えずに、結果不明と評価不能を区別できる |
| O-4 | A.I11 の保存の形式 | ① 案 A（v4 の `provenance`）。② 案 B（台帳）。③ 案 C（評価の記録だけ） | ① | A.I11（Step のイベントに版の項目）を満たし、v3 と互換 |
| O-5 | Docker、daemon の起動、AppArmor、import の契約、OS 利用者、イメージ（S-3〜S-7） | Sandbox の設計の10章 | S-3 は併用、S-4 は AppArmor のある環境でだけ信頼しないコードを実行、S-5 は狭く許可、S-6 は別の利用者 | 安全の条件を下げない |
| O-6 | 統計の実装の依存 | ① 標準ライブラリだけ。② `numpy`・`scipy` を加える | ① | 新しい依存なし。遅い場合に改めて判断 |
| O-7 | Task Catalog の作り手 | ① dev は Claude Code が案を作りオーナーが承認して固定、validation はオーナーか独立した担当が作る。② 両方を Claude Code が作る（validation の盲検性がないと記録） | ① | D の6.1（validation は開発の作業の中で中身を見ない運用） |
| O-8 | ブランチとタグ | ① `impl/phase-2-evaluation` を `92c18c4` から作り、`phase-1-complete` のタグ。② 今のブランチで続ける | ① | Phase 1 の完了の時点を保つ |
| O-9 | 並行実行 | ① Phase 2 は直列だけ。② 並行を入れる（FINAL-R05 が先） | ① | 評価の正しさを優先 |
| O-10 | SG-1 の独立した確認の担当と環境 | ① Codex などの独立した担当が、AppArmor のある環境で。② Claude Code の確認だけ | ① | D14（独立した確認） |
| O-11 | Baseline B の独立審査の時期 | ① WP2-13 の後、正式な比較の前。② もっと早く（構成の案の段階で） | ② と ① の両方（案の段階で一度、実装の後に一度） | 弱い相手にしないため（D の4.1） |

## 10. Phase 2 の正式な完了の条件

`PHASE_2_IMPLEMENTATION_PLAN.md` の3章（Technical Gate：統計のシミュレーション、予算の階層、コーディングエージェント v1 の完成の条件。Safety Gate：C1 の12.2 の Sandbox の試験、P-5、SG-1。オーナーの承認：Baseline B の独立審査。品質：全試験、8種類の検査、CI）。
