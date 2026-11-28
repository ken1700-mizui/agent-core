# Phase 1 の安全性の確認

> 文書の状態：**DRAFT**（オーナーの承認 D8 による確認。独立した安全性の確認の前）　作成：2026-11-27　更新：2026-11-27（P-6 の修正、`97553b2`。最終独立品質レビューの設計の適合の確認、10章。最終の CI、11章。オーナーの最終承認、12章）
>
> - 正本：承認済みの `docs/design/phase-0/ROADMAP.md`（Phase 1 の Technical Gate と Safety Gate、0.2）、`C1_SAFETY.md`、`C2_DATA_SECURITY.md`、`E_INTEGRATION.md`、`PHASE_1_IMPLEMENTATION_PLAN.md`（2章 F07、3章）。
> - 対象の実装：初回の CI の対象 `71208adfc7884da097be377053a38f16d4886293`、と P-6 の修正 `97553b2`（**P-6 は初回の CI の後の変更で、CI ではまだ検証していない。** 手元の検査の結果は 9章）。
> - 方法：設計、実装、既存の試験、過去のレビューの照合だけ。**新しい侵入試験や、外部のシステムを対象とした検査は行っていない。** これは包括的なセキュリティ監査ではない。
> - Phase 3A でも、実 API の接続の前に、改めて確認する（オーナーの承認 3）。

## 1. 通信の区別

| 区分 | 内容 | 扱い |
|---|---|---|
| Agent Core の実行時の通信 | Runtime、CLI、ツール、モデルの経路からの外部への通信 | **0 件であることが Phase 1 の Safety Gate。** 外部へ通信するコードの経路がない（import-linter の契約 `phase1-no-network-or-process`）。試験の中では Python の `socket` を遮断する。ネットワークのない名前空間（`unshare -n`）で全試験が成功した |
| 開発と CI の準備のための通信（Agent Core の実行ではない） | 依存の取得（`uv sync --locked`、PyPI）、uv の wheel の取得（ハッシュ検査付き）、GitHub への push、Action の取得、公式の資料の読み取り（許可された範囲）、初回の CI の起動（承認 2） | Agent Core のプロセスからではない。CI では依存の取得の後、すべての手順が `--offline`。秘密情報は使わない |

## 2. 安全の条件ごとの確認

判定の語：**満たす**（設計の要求を、Phase 1 の範囲で実装と試験で確かめた）、**一部**（一部の経路だけ）、**未実装**（Phase 1 の計画の要求だが実装がない）。試験の結果は Claude Code の環境での結果（2138 件）と、初回の CI の結果（4章）による。

| # | 条件 | 設計の要求 | 実装 | 既存の試験 | 判定と保証の範囲 | 未検証 | 後の Phase の必須の条件 |
|---|---|---|---|---|---|---|---|
| S-1 | 実行時の外部通信の禁止 | ROADMAP Phase 1 Safety Gate「外部への通信が 0 件」、F07-5 | import の契約（`pyproject.toml`）、`tests/conftest.py` の遮断 | `test_import_contracts.py`、`test_network_guard.py`、`test_no_process_execution.py`、`unshare -n` での全試験 | **満たす**（コードの経路がないこと、Python の `socket` の遮断）。OS のレベルの遮断は主張しない | 子プロセスからの通信（Phase 1 は子プロセスを起動しない） | Phase 2：Sandbox のネットワークの隔離と自己診断。Phase 3A：OS のレベルの隔離の強化、Gateway |
| S-2 | 実 API・実モデル・実データを使わない | ROADMAP Phase 1 の範囲、F07、D1 | Fake Provider と Replay だけ。LiveProvider は存在しない。イベントのラベルは `synthetic` だけ（`runtime/service.py`）、SAG は `synthetic` 以外の書き込みを拒否（`safety/sag.py`）。ファイル操作は、オーナーが信頼した合成の作業ツリーだけ（`safety/pdp.py`）。起動のプロファイル（`types/profile.py`、`RuntimeStateMachine.load`、CLI の `HomeConfig.load`）と、信頼した合成のタスクの定義（`pdp.decide`、台帳の `_install_grant`）（P-6） | `test_fake_provider.py`、`test_replay_provider.py`、`test_safety.py`（SAG）、`test_wp08_*.py`、`test_p6_synthetic_only.py` | **満たす**（F07 の全項目。3章。ラベルと定義の検査は、データの内容が実データでないことの証明ではない） | — | Phase 3A：0.2 の安全の機能、LiveProvider の L1。3B：承認した Run だけ |
| S-3 | 認証情報を保持しない | ROADMAP Phase 1 Safety Gate「認証情報が存在しないこと」、C1.S11 | 認証情報を扱うコードがない。秘密情報の検査（CI と各コミット） | `test_secret_scan.py`、鍵が出力に出ない試験（`test_wp10_cli*.py`）、I8 | **満たす**（Phase 1 の範囲）。合成の MAC の鍵（ホームの中、0600）はあるが、外部の認証情報ではない | 秘密情報の検査は決まった規則だけ | Phase 3A：認証情報の保護、P3 を別の OS ユーザーに（C1.S24）、SAG |
| S-4 | 承認と Permit の管理 | C1.S4〜S6・S14・S20・S27、A.I13・A.I15 | 実行のゲート、台帳の Permit・Receipt、ExactApproval | `test_permits.py`、`test_wp09_*.py`、`test_wp10_cli.py`、`test_wp15_scenarios.py` S5 | **満たす**（同じプロセスの PEP） | 別のプロセスの PEP | Phase 3A：別のプロセスの PEP と Lease（E.Z5） |
| S-5 | 予算の上限と費用の記録 | A.I5、D.V10〜V13・V16・V17、E.Z7〜Z9 | 台帳（予約、確定、保留、上限、超過） | `test_budget_ledger.py`、`test_cost_rules.py`、`test_wp09_*.py`、`test_qr01_reconcile.py` | **満たす**（合成の XTU） | 実際の価格表 | Phase 3A：Run の単位の独立した予算の台帳、SpendApproval、AUDIT-02 |
| S-6 | 停止と再起動の後の状態 | C1.S14・S22、A.I12・A.I18 | 台帳の停止の記録、OS のロック、復旧 | `test_wp10_cli_process.py`、`test_wp12_*.py`、`test_wp15_fixes.py` | **満たす**（停止は再起動で失われない、古い承認は無効）。C1.S22 の停止の状態のファイルは Phase 1 にない | 承認待ちの Run の再開（A.I12、オーナーの判断待ち） | Phase 2：Sandbox の停止とタイムアウト。Phase 3A：Process Supervisor |
| S-7 | Journal と台帳の整合 | A.I1・A.I3、E.Z17、QR-02・QR-03 | 追記だけの Journal、同じトランザクションの台帳、開くときの検査 | `test_sqlite_journal.py`、`test_journal_processes.py`、`test_qr02_qr03_consistency.py` | **満たす**（開くときに不一致を拒否） | DB を持つ者の一貫した書き換え（主張しない） | Phase 4：バックアップと独立した証人 |
| S-8 | ファイル操作の制限 | C1.S16・S19（Phase 1 の部分）、ADR-0008 | File Broker（`dir_fd` の解決、目録の MAC、実行券） | `test_wp08_*.py`（特権の5件を含む） | **満たす**（ADR-0008 の範囲） | 協調しない書き込み元、root | Phase 2：Sandbox へのコピーとホストへの反映（S18・S19） |
| S-9 | 結果不明のときの安全な復旧 | A.I4・A.I16、E.Z32、QR-01 | 復旧の表、`ReconcileOutcome` | `test_wp12_*.py`、`test_qr01_reconcile.py`、`test_wp13_integration.py` I5 | **満たす**（再実行しない、推測しない） | 実行報告の永続化 | Phase 3A：結果不明の操作の管理（0.2） |
| S-10 | Context と信頼の階層 | B1.C1〜C4・C6・C7、C1.S2・S13 | Context Engine | `test_wp14_*.py`、`test_wp15_scenarios.py` S5・S7 | **満たす**（合成のデータ） | 実際のプロンプトインジェクションの完全な防止（主張しない） | Phase 3A：実データの前の鍵付きのハッシュ |
| S-11 | 完了の判断の検証 | E.Z13・Z15、QR-04、WP15-R06・R07 | 完了のトランザクションの検査 | `test_wp15_r06.py`、`test_wp12_fixes.py` R07、`test_qr04_ordering.py` | **満たす**（同じ Run の書き込み） | 別の Run と外部の書き込み元の順序 | — |

## 3. F07（合成のデータ専用の、実行のときの強制）の照合

| F07 の項目 | 要求 | 実装 | 判定 |
|---|---|---|---|
| 1. 起動の条件 | 構成の `profile` が `phase1_synthetic` でなければ、Runtime は起動を拒否する | **P-6 で実装（`97553b2`）。** `RuntimeStateMachine.load` は必須の `profile` を、Journal を読む前に検査する（1つの関数 `require_phase1_profile`）。CLI のホームの構成に `profile` を持ち、`HomeConfig.load` が Journal を開く前に検査する（欠けている・別の値は `PROFILE_REFUSED`、型の違いは `HOME_UNSAFE`。所有者の epoch も変わらない）。既定の値はない。LiveProvider と Gateway のコードがないこと（import の契約）も維持 | **解消**（`test_p6_synthetic_only.py`。初回の CI の後の変更で、CI では未検証） |
| 2. 作業の場所 | 合成の作業ツリーだけ、署名付きの目録がなければ拒否 | File Broker、PDP の `trusted_synthetic_workspaces` | 満たす |
| 3. タスク | PDP は `synthetic: true` の合成の Task Catalog のタスクだけを許す | **P-6 で最小限を実装（`97553b2`）。** 管理された合成のタスクの定義（`SyntheticTaskDefinition`、その ref）。オーナーのポリシーが信頼する定義の一覧（`trusted_synthetic_tasks`、既定なし）、オーナーが入れるグラントが定義を名指す（`synthetic_task_ref`、欠けていれば拒否）。台帳はその定義でないグラントを入れず、PDP は決定を拒否する（`SYNTHETIC_TASK_UNTRUSTED`）。モデルはこれらを設定できない。Phase 2 の Task Catalog はこの定義の ref を供給する形で広げる（前倒しはしていない） | **解消**（Phase 1 の範囲。`test_p6_synthetic_only.py`。CI では未検証）。**限界：** Run と管理された定義の結び付きはオーナーの経路への信頼による。タスクの目標の文面と定義を暗号的に結び付けてはいない |
| 4. 保存 | SAG は `synthetic` のラベルのない書き込みを拒否 | `safety/sag.py` | 満たす |
| 5. ネットワーク | 外部へ通信するコードの経路がない | import の契約 | 満たす（OS のレベルの遮断は Phase 3A） |

- 1 と 3 は、オーナーの指示（P-6）で限定的に修正した（`97553b2`）。独立した安全性の確認の対象にする（8章）。

## 4. 初回の CI の結果

run `46031933145`（対象 `71208ad`、標準の runner `ubuntu-24.04`、billable 0 ms）：8種類の検査がすべて成功、通常の試験 2133 件と特権の試験5件（合計 2138 件）がすべて成功。2章の試験は、すべてこの実行に含まれる。詳細は `PHASE_1_ACCEPTANCE.md` の11章。

## 5. Technical Gate と Safety Gate の評価（Phase 1、ROADMAP）

- **Technical Gate**（状態遷移、Journal、復旧の不変条件の試験の合格、障害の注入を含む）：初回の CI の結果で条件を満たす（`PHASE_1_ACCEPTANCE.md` の12章）。
- **Safety Gate**（外部への通信 0 件、Permit のない副作用 0 件、認証情報が存在しないこと）：2章の S-1、S-3、S-4 の範囲で満たす。F07 の 1・3 は P-6 で解消した（ただし P-6 は次回の CI で確かめる）。合格と認めるかはオーナーが決める。

## 6. 次の独立した安全性の確認の対象

1. P-6（F07 の 1・3）の実装：起動の経路のすべてが検査を通ること（`RuntimeStateMachine.load` を迂回して状態を作れないか）、合成のタスクの定義の結び付きの範囲（オーナーの経路への信頼）。
2. 権限の経路（Permit、Receipt、承認、停止）を迂回できないこと。CLI からも Agent からも（AGENTS.md の 4.12）。
3. 台帳と Journal の改変の検出の範囲（QR-02・QR-03 の検査）。
4. File Broker のパスの解決（ADR-0008）と、特権の5件の試験の範囲。
5. 秘密情報の検査の規則の範囲と限界。
6. 供給網（`uv.lock` のハッシュ、Action の SHA、uv の wheel、`setup-python` の Python、runner のイメージ）。
7. CLI の入力と出力（端末の制御文字、エラーの文言に機密が出ないこと）。

## 7. 後の Phase の条件（Phase 1 の確認で省略しない）

- **Phase 2：** Sandbox と子プロセスの管理、ネットワークの隔離と自己診断、停止とタイムアウト、信頼しないコードを実行する前の隔離の試験、A.I17 の設計上の再評価。
- **Phase 3A：** 実 API の接続の前の追加の Technical Gate と Safety Gate、OS のレベルの隔離の強化、外部通信の Gateway、Process Supervisor、独立の予算の台帳、認証情報と実データの保護、AUDIT-02、AUDIT-04。Phase 1 の安全性の確認を改めて行う。

## 8. 次の独立した安全性の確認のための対応表

| 安全の条件 | 設計 | 製品のコード | 既存の試験 | CI の証拠 | 保証の範囲 | 未検証 |
|---|---|---|---|---|---|---|
| 外部通信の禁止 | ROADMAP Phase 1、F07-5、C1.S12 | `pyproject.toml` の import-linter の契約、`tests/conftest.py` | `test_import_contracts.py`、`test_network_guard.py`、`test_no_process_execution.py` | run `46031933145`（5 kept、全試験の成功） | コードの経路と Python の `socket` | OS のレベル |
| 合成のデータ専用 | F07-1〜5、D1 | `types/profile.py`、`runtime/service.py`、`safety/pdp.py`、`safety/sag.py`、`adapters/sqlite_ledger.py` の `_install_grant`、`agent_core_cli/environment.py` | `test_p6_synthetic_only.py`、`test_safety.py`、`test_fake_provider.py`、`test_replay_provider.py` | P-6 は**未検証**（次回の CI） | 起動、タスク、作業ツリー、保存、通信の印 | データの内容が実データでないこと |
| 認証情報を持たない | ROADMAP Phase 1 Safety Gate | 秘密情報の検査 `tools/secret_scan.py` | `test_secret_scan.py` | run `46031933145` | 決まった規則 | 規則にない形 |
| 承認と Permit | C1.S4〜S6・S14・S20・S27 | `runtime/execution_gate.py`、`runtime/pipeline.py`、`adapters/sqlite_ledger.py` | `test_permits.py`、`test_wp09_*.py`、`test_wp10_cli.py` | run `46031933145` | 同じプロセスの PEP | 別のプロセスの PEP（3A） |
| 予算と費用 | A.I5、D.V10〜V17 | `cost/rules.py`、`adapters/sqlite_ledger.py` | `test_budget_ledger.py`、`test_cost_rules.py`、`test_qr01_reconcile.py` | run `46031933145` | 合成の XTU | 実際の価格表 |
| 停止と再起動 | C1.S14、A.I12・A.I18 | `runtime/recovery.py`、`adapters/runtime_lock.py` | `test_wp10_cli_process.py`、`test_wp12_*.py` | run `46031933145` | 1つのホスト | Process Supervisor（3A） |
| Journal と台帳の整合 | A.I1、QR-02・QR-03 | `adapters/sqlite_journal.py`、`adapters/sqlite_ledger.py` | `test_sqlite_journal.py`、`test_qr02_qr03_consistency.py` | run `46031933145` | 開くときの不一致の拒否 | DB を持つ者の一貫した書き換え |
| ファイル操作 | C1.S16・S19、ADR-0008 | `adapters/file_broker.py` | `test_wp08_*.py`（特権の5件） | run `46031933145`（特権の5件を root で） | ADR-0008 の範囲 | 協調しない書き込み元 |
| 結果不明の復旧 | A.I4・A.I16、QR-01 | `runtime/recovery.py`、`_reconcile_outcome` | `test_wp12_*.py`、`test_qr01_reconcile.py`、`test_wp13_integration.py` | run `46031933145` | 再実行しない、推測しない | 実行報告の永続化 |
| Context と信頼の階層 | B1.C1〜C7 | `context/` | `test_wp14_*.py` | run `46031933145` | 合成のデータ | 実際のインジェクション |
| 完了の判断 | E.Z13・Z15、QR-04 | `_completion_basis`、`_write_order_reasons` | `test_wp15_r06.py`、`test_qr04_ordering.py` | run `46031933145` | 同じ Run の書き込み | 別の Run・外部 |

## 9. P-6 の後の手元の検査（独立した実行ではない）

製品のコードは `97553b2` の時点（試験 2162 件）。

| 実行 | 利用者・環境 | 成功 | 失敗 | 除外・未実行 |
|---|---|---|---|---|
| 8種類の検査 | root | すべて成功（pytest 2162） | 0 | 0 |
| 全試験の連続2回 | root | 2162、2162 | 0 | 0 |
| ネットワークのない環境 | root、全試験 | 2162 | 0 | 0 |
| CI の順序の検査 | `nobody`（`nobody` が所有する clone） | 秘密情報（3方式）、`uv lock --check`、ruff、ruff format、mypy、import-linter | 0 | `uv sync --locked --offline --check` は `nobody` では未実行（root では成功） |
| 通常の試験（`-m "not privileged"`） | `nobody` | 2157 | 0 | 5（除外） |
| 同（`-m` なし） | `nobody` | 2157 | 5（`PRIVILEGED_NOT_RUN`。成功に数えない） | 0 |
| 特権の試験（`-m privileged`） | root | 5 | 0 | 2157（除外） |
| ネットワークのない環境、通常の試験 | `nobody` | 2157 | 0 | 5（除外） |
| GitHub Actions | — | — | — | **未実行**（P-6 は次回の CI の対象。実行には別の承認が要る） |

## 10. 最終独立品質レビューの設計の適合の確認と、検証の段階の区別（2026-11-27、対象 `aae033a`）

Codex の最終独立品質レビュー（`aae033a7c12c9e87f084d366a23aaa7796226353`）の結果：
- 総合判定：**CONDITIONAL_PASS**。CRITICAL・HIGH・MEDIUM 0件、LOW 1件（FQ-01、文書の古い記述）。
- 新しい製品の不具合なし。
- 詳細は `PHASE_1_ACCEPTANCE.md` の1.3。

**このレビューは品質のレビューと設計の適合の確認であり、包括的なセキュリティ監査ではない。** 包括的な監査の正式な範囲と実施の時期、Technical Gate と Safety Gate の最終の判定は、**オーナーの承認待ち**のまま。

| # | 条件 | 実装済み | Claude Code の環境で検証済み（root と `nobody`、ネットワークなし） | Codex で独立に確認済み | 最終 CI で未検証 | 後続の Phase の必須の条件 |
|---|---|---|---|---|---|---|
| S-1 | 実行時の外部通信の禁止 | 済み | 済み（全 2340 件、ネットワークなし） | import-linter の5つの契約が成功（静的）。設計の適合を確認 | 現在のコードは未検証 | OS のレベルの遮断（Phase 3A） |
| S-2 | 実 API・実モデル・実データを使わない | 済み（P-6 の起動のプロファイル） | 済み | 設計の適合を確認、import-linter | 現在のコードは未検証 | LiveProvider の条件（Phase 3B 以後、D1・D5） |
| S-3 | 認証情報を保持しない | 済み | 済み（秘密情報の検査） | 秘密情報の検査が成功 | 現在のコードは未検証 | 認証情報の保護（Phase 3A） |
| S-4 | 承認と Permit の管理 | 済み | 済み | 設計の適合を確認（試験の一部は環境の条件で未成功、1.3） | 現在のコードは未検証 | — |
| S-5 | 予算の上限と費用の記録 | 済み | 済み | 設計の適合を確認 | 現在のコードは未検証 | 独立の予算の台帳（Phase 3A）。`_totals` の性能（上限を上げる前） |
| S-6 | 停止と再起動の後の状態 | 済み（P-2 の制限の下でも停止・復旧を妨げない） | 済み | 設計の適合を確認 | 現在のコードは未検証 | 常時の期限の監視（P-4・P-5、Phase 2） |
| S-7 | Journal と台帳の整合 | 済み（開く処理の検査の逐次化の後も同じ検査） | 済み（改善の前後のハッシュの一致） | Journal に関わる重点の試験 120 件が成功（全体の成功数には加えない） | 現在のコードは未検証 | Snapshot（Phase 4、D9） |
| S-8 | ファイル操作の制限 | 済み | 済み（特権の5件は root で成功） | **独立の確認なし**（`AREA_UNSAFE`・`HOME_UNSAFE` の環境の不適合。特権の5件は未実行） | 現在のコードは未検証 | Sandbox へのコピーと反映（Phase 2） |
| S-9 | 結果不明のときの安全な復旧 | 済み | 済み | 設計の適合を確認 | 現在のコードは未検証 | — |
| S-10 | Context と信頼の階層 | 済み | 済み | 設計の適合を確認 | 現在のコードは未検証 | 実際のプロンプトインジェクション（Phase 3A 以後） |
| S-11 | 完了の判断の検証 | 済み | 済み | 設計の適合を確認 | 現在のコードは未検証 | — |
| F07-1・3 | 起動の構成と合成のタスクの検査（P-6）と、互換性の修正 | 済み | 済み | 設計の適合を確認 | 現在のコードは未検証（初回の CI の後の変更） | Task Catalog（Phase 2） |
| P-2 | 運用の制限（暫定の上限） | 済み | 済み | 設計の適合を確認 | 現在のコードは未検証 | 上限の再評価（性能の改善の後） |

- **「設計の適合を確認」は、Codex がコードと設計を照合したことを指す。** その項目の試験の全件が Codex の環境で成功したことではない。
  - Codex の環境で成功しなかった 381 件と、未実行の特権の5件は、独立した実行では未検証。
  - これらは次回の CI で確かめる。
- 「Claude Code の環境で検証済み」は独立した実行ではない（`PHASE_1_ACCEPTANCE.md` の1.4）。
- 「最終 CI で未検証」：初回の CI（`71208ad`、2138 件）の後のすべての変更が対象。次回の CI の実行には、オーナーの別の承認が要る。

## 11. 最終の CI の結果（2026-11-27）

- run **`46082898611`**（https://github.com/ken1700-mizui/agent-core/actions/runs/46082898611 ）。
- 対象 **`93435c82e06a168794cf68c6ecf1fc598815d426`**（製品のコードと試験は `aae033a` と同一）。
- 実行は1回。標準の runner `ubuntu-24.04`。**billable 0 ms**。
- 8種類の検査がすべて成功した。
- 通常の試験 2335 件と特権の試験5件（合計 **2340 件**）が、すべて成功した。失敗、エラー、スキップ、未実行は 0。
- 詳細は `PHASE_1_ACCEPTANCE.md` の19章。

これにより、10章の表の「最終 CI で未検証」の列は、S-1〜S-11、F07-1・3、P-2 のすべてで **最終の CI（`93435c8`）で成功** になった。

- S-8 は、Codex では独立に確認できなかったが、CI の条件に合う環境で特権の5件を含めて成功した。
- 「後続の Phase の必須の条件」の列は変わらない。

**Safety Gate の評価（Claude Code の評価。判定はオーナー）：**
- 外部への通信 0 件、Permit のない副作用 0 件、認証情報が存在しないことは、2章の範囲で満たす（`PHASE_1_ACCEPTANCE.md` の20章）。
- 包括的なセキュリティ監査ではない。
- OS のレベルの完全な隔離は主張しない。
- 監査の範囲と時期、両 Gate の最終の判定は、オーナーの承認待ち。

## 12. オーナーの最終承認（2026-11-28）

- **Safety Gate：定められた保証の範囲の内で PASS**（オーナーの承認、`OWNER_DECISIONS.md` の D12）。保証の範囲は2章と10章、主張しないことは `PHASE_1_ACCEPTANCE.md` の20章。この章は追記で、1〜11章の記録は書き換えていない。
- Codex の判定（CONDITIONAL_PASS）と最終の CI の結果（2340 件の成功）は、別々の結果として保持する。
- **包括的なセキュリティ監査（D14）：**
  - 時期：Phase 3A の終わり、Phase 3B で実 API に接続する前。
  - 方法：開発者とは独立した適切な検証の環境で行い、必要に応じて専門家の確認を受ける。
  - 対象：`docs/decisions/PHASE_1_COMPLETION.md` の7章。
- **Phase 2 の安全の条件：**
  - 信頼しないコードを実行する前に、Sandbox に関する独立した安全性の確認を別に行う。
  - P-5 の受入の試験（最新の P-4 の5章の10項目）に合格する。
  - 監査の延期を理由に、これらを省略しない。
- 7章の後の Phase の条件と、Phase 3A の Technical Gate と Safety Gate は、そのまま維持する。

