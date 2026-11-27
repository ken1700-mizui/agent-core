# Phase 1 実装報告書

> 文書の状態：**DRAFT**（Codex の独立品質再レビューは CONDITIONAL_PASS。最終受入の準備中。CI、オーナーの最終承認の前）　作成：2026-11-26　更新：2026-11-27
>
> **Phase 1 の状態：独立品質レビュー指摘の修正提出済み・再レビュー待ち。** 正式な完了ではない。再レビュー、CI の実行、必要な修正、オーナーの最終承認の後に、初めて完了とする。Phase 2 には進んでいない。
>
> Codex の独立品質レビュー（判定 FAIL、CRITICAL 0、HIGH 1、MEDIUM 3、LOW 0）の4件を修正した（13章）。独立品質レビューは、包括的なセキュリティ監査ではない。セキュリティ監査は、まだ行われていない。

## 1. 概要

- 対象：`impl/phase-1-foundation`。Phase 1 の承認の記録を含む設計のコミット `e6b70c0` から、この報告書のコミットまで。
- 最新の HEAD：この版の報告書を含むコミット（SHA は最終報告に記す）。直前は `e3b75bb`（QR-04）。最初の提出は `98f5fd1`。
- 実装の範囲：単一のホスト、単一のオーナー、単一のエージェントの、合成のデータだけで動く基盤。実モデル、実 API、実データ、Docker、外部への接続は使っていない。実 API の費用は $0。
- 試験：2126 件（Phase 1 の開始時は 0 件。WP-12 のレビューの時点は 2038 件、最初の提出は 2079 件、QR-01 の後は 2089 件、QR-02・QR-03 の後は 2112 件、QR-04 の後は 2126 件）。8種類の検査は、すべて成功した。
- 変更の規模（`e6b70c0` から `7846a19`）：39 コミット、177 ファイル、約 48,700 行の追加。

## 2. 15 個の WP の状態

| WP | 内容 | レビュー | 状態 | 記録 |
|---|---|---|---|---|
| WP-01 | 構成、品質の検査、CI | 96/100（手元） | **CI が未実行。完了の条件を満たしていない** | STATUS.md の 2.1、README.md |
| WP-02 | 共通の型と Ports | 95/100 条件付き合格 → R01〜R03 修正 | 修正済み | `WP-02_COMMON_TYPES.md` |
| WP-03 | SQLite の Journal | 94/100 → R01〜R04 修正 | 修正済み | `WP-03_JOURNAL.md` |
| WP-04 | 状態機械 | 92/100 → R01〜R06 修正 | 修正済み | `WP-04_STATE_MACHINE.md` |
| WP-05 | Fake Provider と ModelPort | 95/100 → R01〜R03 修正 | 修正済み | `WP-05_FAKE_PROVIDER.md` |
| WP-06 | Replay Provider | 93/100 → R01〜R04 修正 | 修正済み | `WP-06_REPLAY.md` |
| WP-07 | Tool Registry | 95/100 → R01・R02 修正 | 修正済み | `WP-07_TOOL_REGISTRY.md` |
| WP-08 | 基本のファイル操作 | 94/100、第2回 95/100 → R06〜R09 修正 | 修正済み | `WP-08_FILE_OPS.md`、ADR-0008 |
| WP-09 | Safety・Cost・Permit | 88/100 → R01〜R07 修正、R07 の再レビューに合格 | 修正済み | `WP-09_SAFETY_COST.md` |
| WP-10 | 最小の CLI | 94/100 → R01〜R04 修正 | 修正済み | `WP-10_CLI.md` |
| WP-11 | 構造化ログ | 92/100 → R01〜R04 修正 | 修正済み | `WP-11_OBSERVABILITY.md` |
| WP-12 | 障害からの復旧 | 90/100 → R01〜R05 修正。独立品質レビューの QR-01〜QR-03 を修正 | **修正を提出、再レビュー待ち** | `WP-12_RECOVERY.md`（14・15章） |
| WP-13 | 最終統合試験 | 独立品質レビューで FAIL（4件） | **修正を提出、再レビュー待ち** | `WP-13_INTEGRATION.md`（7・8章） |
| WP-14 | 最小の Context Engine | 92/100 → R01〜R05 修正 | 修正済み | `WP-14_CONTEXT.md` |
| WP-15 | エージェントのループ | 91/100 → R01〜R07 修正。独立品質レビューの QR-04 を修正 | **修正を提出、再レビュー待ち** | `WP-15_AGENT_LOOP.md`（17・18章） |

「修正済み」は、ChatGPT のレビューの指摘を直したことを意味する。Codex の独立品質レビューは全体について受けた（判定 FAIL。指摘の4件は 13章）。包括的なセキュリティ監査は、まだ受けていない。

## 3. 実装したことと、実装していないこと

### 3.1 実装した（合成のデータの範囲）

- **Journal と台帳：** SQLite の追記だけの Journal（連番、ハッシュの連鎖、版の管理、開くときの全体の検証）。予算・権限・Permit・Receipt・予約の台帳を、同じ DB の同じトランザクションで記録する。投影（`rt_*`）と、その修復。Journal の版 8。
- **状態機械：** Task、Run、Step、Attempt の遷移と、Journal による復元（Rebuild）。
- **Safety と Cost：** 既定で拒否する PDP、ActionDescriptor、ExactApproval、一度だけで期限付きの Permit、Receipt、実行のゲート（MAC 付きの未実行と実行の報告）。整数の予算、予約、確定、未確定の保留、Task・Run・Step の上限、停止と失効。
- **モデル：** ModelPort、合成の Fake Provider、合成の記録の Replay Provider（3つのハッシュ）。
- **ツールとファイル：** Tool Registry（`builtin` だけ）。ワークスペースの中の1つのファイルの安全な読み取り、CREATE、REPLACE（ADR-0008 の `dir_fd` の解決、目録の MAC、実行券、一時ファイルの回収）。
- **Context Engine：** Context IR、信頼の階層、ProtectedFact、予算、3つのハッシュ。
- **エージェントのループ：** 単純な Task の、モデルの呼び出し、ツールの実行、ファイルの実際の内容による検証（基準ごとの結び付け。WP15-R07）、修復と上限。
- **CLI：** `agent_core` の外の合成の根（`agent_core_cli`）。init、status、recovery、対話のセッション（承認、停止、復旧、照合）。
- **復旧：** 所有権の取得、Rebuild、評価、明示的な復旧、照合（台帳で検査する `ReconcileOutcome`）、ProjectionRepair、一時ファイルの回収。
- **観測：** 構造化ログと監査ログ（ハッシュの連鎖）。
- **品質の仕組み：** 秘密情報の検査、import-linter の層の契約、Python の通信の遮断（試験の中）、mypy strict、ruff。

### 3.2 実装していない（Phase 1 の範囲の外、または後に残した）

- 実モデル、実 API、LiveProvider（Phase 3B 以降）。外部への通信と Gateway。
- Sandbox、OS の隔離、別のプロセスの PEP（Phase 3A）。
- 評価の環境と holdout（Phase 2。holdout には接続していない）。
- Memory、要約、取得、キャッシュ。削除、バックアップ、復元、鍵の管理。
- 鍵付きのハッシュ（実データの前に必要）。署名付きの監査のチェックポイント、独立した証人。
- Snapshot（A.I6）。会話の記録の永続化と、承認待ちの Run の再起動の後の再開（A.I12）。
- 常時の期限の監視（Phase 4。Phase 1 は明示的な復旧の時だけ）。
- 遠隔操作、Windows・macOS への対応。

## 4. 不変条件の対応表

全体の表は `docs/design/phase-1/WP-13_INTEGRATION.md` の3章にある。要約：

| 区分 | PASS | PARTIAL | NOT_TESTED | DEFERRED |
|---|---|---|---|---|
| A（A.I1〜I18） | 14 | A.I6、I11、I12、I17 | — | — |
| C1（S1〜S27） | 15 | S11、S15、S19（Phase 1 の File Broker の部分だけ） | — | S9、S10、S12、S18、S21、S22、S24〜S26 |
| B1（C1〜C23） | 13 | C14、C18、C20 | — | C5、C11〜C13、C15、C21、C22 |
| C2 | X13 | X4、X5、X18、X19、X21 | — | X12 とその他 |
| D | V10〜V13、V16、V17 | V14 | V19 | V1〜V9、V15、V18、V20、V21 |
| E | 本文の表のとおり | Z4、Z5（Phase 1 の部分は PASS、ack と Lease は後）、Z12、Z17、Z24、Z33 | Z1（構成の照合では不一致なし） | Z10 などの後の Phase の項目 |

実装があるだけで PASS にした項目はない。A.I11 の保証の範囲、D.V19 の未検証の事項、E.Z1 の用語と構成、E.Z5 の Phase 1 と後の Phase の区別、C1.S19 の Phase 1 の File Broker に当たる範囲は、`WP-13_INTEGRATION.md` の3章の表に記した。既存の部分的な試験を、すべての経路の証明としては扱っていない。

## 5. 統合試験の結果

`WP-13_INTEGRATION.md` の2章。I1〜I10 は、すべて PASS（I6 の Replay、I7、I10 は既存の試験の範囲）。新しい試験は 11 件で、すべて特性確認（最初の実行で成功。赤は確かめていない）。WP-12 で未検証だった、ファイルの確定の後・Journal の保存の前の SIGKILL を、I5 で確かめた（結果不明を保ち、ファイルの内容を証拠にせず、再実行しない）。

## 6. 品質と障害試験の結果

| 検査 | 結果 |
|---|---|
| 8種類の検査 | すべて成功（2126 件。最初の提出は 2079 件） |
| 連続2回の全試験 | 2回とも成功（2126 件） |
| クリーンな別の作業ツリー | 成功（`uv sync --check` は初回の同期の前に「環境が古い」と報告。想定どおり） |
| ネットワークのない環境（`unshare -n`） | 2126 件が成功 |
| 一般ユーザーの権限（`nobody`） | 2026-11-27 の時点（14章）：`-m "not privileged"` で 2129 件がすべて成功。特権の5件は root で `-m privileged` を別に実行して成功。`-m` なしでは特権の5件が `PRIVILEGED_NOT_RUN` で失敗する（成功に数えない） |
| 例外の注入 | WP-08、WP-09、WP-12、WP-15 の試験 |
| 実際のプロセスの停止 | SIGKILL と SIGINT：WP-03、WP-08、WP-09、WP-10、WP-12、WP-13 の子プロセスの試験 |
| GitHub Actions の CI | **未実行**（指示により実行していない） |

## 7. 既知の制約とリスク

- DB とファイルの間は原子的でない（ADR-0008）。結果不明として保ち、自動で再実行しないことで扱う。
- 同じ利用者で協調しないプロセス、root、DB を持つ者の一貫した書き換えは、検出を主張しない。
- 実行報告はプロセスの中だけで、再起動の後は照合の証拠にならない（結果不明のまま、オーナーの判断に回す）。
- ハッシュは鍵のない SHA-256。実データでは鍵付きのハッシュが必要。
- 価格表、上限、期限の値は、試験の値（`status: test`、修復の上限 3 など）。運用の値は承認されていない。
- 試験の中の通信の遮断は Python の `socket` だけ。OS のレベルの隔離は Phase 3A。
- CI の Action はタグで参照しており、SHA に固定していない。
- `entity_times`・`run_status_times` の時刻の値と、`operation_results` の digest と実際のバイトの一致は、記録だけでは検証できない（存在、対象、状態、境界だけを確かめる。QR-03）。
- 書き込みと検証の順序は、同じ Run の書き込みについてだけ Journal で証明する。別の Run や外部の書き込み元は扱わない（QR-04）。
- `sqlite_ledger.py`（約 3,500 行）に責務が集まっている（技術的負債。分割の案と原子性の条件は `WP-12_RECOVERY.md` の 15.5）。

## 8. Phase 1 の完了を妨げる事項（Blocker）

1. **CI が未実行**（WP-01）。Action の SHA の固定と uv のハッシュの固定は済んだ（`89f8584`）。`workflow_dispatch` は既定のブランチ（空の `main`）に定義がないと起動できないことを公式の資料で確認した。実行の方法のオーナーの決定と実行の許可が要る（`PHASE_1_ACCEPTANCE.md` の3章）。
2. **独立品質レビューの指摘（QR-01〜QR-04）の修正の再レビューが済んでいない。**
3. **包括的なセキュリティ監査が行われていない。**
4. **オーナーの最終承認がない。**
5. オーナーの判断が要る設計との差：A.I6（Snapshot がない）、A.I12（承認待ちの Run は再起動の後に再開できず、中止される）、A.I17（期限は明示的な復旧の時だけ検査）。それぞれ別の延期の候補として、条件を `WP-13_INTEGRATION.md` の8章に記した。一括して Phase 4 に送らない。A.I17 は Phase 2 の開始の前に、タイムアウトと停止の処理との関係を再評価する。今回は承認も変更もしていない（移しただけで解決とはしない）。

## 9. Phase 2 の前に必要な事項

- 上の Blocker の解消。
- AUDIT-02（予算のタイムゾーン）、AUDIT-03（削除の前）、AUDIT-04、A-P6（初回 Phase 0 報告書の全文との照合）は、それぞれの必須の条件として残っている。
- 評価の環境の設計の確認（Phase 2 の範囲。holdout はこのリポジトリと別）。
- OS の隔離、鍵付きのハッシュは、それぞれ Phase 3A と実データの前の条件として残っている。

## 10. 再レビューとセキュリティ監査で重点的に見てほしい箇所

再レビュー（QR-01〜QR-04）：`sqlite_ledger.py` の `_refuse_cost_only`、`_reconcile_outcome` の下限、`_journal_view`・`_check_chain`・`_check_mirror`・`_check_ledger_records`・`read_run_status_since`、`_run_spans`・`_write_order_reasons`・`read_committed_writes`。`recovery.py` の `conflicts`。`agent_loop.py` の `_committed_writes`。

従来の重点：

1. `src/agent_core/adapters/sqlite_ledger.py`：台帳の検査（ペアリング、`_reconcile_outcome`、`_completion_basis`、`repair_mirror`）。正本と投影の分離。
2. `src/agent_core/runtime/recovery.py`：復旧の表、未実行の証明（Receipt の状態と owner_epoch）、再入の拒否、費用の保留。
3. `src/agent_core/runtime/execution_gate.py` と `pipeline.py`：Permit、Receipt、MAC 付きの報告、結果の digest。
4. `src/agent_core/adapters/file_broker.py`：ADR-0008 の `dir_fd` の解決、確定の前後の失敗の区別。
5. `src/agent_core/runtime/agent_loop.py`：検証と完了の排他、基準ごとの証拠。
6. `src/agent_core_cli/`：オーナーの経路だけを使うこと、出力の無害化、終了と再起動。
7. `pyproject.toml` の import-linter の契約と、`tests/conftest.py` の通信の遮断。
8. 試験が本当に振る舞いを確かめているか（特に特性確認と記した WP-13 の試験）。

## 11. 依存関係と設計の変更

- **依存関係：** 変更なし。`uv.lock` は WP-01（`cb1df4e`）の後、変わっていない。承認された直接の依存だけ（`docs/adr/0006-toolchain.md`）。`pyproject.toml` は、import-linter の契約、mypy の pydantic の plugin（既存の依存の設定）、試験のパスだけを変えた。
- **ADR：** ADR-0008（`dir_fd` によるパスの解決）を WP-08 で加えた（PROPOSED）。
- **設計の変更（Phase 1 の実装の中での決定）：** Journal の版 1 から 8（各 WP の文書に記録）。`ReconcileOutcome` の台帳の操作、`entity_times`・`run_status_times`・`operation_results` の表（WP-12 の14章）。基準ごとの `CriterionCheck`（WP-15 の17章）。オーナーの決定の記録（`OWNER_DECISIONS.md`、`PHASE_1_APPROVAL.md`）は変えていない。

## 12. Phase 1 の開始からの変更の概要

| 段階 | コミット |
|---|---|
| WP-01 | `cb1df4e`、`e78e23e` |
| WP-02〜WP-07、WP-11 | `a3dc6d5` 〜 `6d1dfce` |
| WP-09 | `9205aa5` 〜 `bb77074` |
| WP-08 | `677201d` 〜 `2a6656f` |
| WP-14 | `47c4643`、`9f1c41f` |
| WP-15 | `f3647f3`、`7ec1e7b`、`25ca4a3` |
| WP-10 | `88ab790`、`b23b0ac` |
| WP-12 | `2f4f9c1`、`b441b03`、`01cc522` |
| WP-13 | `7846a19`、この報告書のコミット |

主な変更のファイル：`src/agent_core/`（types、ports、identity、runtime、tool、safety、cost、context、observability、adapters）、`src/agent_core_cli/`、`tests/unit/`（52 ファイル）、`tests/support/`、`tools/secret_scan.py`、`.github/workflows/ci.yml`、`docs/design/phase-1/`（14 文書）、`docs/adr/0008-dir-fd-path-resolution.md`、`docs/STATUS.md`、`README.md`。

## 13. 独立品質レビューの指摘と修正（2026-11-27）

| 指摘 | 重要度 | 内容 | 修正 | コミット |
|---|---|---|---|---|
| QR-01 | HIGH | 旧来の費用だけの照合で結果不明の Attempt の費用を確定すると、その後に結果を確定できなくなる | 結果が確定していない Attempt の費用だけの照合を拒否（`OUTCOME_NOT_SETTLED`）。`ReconcileOutcome` で下限を検査。以前の版の状態は報告し、直さない（`COST_SETTLED_APART`） | `dc2da98` |
| QR-02 | MEDIUM | 開くときに tenant を照合していない | Journal の tenant を正本とし、投影と親子を通常と読み取り専用の open で照合。矛盾した Journal を修復しない | `d88165d` |
| QR-03 | MEDIUM | 追加した正本の表の整合を検査していない | 区分の明文化。存在、孤立、時刻の境界、状態、結果の digest の形式と開始と完了の証拠を検査。読み取りも拒否する。推測で修復しない | `d88165d` |
| QR-04 | MEDIUM | 最後の書き込みを決定の登録の順（`decisions.rowid`）で決めていた | Journal の開始と終了の位置で順序を証明。検証の後の書き込み、読み取りとの重なり、最後の書き込みを証明できない重なり、結果不明を経た書き込み、実行中の書き込みでは完了しない（前の書き込みどうしの重なりは、最後が証明できれば拒否しない。14章）。ループの事前の確認も同じ規則 | `e3b75bb` |

- 追加した試験：47 件（QR-01 10、QR-02・QR-03 23、QR-04 14）。既存の試験の期待値を正しい契約に変えたもの：2件（QR-01）。
- 修正の前の失敗：QR-01 4件、QR-02・QR-03 18件、QR-04 12件（ループの2件は振る舞いの赤、規則の10件は関数がなく import で失敗）。残りは特性確認（修正の前から成功）。
- 記録：`WP-12_RECOVERY.md` の15章（QR-01〜QR-03、FINAL-R05 の Recovery API の契約、技術的負債）、`WP-15_AGENT_LOOP.md` の18章（QR-04）、`WP-13_INTEGRATION.md` の7・8章。
- 依存関係、Journal の版（8）、オーナーの承認の記録は変えていない。

## 14. 一般ユーザーでの試験と CI の準備（2026-11-27）

ChatGPT による QR-01〜QR-04 の主要なコードのレビューは条件付き合格。その後、一般ユーザーで失敗した8件と CI の準備に限って修正した（コミット `de79193`）。

- **8件の分類：** WP-08 の5件は試験の準備が特権（`CAP_MKNOD`、`CAP_CHOWN`）に依存。秘密情報の検査の3件は実行環境（検査の対象の作業ツリーの Git の管理情報が root の所有）。製品の問題はなかった。
- **特権の試験：** `privileged(reason)` の印で区別した。root でなければ `PRIVILEGED_NOT_RUN` で失敗し、省略も成功の計上もしない。一般ユーザーは `-m "not privileged"`、root は `-m privileged`。期待値は変えていない。一般ユーザーでも所有者の比較を確かめる試験を1件加えた。
- **秘密情報の検査：** `nobody` が所有する clone で、3つの方式とも成功した。Git の所有者の検査は無効にしていない。
- **QR-04：** 前の書き込みどうしの重なりは拒否せず、最後の書き込みを証明できないときだけ拒否する、という関数の範囲に、文書の記述を合わせた。回帰試験を2件加えた（特性確認）。コードは変えていない。
- **CI（実行していない）：** 環境と対象の SHA の記録、一般ユーザーの試験と root の特権の試験を分けた手順を加えた。`workflow_dispatch` は既定のブランチに定義がないと起動できないと理解しており（未確認）、今の空の `main` では起動できない可能性がある。Action の SHA、無料の枠、runner の `sudo` も未確認。必要な確認と承認の一覧は `WP-13_INTEGRATION.md` の5章。
- **結果：** 試験は 2134 件。root：8種類の検査、連続2回、ネットワークなしで全件成功。`nobody`：`-m "not privileged"` の 2129 件が、通常のネットワークとネットワークなしの両方で成功。root：`-m privileged` の5件が成功。
- **独立再レビューの対象：** `tests/conftest.py` の `pytest_runtest_setup`、`tests/support/privileged.py`、印を付けた5件、所有者の比較の追加の試験、`ci.yml` の新しい手順、QR-04 の説明の訂正と2件の試験。

## 15. 独立品質再レビューと最終受入の準備（2026-11-27）

- Codex の独立品質再レビュー（対象 `20fa058`）：**CONDITIONAL_PASS**。QR-01〜QR-04 の主要な指摘は解消、新しい HIGH・MEDIUM 以上の指摘なし。残った LOW の RR-01 を補強した（`a54d52b`。製品のコードは変えていない）。Codex の側で成功した検査と実行できなかった検査の詳細は、まだ受け取っていない。包括的なセキュリティ監査は行われていない。
- CI（実行していない）：公式の資料を読み、Action を v6 の完全な SHA に固定し、uv をハッシュ検査付きで導入するようにした（`89f8584`）。Node 20 は runner で使えなくなっていたため、v4・v5 から v6 に上げた。
- 試験は 2138 件。root の全試験（連続2回、ネットワークなし）、`nobody` の通常の試験 2133 件（ネットワークあり・なし）、root の特権の試験5件が成功。
- 3つの設計差の承認案、残っている受入の条件の区分、正式な完了の条件の一覧、Codex で依存環境を再現する手順は `docs/reviews/PHASE_1_ACCEPTANCE.md`。いずれもオーナーの判断を待つ。
