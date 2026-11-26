# WP-13 最終統合試験（Phase 1）

> 文書の状態：**DRAFT**（ChatGPT のレビュー待ち。Codex の独立監査、CI、オーナーの最終承認の前）　最終更新：2026-11-26
>
> - 起点：`b441b03`（WP-12）。作業の前に、リモートとの一致、作業ツリーが空であること、`main`（`aa089ae`）、設計のブランチ（`e6b70c0`）、2038 件の試験と8種類の検査の成功を確かめた。
> - 段階1：`01cc522`（WP-12・WP-15 の限定修正。記録は `WP-12_RECOVERY.md` の14章、`WP-15_AGENT_LOOP.md` の17章）。
> - 段階2：`7846a19`（最終統合のシナリオ）。
> - 段階3：この文書のコミット（最終の報告書と監査の準備）。
> - **Phase 1 は「実装提出済み・最終監査待ち」。** 独立監査、CI の実行、必要な修正、オーナーの最終承認の前には、正式な完了としない。Phase 2 には進まない。

## 1. 範囲と方法

- Phase 1 の全機能を、実際の構成（CLI、Journal と台帳、Runtime、実行のゲート、Context Engine、エージェントのループ、ファイルのブローカー）で組み合わせて確かめた。
- 試験の数を増やすことは目的にしない。各シナリオで、既存の試験がどこまで保証し、新しい試験が何を加えたかを明記する。
- **段階2の新しい試験（`tests/unit/test_wp13_integration.py` の11件）は、すべて最初の実行で成功した。特性確認の試験であり、赤を確かめたとは主張しない。** 製品のコードの不具合は、段階2では見つからなかった（子プロセスの停止の地点を3つ加えた試験の補助だけを変えた）。
- 段階1（限定修正）の試験は先に書き、振る舞いの赤を確かめた（`WP-12_RECOVERY.md` の14章）。

## 2. 最終統合のシナリオの結果

| シナリオ | 新しい試験（`test_wp13_integration.py`） | 既存の試験 | 結果 |
|---|---|---|---|
| I1 通常の作業 | `test_i1_normal_work_through_the_cli`（子プロセスの CLI。成果物、Journal の Run の全遷移、費用 320、回数、未確定の予約なし、Step の種類） | `test_wp10_cli*.py`、`test_wp15_scenarios.py` S1 | PASS |
| I2 修正のループ | `test_i2_repair_and_limits`（新しい承認、修正1回、費用 640、修正の上限、呼び出しの回数） | `test_wp15_scenarios.py` S2〜S4、`test_wp15_fixes.py` R04 | PASS |
| I3 権限の管理 | `test_i3_a_policy_change_*`、`test_i3_a_revocation_*` | 未承認・改変された引数・対象外のファイル・古い承認・古い Permit と Receipt・偽の証拠・停止：`test_wp15_scenarios.py` S5、`test_wp09_*.py`、`test_permits.py`、`test_wp12_recovery.py`、`test_wp12_fixes.py` R02・R07、`test_wp15_r06.py` | PASS |
| I4 費用の管理 | `test_i4_costs_and_counts_are_kept_by_a_new_runtime` | 予算の境界、Task・Run・Step の上限、回数、作業単位、超過、未確定、訂正と照合：`test_budget_ledger.py`、`test_cost_rules.py`、`test_wp09_*.py`、`test_wp15_scenarios.py` S9、`test_wp12_*.py` | PASS |
| I5 異常終了と復旧 | SIGKILL：予約の後、ファイルの rename による確定の直後（Journal の保存の前）、ProjectionRepair の最中 | SIGKILL：承認待ち、Permit の消費の後、実行の開始の直後、一時ファイルへの書き込み中、結果の取得の後・Journal の保存の前、照合（`recover`）の最中（`test_wp12_cli_and_faults.py`、`test_wp10_cli_process.py`）。例外の注入：`test_wp12_recovery.py`、`test_wp12_fixes.py` | PASS |
| I6 Rebuild と Replay | `test_i6_the_journal_rebuilds_the_same_state_without_writing` | 不正なイベント（順序、版、連鎖、種類）：`test_wp12_recovery.py`。Replay の3つのハッシュ、改変、欠損、順序、今のアクセスの拒否、記録元の費用、実ファイルの操作がないこと：`test_replay_provider.py`、`test_wp14_context.py` | PASS（Replay は既存の試験の範囲） |
| I7 Context の安全性 | なし | 信頼の階層、ProtectedFact、予算の不足でモデルを呼ばない、偽の system の命令、改変された ModelRequest：`test_wp14_context.py`、`test_wp14_fixes.py`、`test_wp15_scenarios.py` S5・S7、`test_wp15_loop.py` | PASS（既存の試験の範囲） |
| I8 情報の保護 | `test_i8_a_secret_marker_reaches_no_output`（合成の機密の印がモデルの文脈に入ったことを確かめた上で、CLI の出力、エラー、承認画面、監査ログ、Git の追跡ファイルに出ないこと） | 鍵が出力されない：`test_wp10_cli*.py`。ログ：`test_observability.py` | PASS（合成のデータの範囲。実データの完全な保護の証明ではない） |
| I9 ファイルの操作 | `test_i9_a_changed_work_tree_is_refused_before_any_write` | 読み取り、CREATE、REPLACE、リンク、特殊なファイル、権限、上限、実行券の再使用、部分書き込み、不明な一時ファイル：`test_wp08_*.py`、`test_wp12_recovery.py` | PASS（ADR-0008 の限定された範囲） |
| I10 終了と再起動 | なし | 正常終了、EOF、SIGINT、SIGKILL、二重起動、状態確認で epoch が変わらない、古いセッションを再開しない、停止の維持：`test_wp10_cli*.py`、`test_wp10_fixes.py`、`test_wp12_*.py` | PASS（既存の試験の範囲） |

**WP-12 で未検証だった、ファイルの確定（rename）の後・Journal の保存の前の異常終了**は、`test_i5_killed_right_after_the_file_was_committed` で扱った。再起動の後、結果不明（予約は UNKNOWN）を保ち、期待どおりの内容のファイルがあっても証拠にせず（`reconcile` は `STILL_UNKNOWN`）、同じ操作を再実行しない（2回目の `recover` は変化なし）。

## 3. Phase 1 の不変条件の対応表

判定：PASS（試験で確かめ、成功）、PARTIAL（一部だけ確かめた）、NOT_TESTED（Phase 1 に当たるが試験で確かめていない）、DEFERRED（後の Phase の範囲で、Phase 1 では実装しない）。実装があるだけで PASS にしていない。「阻害」は、その項目が Phase 1 の完了を妨げうるか（要：オーナーの判断）。

### 3.1 A（Runtime）

| ID | 内容（要約） | 実装 | 試験 | 判定 | 保証範囲・未検証 | 阻害 |
|---|---|---|---|---|---|---|
| A.I1 | Journal は追記だけ、連番と連鎖 | `adapters/sqlite_journal.py` | `test_sqlite_journal.py`、`test_journal_processes.py` | PASS | DB を持つ者の一貫した書き換えは検出できない | — |
| A.I2 | Intent のない副作用は発行しない | 台帳のペアリング、`runtime/pipeline.py` | `test_wp09_*.py`、`test_permits.py` | PASS | 合成のツールとファイルの操作 | — |
| A.I3 | 有効な epoch の1つの主体だけが書く | OS のロック、owner_epoch | `test_journal_processes.py`、`test_wp10_cli_process.py` | PASS | 1つのカーネルの中（ネットワークのファイルシステムは対象外） | — |
| A.I4 | 取り消せない Step を結果不明の後に自動で再実行しない | ループ、復旧 | `test_wp15_scenarios.py` S6、`test_wp12_*.py`、I5 | PASS | — | — |
| A.I5 | 確定額＋予約＋未確定 ≤ 上限 | 台帳 | `test_budget_ledger.py`、`test_wp09_*.py` | PASS | 合成の XTU | — |
| A.I6 | Snapshot と状態のハッシュの一致 | Rebuild の決定性だけ | `test_runtime_service.py`、I6 | PARTIAL | Snapshot は未実装 | 要判断 |
| A.I7 | 終了状態から遷移しない | 状態機械 | `test_runtime_state_machine.py` | PASS | — | — |
| A.I8 | Safety の判定のない Step は出さない。policy_version の変化で再判定 | 台帳の PDP の再実行 | `test_wp09_*.py`、I3 | PASS | — | — |
| A.I9 | 復旧は冪等 | `runtime/recovery.py` | `test_wp12_recovery.py`、`test_wp12_fixes.py`、I5 | PASS | 明示的な復旧の操作として | — |
| A.I10 | Phase 3B まで LiveProvider に届く経路がない | import の契約 | `test_import_contracts.py` | PASS | 静的な検査と実行時の遮断の範囲 | — |
| A.I11 | Step のイベントに版の項目がそろう | イベントの型 | `test_event_versions.py` | PARTIAL | 版の項目の全体を WP-13 で個別に照合していない | — |
| A.I12 | 承認待ちは再起動で失われず、期限切れで自動承認しない | 承認の記録、照合 | `test_wp10_cli*.py`、`test_wp12_recovery.py` | PARTIAL | 自動承認はしない（確認済み）。ただし Phase 1 では会話の記録がプロセスの中だけで、承認待ちの Run は再起動の後に再開できず、復旧で中止される | **要判断** |
| A.I13 | 有効な Permit のない外部操作はない | 実行のゲート | `test_permits.py`、`test_wp09_execution.py` | PASS | 外部操作は Phase 1 にない（合成） | — |
| A.I14 | Rebuild に副作用の接続口がない | `rebuild_with_head` | `test_runtime_service.py`、I6 | PASS | — | — |
| A.I15 | 承認時と実行時の引数のハッシュが一致 | action_hash の結び付け | `test_wp09_binding.py`、`test_wp08_write.py` | PASS | — | — |
| A.I16 | 結果不明があれば COMPLETED・CANCELLED にしない | 状態機械、台帳 | `test_wp12_fixes.py` R03、`test_wp15_r06.py` | PASS | — | — |
| A.I17 | CANCELLING と RECOVERING の期限 | `recovery.py` | `test_wp12_recovery.py`、`test_wp12_fixes.py` R04 | PARTIAL | 明示的な復旧の時だけ検査（常時の監視は Phase 4）。期限の値は未承認 | 要判断 |
| A.I18 | 書き込むプロセスは1つ、異常終了でロックは残らない | `runtime_lock.py` | `test_journal_processes.py`、`test_wp10_cli_process.py` | PASS | — | — |

### 3.2 C1（Safety）

| ID | 内容（要約） | 判定 | 試験・備考 |
|---|---|---|---|
| C1.S1 | 既定で拒否 | PASS | `test_safety.py` |
| C1.S2 | モデルは権限を持たない | PASS | `test_wp15_scenarios.py` S5（偽の system の命令）、`test_wp14_context.py` |
| C1.S3 | 副作用は PEP を通る | PASS | `test_wp09_execution.py`、`test_wp08_*.py`（execute_file だけ） |
| C1.S4 | action_hash に結び付く、一度だけ、期限付きの Permit | PASS | `test_permits.py` |
| C1.S5 | PEP の直前の検査 | PASS | `test_wp09_execution.py` |
| C1.S6 | 承認は action_hash に結び付く | PASS | `test_wp10_cli.py`、`test_wp12_recovery.py` |
| C1.S7 | 権限は下るほど狭い | PASS | Run の許可とポリシーの積（`test_wp15_scenarios.py` S5） |
| C1.S8 | 未登録・版の固定のないツールの拒否 | PASS | `test_tool_registry.py`、S5 |
| C1.S9、S10 | Sandbox | DEFERRED | Phase 1 は合成のツールだけ（OS の隔離は Phase 3A） |
| C1.S11 | 認証情報を文脈・Journal・ログに入れない | PARTIAL | 認証情報は使わない。鍵の非表示と合成の印（I8）だけ |
| C1.S12 | 外部通信は登録先だけ | DEFERRED | 外部通信なし（import の契約、試験中の遮断、I10 のネットワークのない実行） |
| C1.S13 | 外部の内容でポリシーを変えられない | PASS | `test_wp14_context.py` |
| C1.S14 | 停止の後、新しい Permit を出さない | PASS | `test_permits.py`、`test_wp15_fixes.py` R02 |
| C1.S15 | 再開はオーナーの操作 | PARTIAL | `ResumeScope`（`test_wp10_cli.py`）。11.3 の確認の全体は未照合 |
| C1.S16 | ファイルはワークスペースのルートの内側 | PASS | `test_wp08_*.py`（ADR-0008 の範囲） |
| C1.S17 | ポリシーの変更はオーナーの認証された経路 | PASS | `test_safety.py`、I3 |
| C1.S18、S19、S21、S24、S25 | Sandbox へのコピー・反映、Gateway、プロセスの分離、MCP | DEFERRED | Phase 3A 以降 |
| C1.S20 | Permit の消費の検査 | PASS | `test_permits.py` |
| C1.S22 | 停止の状態のファイルを先に書く | DEFERRED | Phase 1 は台帳の中の停止の記録（別ファイルではない） |
| C1.S23 | manifest_hash・artifact_hash の変化で再審査 | PASS | `test_tool_registry.py`、`test_wp08_files.py` |
| C1.S26 | 資源の上限の OS による強制 | DEFERRED | OS の隔離の課題 |
| C1.S27 | 取り消せない操作は ExactApproval | PASS | `test_wp10_cli.py` |

### 3.3 B1（Context）

| ID | 判定 | 試験・備考 |
|---|---|---|
| B1.C1〜C4、C6、C7、C16、C17、C19 | PASS | `test_wp14_context.py`、`test_wp14_fixes.py`、`test_wp15_loop.py` |
| B1.C8、C9 | PASS | import の契約、`test_runtime_service.py` |
| B1.C10 | PASS | `test_replay_provider.py` |
| B1.C14 | PARTIAL | 認証情報は使わない。機密の項目のハッシュ化（鍵付き）は未実装 |
| B1.C18 | PARTIAL | ProtectedFact の解決は無効（WP14-R02） |
| B1.C20 | PARTIAL | 組み立て直しの再試行はない。ループの上限だけ |
| B1.C23 | PASS | モデルへの送信は許可された経路だけ |
| B1.C5、C11、C12、C13、C15、C21、C22 | DEFERRED | 要約、キャッシュ、記憶、取得の機能は Phase 1 にない |

### 3.4 C2（データ）と D（費用・評価）

| ID | 判定 | 試験・備考 |
|---|---|---|
| C2.X4、X5 | PARTIAL | I8（合成の印は監査ログと出力に出ない）。Journal の記録の全体は未照合 |
| C2.X13 | PASS | 独自の暗号はない（標準ライブラリの HMAC と SHA-256） |
| C2.X18 | PARTIAL | 監査ログのハッシュの連鎖（`verify_consistency`）。署名付きのチェックポイントは未実装 |
| C2.X19、X21 | PARTIAL | holdout に接続していない（運用の規則）。コードの試験はない |
| C2.X12 | DEFERRED | 鍵付きのハッシュ（残っている課題） |
| C2 のその他 | DEFERRED | Gateway、削除、復元、評価の環境 |
| D.V10、V11、V13、V16、V17 | PASS | `test_budget_ledger.py`、`test_cost_rules.py` |
| D.V12 | PASS | 実 API の経路がない（import の契約） |
| D.V14 | PARTIAL | 価格表は `status: test` だけ |
| D.V19 | NOT_TESTED | 安全の違反と通常の拒否の分けた記録を、個別に試していない |
| D.V1〜V9、V15、V18、V20、V21 | DEFERRED | 評価（Phase 2 以降） |

### 3.5 E（統合）

| ID | 判定 | 試験・備考 |
|---|---|---|
| E.Z2、Z3 | PASS | `test_identity_generator.py`、`test_sqlite_journal.py` |
| E.Z6 | PASS | ゲートの未実行の報告、Receipt の状態による未実行の証明（`test_wp12_fixes.py`） |
| E.Z7、Z8、Z9、Z37、Z42、Z43 | PASS | `test_budget_ledger.py`、`test_wp09_*.py` |
| E.Z11 | PASS | Task の上限は Run をまたいで残る（`test_wp12_recovery.py`、I4） |
| E.Z13、Z15 | PASS | 検証が完了を決め、モデルの主張や証拠の作成だけでは完了しない（`test_wp15_r06.py`、`test_wp12_fixes.py` R07） |
| E.Z14 | PASS | `test_replay_provider.py` |
| E.Z17 | PARTIAL | Journal と台帳は同じ DB の1つのトランザクション。ファイルと DB の間は原子的でない（ADR-0008、I5 の rename の後の停止で確かめた範囲） |
| E.Z32 | PASS | 結果不明の読み取りも未実行とみなさない（`test_wp12_*.py`） |
| E.Z33 | PARTIAL | 管理された木の中の検証だけ（外部の作業領域への反映はない） |
| E.Z38、Z39 | PASS | 外部操作と通信の経路がない（import の契約） |
| E.Z4、Z12、Z24 | PARTIAL | プロセスの起動と通信はない。上限の値はオーナーの決定を待つ |
| E.Z10、Z16、Z18〜Z23、Z25〜Z31、Z34〜Z36、Z40、Z41、Z44〜Z46 | DEFERRED | 実 API、削除、暗号、復元、外部の作業領域、評価の各 Phase |
| E.Z1、Z5 | NOT_TESTED | この報告では個別に追跡していない（P1 の呼び名、送信の Lease） |

### 3.6 Phase 1 の完了を妨げうる事項と、後に残せる事項

- **妨げうる（オーナーと監査の判断が必要）：**
  1. CI が未実行（WP-01。6章）。
  2. A.I12：承認待ちの Run は、再起動の後に再開できない（会話の記録の永続化は Phase 4 の予定）。設計の文言との差。
  3. A.I17：期限の検査は明示的な復旧の時だけ。期限の運用の値は未承認。
  4. A.I6：Snapshot は未実装（Rebuild の決定性だけ）。
  5. Codex の独立監査（AUDIT-02〜04 を含む）が済んでいない。
- **後に残せる（DEFERRED、各 Phase の範囲）：** Sandbox と OS の隔離（3A）、Gateway と外部通信、実 API（3B 以降）、評価（2）、記憶と要約、削除と復元（4）、鍵付きのハッシュ（実データの前）、外部の作業領域への反映。

## 4. 品質検査と追加の検証

| 検査 | 結果 |
|---|---|
| 8種類（秘密情報、`uv lock --check`、`uv sync --locked --offline --check`、ruff check、ruff format、mypy strict、import-linter、pytest） | すべて成功（2079 件） |
| 全試験の連続2回の実行 | 2回とも 2079 件が成功 |
| クリーンな別の作業ツリー（`git worktree`、HEAD `7846a19`） | ruff、ruff format、mypy、import-linter、pytest（2079 件）が成功。`uv sync --locked --offline --check` は、まだ環境のない新しい作業ツリーでは「環境が古い」と報告した（初回の同期の前としては想定どおり。pytest の実行で lock から環境が作られた） |
| 一般ユーザーの権限 | `test_wp08_nonroot.py` が `nobody` の権限で読み書きと拒否を確かめる。ただし最初の別の作業ツリーを、`nobody` が辿れない scratchpad の下に作ったときは、この試験が `PermissionError` で失敗した（環境の配置の問題。辿れる場所での再実行で成功）。CLI の統合試験の全体を一般ユーザーで実行することはしていない（未検証） |
| ネットワークのない環境 | `unshare -n`（ループバックだけの名前空間。外への接続は "Network is unreachable"）で全試験の 2079 件が成功 |

障害試験の内訳：例外の注入（同じプロセス）は `test_wp12_recovery.py`、`test_wp12_fixes.py`、`test_wp12_cli_and_faults.py` の COMMIT の結果不明。実際のプロセスの停止（SIGKILL、SIGINT）は `test_wp10_cli_process.py`、`test_wp12_cli_and_faults.py`、`test_wp13_integration.py`、`test_journal_processes.py`、`test_permit_processes.py`、WP-08 の子プロセスの試験。

## 5. WP-01 の CI の残っている課題

- **今の定義（`.github/workflows/ci.yml`）：** 手動の実行（`workflow_dispatch`）だけ。権限は `contents: read`。`ubuntu-24.04`、`timeout-minutes: 15`、uv 0.11.32、Python 3.12。手順は手元の8種類と同じ順序（秘密情報、`uv lock --check`、`uv sync --locked`、`--offline --check`、ruff、ruff format、mypy、import-linter、pytest）。
- **依存する Action：** `actions/checkout@v4`、`actions/setup-python@v5`（タグで参照）。完全なコミット SHA への固定の準備として、固定すべき2つを特定した。**正式なリリースと SHA の対応は、外部の照会をしていないため確かめていない。推測で書かない（未確認）。**
- **無料の利用枠：** 確認に必要な情報は、リポジトリの公開・非公開の別、アカウントの種類（Free・Pro など）、月の Linux の分数の残り、ストレージ、`ubuntu-24.04` の分数の倍率、支払いの上限（spending limit）の設定。**いずれも未確認**（外部の照会をしていない）。
- **手動の実行で実装ブランチを検証できるか：** `workflow_dispatch` は、ワークフローの定義がそのブランチにあれば、そのブランチを選んで実行できる（GitHub の仕様の一般的な理解。この環境では実行していないので、実際の動作は未確認）。
- **CI と手元の差：** CI は `uv sync --locked` で依存をネットワークから入れる（手元は `--offline` でキャッシュから）。CI は root でない利用者で動く（`test_wp08_nonroot.py` は root でないときの分岐を持つ）。手元は Linux のコンテナ（uid 0）。Python は CI が `setup-python`、手元はシステムの 3.12。
- **ネットワークの制限：** CI では、依存の導入の後のすべての手順が `uv run --locked --offline` で、試験の中では Python の `socket` を遮断する。OS のレベルの遮断（手元の `unshare -n` のようなもの）は CI では行っていない。
- **今回、GitHub Actions は実行していない。CI が未実行のまま、Phase 1 の正式な完了は宣言しない。**

## 6. 未検証の事項

- CLI の統合試験の全体を一般ユーザーで実行すること。
- Windows、macOS、ネットワークのファイルシステム。
- 協調しない書き込み元（ADR-0008）、DB を持つ者の一貫した書き換え。
- 実行報告の永続化（プロセスの中だけ）。実際のプロバイダーの照会。
- E.Z1、Z5、D.V19、A.I11 の個別の照合。
- GitHub Actions の SHA、無料の利用枠。
