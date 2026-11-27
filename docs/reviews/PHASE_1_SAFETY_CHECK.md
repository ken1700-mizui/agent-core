# Phase 1 の安全性の確認

> 文書の状態：**DRAFT**（オーナーの承認 3「Phase 1 の安全性の確認を正式な完了の前に行う」による確認。独立した安全性の確認の前）　作成：2026-11-27
>
> - 正本：承認済みの `docs/design/phase-0/ROADMAP.md`（Phase 1 の Technical Gate と Safety Gate、0.2）、`C1_SAFETY.md`、`C2_DATA_SECURITY.md`、`E_INTEGRATION.md`、`PHASE_1_IMPLEMENTATION_PLAN.md`（2章 F07、3章）。
> - 対象の実装：`71208adfc7884da097be377053a38f16d4886293`（初回の CI の対象と同じ）。
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
| S-2 | 実 API・実モデル・実データを使わない | ROADMAP Phase 1 の範囲、F07、D1 | Fake Provider と Replay だけ。LiveProvider は存在しない。イベントのラベルは `synthetic` だけ（`runtime/service.py`）、SAG は `synthetic` 以外の書き込みを拒否（`safety/sag.py`）。ファイル操作は、オーナーが信頼した合成の作業ツリーだけ（`safety/pdp.py`） | `test_fake_provider.py`、`test_replay_provider.py`、`test_safety.py`（SAG）、`test_wp08_*.py` | **一部**（下の F07 の確認を参照） | — | Phase 3A：0.2 の安全の機能、LiveProvider の L1。3B：承認した Run だけ |
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
| 1. 起動の条件 | 構成の `profile` が `phase1_synthetic` でなければ、Runtime は起動を拒否する | **その名前の構成の検査は見つからなかった。** LiveProvider と Gateway のコードがないこと（import の契約）は満たす | **一部（未実装の部分あり）。報告だけ、修正しない** |
| 2. 作業の場所 | 合成の作業ツリーだけ、署名付きの目録がなければ拒否 | File Broker、PDP の `trusted_synthetic_workspaces` | 満たす |
| 3. タスク | PDP は `synthetic: true` の合成の Task Catalog のタスクだけを許す | **その検査は見つからなかった。** Task Catalog は ROADMAP では Phase 2。PDP は、オーナーが信頼した合成の作業ツリーと、合成のハッシュの方式だけを許す | **一部（未実装の部分あり）。報告だけ、修正しない** |
| 4. 保存 | SAG は `synthetic` のラベルのない書き込みを拒否 | `safety/sag.py` | 満たす |
| 5. ネットワーク | 外部へ通信するコードの経路がない | import の契約 | 満たす（OS のレベルの遮断は Phase 3A） |

- 1 と 3 は、承認された作業の範囲（製品のコードを変えない）を超えるので、修正していない。独立した安全性の確認とオーナーの判断の対象にする（6章）。

## 4. 初回の CI の結果

run `46031933145`（対象 `71208ad`、標準の runner `ubuntu-24.04`、billable 0 ms）：8種類の検査がすべて成功、通常の試験 2133 件と特権の試験5件（合計 2138 件）がすべて成功。2章の試験は、すべてこの実行に含まれる。詳細は `PHASE_1_ACCEPTANCE.md` の11章。

## 5. Technical Gate と Safety Gate の評価（Phase 1、ROADMAP）

- **Technical Gate**（状態遷移、Journal、復旧の不変条件の試験の合格、障害の注入を含む）：初回の CI の結果で条件を満たす（`PHASE_1_ACCEPTANCE.md` の12章）。
- **Safety Gate**（外部への通信 0 件、Permit のない副作用 0 件、認証情報が存在しないこと）：2章の S-1、S-3、S-4 の範囲で満たす。ただし 3章の F07 の 1・3 の未実装の部分がある。合格と認めるかはオーナーが決める。

## 6. 次の独立した安全性の確認の対象

1. F07 の 1（起動の構成の検査）と 3（合成のタスクの検査）の未実装の部分を、Phase 1 の Safety Gate でどう扱うか。
2. 権限の経路（Permit、Receipt、承認、停止）を迂回できないこと。CLI からも Agent からも（AGENTS.md の 4.12）。
3. 台帳と Journal の改変の検出の範囲（QR-02・QR-03 の検査）。
4. File Broker のパスの解決（ADR-0008）と、特権の5件の試験の範囲。
5. 秘密情報の検査の規則の範囲と限界。
6. 供給網（`uv.lock` のハッシュ、Action の SHA、uv の wheel、`setup-python` の Python、runner のイメージ）。
7. CLI の入力と出力（端末の制御文字、エラーの文言に機密が出ないこと）。

## 7. 後の Phase の条件（Phase 1 の確認で省略しない）

- **Phase 2：** Sandbox と子プロセスの管理、ネットワークの隔離と自己診断、停止とタイムアウト、信頼しないコードを実行する前の隔離の試験、A.I17 の設計上の再評価。
- **Phase 3A：** 実 API の接続の前の追加の Technical Gate と Safety Gate、OS のレベルの隔離の強化、外部通信の Gateway、Process Supervisor、独立の予算の台帳、認証情報と実データの保護、AUDIT-02、AUDIT-04。Phase 1 の安全性の確認を改めて行う。
