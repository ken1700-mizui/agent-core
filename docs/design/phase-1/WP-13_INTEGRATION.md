# WP-13 最終統合試験（Phase 1）

> 文書の状態：**DRAFT**（独立品質レビューの指摘の修正を提出、再レビュー待ち。CI、オーナーの最終承認の前）　最終更新：2026-11-27
>
> - 起点：`b441b03`（WP-12）。作業の前に、リモートとの一致、作業ツリーが空であること、`main`（`aa089ae`）、設計のブランチ（`e6b70c0`）、2038 件の試験と8種類の検査の成功を確かめた。
> - 段階1：`01cc522`（WP-12・WP-15 の限定修正。記録は `WP-12_RECOVERY.md` の14章、`WP-15_AGENT_LOOP.md` の17章）。
> - 段階2：`7846a19`（最終統合のシナリオ）。
> - 段階3：この文書のコミット（最終の報告書と監査の準備）。
> - 独立品質レビューの指摘の修正（7章）：QR-01 `dc2da98`、QR-02・QR-03 `d88165d`、QR-04 `e3b75bb`、記録の更新はこの版のコミット。
> - **Phase 1 は「独立品質レビュー指摘の修正提出済み・再レビュー待ち」。** 再レビュー、CI の実行、必要な修正、オーナーの最終承認の前には、正式な完了としない。Phase 2 には進まない。
> - Codex の独立品質レビュー（コードの品質と正しさ）は、包括的なセキュリティ監査ではない。セキュリティ監査は、まだ行われていない。

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
| A.I6 | Snapshot と状態のハッシュの一致 | Rebuild の決定性だけ | `test_runtime_service.py`、I6 | PARTIAL | Snapshot は未実装（延期の候補。8章） | 要判断 |
| A.I7 | 終了状態から遷移しない | 状態機械 | `test_runtime_state_machine.py` | PASS | — | — |
| A.I8 | Safety の判定のない Step は出さない。policy_version の変化で再判定 | 台帳の PDP の再実行 | `test_wp09_*.py`、I3 | PASS | — | — |
| A.I9 | 復旧は冪等 | `runtime/recovery.py` | `test_wp12_recovery.py`、`test_wp12_fixes.py`、`test_qr01_reconcile.py`、I5 | PASS | 明示的な復旧の操作として。照合の再実行で費用を二重に計上しない（QR-01） | — |
| A.I10 | Phase 3B まで LiveProvider に届く経路がない | import の契約 | `test_import_contracts.py` | PASS | 静的な検査と実行時の遮断の範囲 | — |
| A.I11 | Step のイベントに版の項目がそろう（A の2章：`run_id`、`seq`、`prev_hash`、`schema_version`、`epoch`、`runtime_version`、`provider_kind`、`model_id`、`context_hash`、`tool_id@version`、`policy_version`） | イベントの型（`_EventFields`）、`ActionDescriptor` | `test_event_versions.py`、`test_event_hash.py` | PARTIAL | **保証する：** すべてのイベント（Step のものを含む）が型で `run_id`・`run_seq`、ハッシュの連鎖、`schema_version`、`engine`（Runtime の版）、`policy_version`、`owner_epoch` を持つ。**保証しない：** Runtime のイベントの `access_epoch` と `revocation_gen` は固定の 0（実際の値は台帳が持つ）。`provider_kind`、`model_id`、`context_hash`、`tool_id@version` は Step のイベントではなく、台帳に記録した `ActionDescriptor`（action_hash に結び付く）にある。すべての Step のイベントで全項目を照合する試験はない | — |
| A.I12 | 承認待ちは再起動で失われず、期限切れで自動承認しない | 承認の記録、照合 | `test_wp10_cli*.py`、`test_wp12_recovery.py` | PARTIAL | 自動承認はしない（確認済み）。ただし Phase 1 では会話の記録がプロセスの中だけで、承認待ちの Run は再起動の後に再開できず、復旧で中止される（延期の候補。8章） | **要判断** |
| A.I13 | 有効な Permit のない外部操作はない | 実行のゲート | `test_permits.py`、`test_wp09_execution.py` | PASS | 外部操作は Phase 1 にない（合成） | — |
| A.I14 | Rebuild に副作用の接続口がない | `rebuild_with_head` | `test_runtime_service.py`、I6 | PASS | — | — |
| A.I15 | 承認時と実行時の引数のハッシュが一致 | action_hash の結び付け | `test_wp09_binding.py`、`test_wp08_write.py` | PASS | — | — |
| A.I16 | 結果不明があれば COMPLETED・CANCELLED にしない | 状態機械、台帳 | `test_wp12_fixes.py` R03、`test_wp15_r06.py`、`test_qr01_reconcile.py`、`test_qr04_ordering.py` | PASS | 結果不明の費用を単独で確定しない（QR-01）。結果不明を経た書き込みでは完了しない（QR-04） | — |
| A.I17 | CANCELLING と RECOVERING の期限 | `recovery.py` | `test_wp12_recovery.py`、`test_wp12_fixes.py` R04、`test_qr02_qr03_consistency.py` | PARTIAL | 明示的な復旧の時だけ検査。期限の値は未承認。状態に入った時刻の記録は開くときと読むときに照合する（QR-03）（延期の候補。8章） | 要判断 |
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
| C1.S9、S10 | Sandbox | DEFERRED | Phase 1 は合成のツールだけ。**Phase 2 で、信頼しないコードを実行する前に**実装と試験が要る（ROADMAP の Phase 2・0.2）。OS のレベルの隔離の強化は Phase 3A |
| C1.S11 | 認証情報を文脈・Journal・ログに入れない | PARTIAL | 認証情報は使わない。鍵の非表示と合成の印（I8）だけ |
| C1.S12 | 外部通信は登録先だけ | DEFERRED | 外部通信なし（import の契約、試験中の遮断、I10 のネットワークのない実行） |
| C1.S13 | 外部の内容でポリシーを変えられない | PASS | `test_wp14_context.py` |
| C1.S14 | 停止の後、新しい Permit を出さない | PASS | `test_permits.py`、`test_wp15_fixes.py` R02 |
| C1.S15 | 再開はオーナーの操作 | PARTIAL | `ResumeScope`（`test_wp10_cli.py`）。11.3 の確認の全体は未照合 |
| C1.S16 | ファイルはワークスペースのルートの内側 | PASS | `test_wp08_*.py`（ADR-0008 の範囲） |
| C1.S17 | ポリシーの変更はオーナーの認証された経路 | PASS | `test_safety.py`、I3 |
| C1.S19 | ホストへの反映は検査を通った普通のファイルだけ、全体として原子的、安全なファイル操作の手段がなければ拒否 | PARTIAL | **Phase 1 の File Broker に当たる部分：** 安全なパスの解決（ADR-0008 の `dir_fd`）ができなければ開く前に拒否する、普通のファイルだけ（リンク、特殊なファイルを拒否）、1つのファイルの CREATE・REPLACE を一時ファイルと `link`・`rename` で行う（`test_wp08_*.py`）。**当たらない部分（DEFERRED）：** コンテナの停止の後の検査と、複数のファイルの全体としての原子的な反映（Sandbox は Phase 3A） |
| C1.S18、S21、S24、S25 | Sandbox へのコピー、Gateway、プロセスの分離、MCP | DEFERRED | S18（Sandbox へのコピー）は Phase 2（信頼しないコードの実行の前）。S21（Gateway）は Phase 3A。S24 は Docker の API の分離が Phase 2、認証情報の P3 の分離が Phase 3A。S25（MCP）は Phase 4 で必要なら |
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
| D.V19 | NOT_TESTED | 設計：安全の違反と通常の認可の拒否を分けて記録し、タスクの結果と安全の結果を別の項目にする。今あるもの：拒否の理由の符号（`PermitReason` など）、監査の判定（ALLOW・DENY）、ループの結果（状態と `TerminationReason`、`ABORTED_SAFETY`）。**未検証：** 「安全の違反」の区分が通常の拒否と別に記録されること、タスクの結果と安全の結果が別の項目であることを確かめる試験はない。評価（Phase 2）の前に、記録の形式と試験が要る |
| D.V1〜V9、V15、V18、V20、V21 | DEFERRED | 評価（Phase 2 以降） |

### 3.5 E（統合）

| ID | 判定 | 試験・備考 |
|---|---|---|
| E.Z2、Z3 | PASS | `test_identity_generator.py`、`test_sqlite_journal.py` |
| E.Z6 | PASS | ゲートの未実行の報告、Receipt の状態による未実行の証明（`test_wp12_fixes.py`） |
| E.Z7、Z8、Z9、Z37、Z42、Z43 | PASS | `test_budget_ledger.py`、`test_wp09_*.py` |
| E.Z11 | PASS | Task の上限は Run をまたいで残る（`test_wp12_recovery.py`、I4） |
| E.Z13、Z15 | PASS | 検証が完了を決め、モデルの主張や証拠の作成だけでは完了しない（`test_wp15_r06.py`、`test_wp12_fixes.py` R07）。書き込みと検証の読み取りの順序は Journal で証明し、証明できなければ完了しない（QR-04、`test_qr04_ordering.py`。同じ Run の書き込みの範囲） |
| E.Z14 | PASS | `test_replay_provider.py` |
| E.Z17 | PARTIAL | Journal と台帳は同じ DB の1つのトランザクション。ファイルと DB の間は原子的でない（ADR-0008、I5 の rename の後の停止で確かめた範囲） |
| E.Z32 | PASS | 結果不明の読み取りも未実行とみなさない（`test_wp12_*.py`） |
| E.Z33 | PARTIAL | 管理された木の中の検証だけ（外部の作業領域への反映はない） |
| E.Z38、Z39 | PASS | 外部操作と通信の経路がない（import の契約） |
| E.Z4、Z12、Z24 | PARTIAL | プロセスの起動と通信はない。上限の値はオーナーの決定を待つ |
| E.Z10、Z16、Z18〜Z23、Z25〜Z31、Z34〜Z36、Z40、Z41、Z44〜Z46 | DEFERRED | 実 API、削除、暗号、復元、外部の作業領域、評価の各 Phase |
| E.Z1 | NOT_TESTED（構成の照合では不一致なし） | 設計：P1 を Runtime Core と呼び、Engine の Orchestration（サブエージェント）と区別する。Phase 1 の構成：P1 は `agent_core` の Runtime と、合成の根 `agent_core_cli` を1つのプロセスで動かす。Orchestration とサブエージェントのコードはない。WP-08・WP-09 の文書の「P1」はこの意味で使っている。用語の整合は文書とコードの照合で確かめた。試験で確かめる性質ではない |
| E.Z5 | PARTIAL | 設計：ack の後、または送信の Lease が切れた後は、まだ始まっていない操作が古い `revocation_gen` で始まらない。**Phase 1 の部分（PASS）：** 同じプロセスの PEP で、失効の後の消費と開始を拒否する（`test_permits.py`、`test_wp09_execution.py` の失効、`test_wp09_restart.py` の `revocation_gen` が戻らないこと）。**後の Phase（DEFERRED）：** ack、送信の Lease、別のプロセスの PEP（Phase 3A 以降） |

### 3.6 Phase 1 の完了を妨げうる事項と、後に残せる事項

- **妨げうる（オーナーと監査の判断が必要）：**
  1. CI が未実行（WP-01。6章）。
  2. A.I12、A.I17、A.I6：設計の文言との差。それぞれ別の延期の候補として、オーナーの承認を待つ（8章。一括して Phase 4 に送らない）。
  3. 独立品質レビュー（FAIL、HIGH 1・MEDIUM 3）の指摘の修正の再レビューが済んでいない（7章）。
  4. 包括的なセキュリティ監査は行われていない。AUDIT-02〜04、A-P6 は残っている。
- **後に残せる（DEFERRED、各 Phase の範囲。ROADMAP が正本）：** Sandbox と隔離の自己診断（Phase 2、信頼しないコードの実行の前）、OS のレベルの隔離の強化（3A）、Gateway と外部通信、実 API（3B 以降）、評価（2）、記憶と要約、削除と復元（4）、鍵付きのハッシュ（実データの前）、外部の作業領域への反映。

### 3.7 追加した台帳の表の区分（QR-03）

投影（`rt_*`）、正本（`entity_times`、`run_status_times`、`operation_results`）、それぞれで確かめることと確かめられないことは、`WP-12_RECOVERY.md` の 15.3 にまとめた。

## 4. 品質検査と追加の検証

### 4.0 独立品質レビューの修正の後（HEAD `e3b75bb` の時点、2026-11-27）

| 検査 | 結果 |
|---|---|
| 8種類の検査 | すべて成功（2126 件） |
| 全試験の連続2回の実行 | 2回とも 2126 件が成功 |
| ネットワークのない環境（`unshare -n`。外への接続は "Network is unreachable"） | 2126 件が成功 |
| 一般ユーザー（`nobody`）での全試験（`setpriv`、別の作業ツリー） | 2118 件が成功、**8件が失敗**。原因はすべて試験の準備に必要な権限：WP-08 の5件は別の利用者への `chown` と文字デバイスの `mknod`（root だけができる）、秘密情報の検査の3件は Git の "dubious ownership"（作業ツリーの Git の管理情報が root の所有）。製品のコードの失敗ではない。**ただし CI は root でない利用者で動くため、WP-08 の5件は CI でも失敗しうる（WP-01 の残っている課題に加えた。試験の無効化や省略はしていない）** |

以下の 4章の表は、最初の提出（`7846a19`）の時点の結果である。

| 検査 | 結果 |
|---|---|
| 8種類（秘密情報、`uv lock --check`、`uv sync --locked --offline --check`、ruff check、ruff format、mypy strict、import-linter、pytest） | すべて成功（2079 件） |
| 全試験の連続2回の実行 | 2回とも 2079 件が成功 |
| クリーンな別の作業ツリー（`git worktree`、HEAD `7846a19`） | ruff、ruff format、mypy、import-linter、pytest（2079 件）が成功。`uv sync --locked --offline --check` は、まだ環境のない新しい作業ツリーでは「環境が古い」と報告した（初回の同期の前としては想定どおり。pytest の実行で lock から環境が作られた） |
| 一般ユーザーの権限 | `test_wp08_nonroot.py` が `nobody` の権限で読み書きと拒否を確かめる。ただし最初の別の作業ツリーを、`nobody` が辿れない scratchpad の下に作ったときは、この試験が `PermissionError` で失敗した（環境の配置の問題。辿れる場所での再実行で成功）。CLI の統合試験の全体を一般ユーザーで実行することはしていない（未検証） |
| ネットワークのない環境 | `unshare -n`（ループバックだけの名前空間。外への接続は "Network is unreachable"）で全試験の 2079 件が成功 |

障害試験の内訳：例外の注入（同じプロセス）は `test_wp12_recovery.py`、`test_wp12_fixes.py`、`test_wp12_cli_and_faults.py` の COMMIT の結果不明。実際のプロセスの停止（SIGKILL、SIGINT）は `test_wp10_cli_process.py`、`test_wp12_cli_and_faults.py`、`test_wp13_integration.py`、`test_journal_processes.py`、`test_permit_processes.py`、WP-08 の子プロセスの試験。

## 5. WP-01 の CI の残っている課題（2026-11-27 に更新）

> 公式の資料の確認（Action の SHA、`workflow_dispatch`、`sudo`、uv のハッシュ、無料の枠）の結果と出典は `docs/reviews/PHASE_1_ACCEPTANCE.md` の3章。下の表の「未確認」は、そこで確認したものを更新した。

GitHub Actions は実行していない。CI が未実行のまま、Phase 1 の正式な完了は宣言しない。

| 項目 | 今の状態 | 残っていること |
|---|---|---|
| 起動 | `workflow_dispatch` だけ。権限は `contents: read`、`persist-credentials: false`。`ubuntu-24.04`、15分 | — |
| 8種類の検査 | 手元と同じ順序：秘密情報（標準ライブラリだけ、依存の取得の前）、`uv lock --check`、`uv sync --locked`、`uv sync --locked --offline --check`、ruff check、ruff format --check、mypy、import-linter、pytest | — |
| 一般ユーザーで動く試験 | `pytest -m "not privileged"` を runner の利用者で実行（9章） | 実際の runner で通るかは未確認 |
| 特権の要る試験 | 別の手順で、root として `-m privileged`（`sudo` と `.venv` の Python）。1件も選ばれなければ pytest が 5 で終わり、手順は失敗する | 公式の資料で確認：Linux の runner はパスワードなしの sudo（実際の runner での動作は、CI を実行するまで未確認） |
| uv.lock との一致 | `uv lock --check`、`uv sync --locked`、`--offline --check`。uv 自体は `pip install uv==0.11.32`（ハッシュの固定なし） | 解決（`89f8584`）：`tools/ci/uv-requirements.txt` のハッシュで、wheel だけを導入する |
| Action の SHA の固定 | `actions/checkout@v4`、`actions/setup-python@v5` をタグで参照 | 解決（`89f8584`）：公式のリポジトリで確かめた v6 の完全な SHA に固定（Node 20 の廃止のため v6 に上げた） |
| `workflow_dispatch` と既定のブランチ | 既定のブランチは `main`（空の初期コミットだけ。ワークフローの定義がない） | 公式の資料で確認：`workflow_dispatch` はワークフローが既定のブランチにあるときだけ起動できる。以前の版の「実装ブランチを選んで実行できる」は不正確だった。今の構成では起動できない。方法の比較は受入の文書の 3.1。`main` と既定のブランチは、オーナーの承認なしに変えない |
| 無料の利用枠と課金の防止 | 公式の資料で確認：このリポジトリは公開で、標準の runner は無料。予算の「上限で利用を止める」で追加の課金を防げる | リポジトリの公開・非公開、アカウントの種類、月の Linux の分数の残りと倍率、ストレージ、支払いの上限（spending limit）の設定 |
| ネットワーク | 依存の取得（uv、`uv sync --locked`）だけが外部に接続する。以降は `--offline`、試験の中は Python の `socket` を遮断 | OS のレベルの遮断（`unshare -n` など）は CI に入れていない（runner で使えるか未確認） |
| 環境と対象の SHA の記録 | 新しい手順で、コミット（`git rev-parse HEAD`、`GITHUB_SHA`、`GITHUB_REF`）、runner の OS とイメージ、カーネル、利用者、Python と uv の版を、ジョブの要約（`GITHUB_STEP_SUMMARY`）に書く | 実行の後に、その記録を STATUS.md に写す |

**外部の確認とオーナーの承認が要る事項：**

1. ~~Action の正式な SHA~~：2026-11-27 に公式のリポジトリで確認し固定した（`89f8584`）。
2. ~~`workflow_dispatch` と既定のブランチの関係~~：公式の資料で確認した（既定のブランチに定義が要る）。
3. 2 が正しければ、CI をどう起動するかのオーナーの決定（`main` へのワークフローの追加、既定のブランチの変更などは、いずれもオーナーの承認が要る。今回は行わない）。
4. 無料の利用枠と支払いの上限の確認（オーナーのアカウントの設定）。
5. ~~runner の `sudo`~~：公式の資料で確認した（パスワードなしの sudo）。実際の動作は CI の実行で確かめる。
6. 初回の CI の実行の許可。

## 6. 未検証の事項

- CLI の統合試験の全体を一般ユーザーで実行すること。
- Windows、macOS、ネットワークのファイルシステム。
- 協調しない書き込み元（ADR-0008）、DB を持つ者の一貫した書き換え。
- 実行報告の永続化（プロセスの中だけ）。実際のプロバイダーの照会。
- E.Z1、Z5、D.V19、A.I11 の個別の照合。
- GitHub Actions の SHA、無料の利用枠。

## 7. 独立品質レビューの指摘の修正（QR-01〜QR-04）

Codex の独立品質レビューの判定は FAIL（CRITICAL 0、HIGH 1、MEDIUM 3、LOW 0）。指摘の4件を限定して修正した。試験を先に書き、修正の前の失敗を確かめた。

| 指摘 | 重要度 | 修正 | 試験（件数） | 修正の前 | 記録 |
|---|---|---|---|---|---|
| QR-01 結果と費用の原子的な照合 | HIGH | 結果不明の Attempt の費用だけの照合を拒否、下限の検査、以前の版の状態を報告して直さない | `test_qr01_reconcile.py`（9）、`test_wp12_cli_and_faults.py` の SIGKILL（1）。旧試験2件の期待値を変えた | 4件が失敗、6件は特性確認 | `WP-12_RECOVERY.md` 15.1 |
| QR-02 tenant の整合 | MEDIUM | Journal の tenant を正本とし、投影と親子を通常と読み取り専用の open で照合、矛盾した Journal を修復しない | `test_qr02_qr03_consistency.py`（23 のうち 5） | 3件が失敗、2件は特性確認 | 15.2 |
| QR-03 追加した表の整合 | MEDIUM | 区分の明文化、存在・孤立・時刻・状態・結果の検査、読み取りの拒否、推測しない | 同（23 のうち 18） | 15件が失敗、3件は特性確認 | 15.3 |
| QR-04 書き込みと完了の検証の順序 | MEDIUM | 決定の順ではなく Journal の位置で順序を証明、証明できなければ完了しない | `test_qr04_ordering.py`（14） | ループの2件が失敗、規則の10件は import で失敗、2件は特性確認 | `WP-15_AGENT_LOOP.md` 18章 |

- 試験は 2079 件から 2126 件（47 件の追加）。
- 既存の部分的な試験を、すべての経路の証明としては扱わない。例えば QR-04 の規則は同じ Run の書き込みについてだけで、別の Run や外部の書き込み元の順序は扱わない。
- FINAL-R05（並行実行の前に必要な Recovery API の契約）は `WP-12_RECOVERY.md` の 15.4 に残した。今の公式の CLI の経路では、修正の必要は見つからなかった（1つのプロセスの1つのセッションで命令を順に実行し、自分の生きている Run は `RUN_ACTIVE` で拒否する）。
- `sqlite_ledger.py` に責務が集まっていることを技術的負債として記録した（15.5）。今回は大きな分割をしていない。

## 8. 設計との差の延期の候補（オーナーの承認待ち。今回は承認も変更もしない）

3項目は、それぞれ別の条件で扱う。一括して Phase 4 に送らない。

| 項目 | 今の状態 | 延期するときの条件と影響 |
|---|---|---|
| A.I6 Snapshot | Rebuild は Journal の全体から決定的に行う。Snapshot はない | 容量：Journal が大きくなっても Rebuild に必要な記憶域は Journal の大きさに比例する。起動時間：開くたびに全体の検証と Rebuild をするため、イベントの数に比例して延びる（Phase 1 の合成のデータの量では測っていない。性能の主張はしない）。条件：Rebuild が状態のハッシュで照合できること（今は決定性の試験だけ）、Snapshot を入れるときは Snapshot と Journal の頭の一致を開くときに検査すること |
| A.I12 承認待ちの Run の再開 | 会話の記録はプロセスの中だけ。承認待ちの Run は再起動の後に再開できず、復旧で中止（FAILED）になる | 作業の喪失：承認待ちまでのモデルの呼び出しと、その費用は失われる（費用は記録に残り、二重に計上しない）。安全な停止：自動承認はせず、承認は無効になり、操作は実行されない（試験で確認済み）。条件：会話の記録の永続化と、再開の時の Context の検証 |
| A.I17 常時の期限の監視 | 期限は明示的な復旧の時だけ検査する | **Phase 2 の開始の前に再評価する：** タイムアウトと停止の処理との関係（長い実行で RECOVERING や CANCELLING のままになる時間）。それまでは、オーナーが `recover` を実行した時にだけ期限切れを `escalated` として報告する |

## 9. 一般ユーザーでの試験と特権の試験（2026-11-27）

### 9.1 失敗した8件の分類

| 試験 | 失敗の原因 | 分類 |
|---|---|---|
| `test_wp08_read.py` の `char-device`、`test_wp08_write.py` の `char-device` | 準備で文字デバイスを作る（`mknod`、`CAP_MKNOD` が要る） | 試験の準備が特権に依存（製品の問題ではない） |
| `test_wp08_workspace.py` の `other-owner`（ルート、親のディレクトリ）、`directory-of-another-user`（たどる途中） | 準備で別の利用者にファイルを渡す（`chown`、`CAP_CHOWN` が要る） | 同上 |
| `test_secret_scan.py` の `test_this_repository_has_no_findings`（3つの方式） | 検査の対象の作業ツリーの Git の管理情報が root の所有で、Git が "dubious ownership" で拒否した | 実行環境の問題。試験は検査の対象の作業ツリーをそのまま使い、準備の誤りはない。利用者が所有する clone では成功した（9.3） |

製品のコードの問題は見つからなかった。製品のファイルの検査と権限の検査は変えていない。Git の安全検査も無効にしていない。

### 9.2 特権の試験の管理

- `tests/support/privileged.py` の印 `privileged(reason)` で、特権の要る5件を区別した。理由は印に書く（`CAP_MKNOD`、`CAP_CHOWN`）。
- **省略しない：** root でないときに選ばれると、`PRIVILEGED_NOT_RUN` で失敗する（`tests/conftest.py`）。成功として数えない。一般ユーザーの実行では `-m "not privileged"` で明示的に除外する（pytest は「deselected」と報告する）。
- **保証を失わない：** root で `-m privileged` を別に実行する。試験の期待値と内容は変えていない。
- 一般ユーザーでも、所有者の比較そのものは確かめる：`test_the_walk_compares_each_owner_without_privilege`（実効 uid を別の利用者のものに見せ、自分のディレクトリを別の利用者のものとして、たどる途中の拒否を確かめる）。これは特権の試験の代わりではなく追加である。文字デバイスの拒否は、一般ユーザーでは FIFO とソケットの拒否で、同じ「普通のファイルでない」の分岐を確かめる。
- 一覧は `pytest --collect-only -m privileged` で出せる（今は5件）。

### 9.3 再検証の結果（HEAD `de79193`）

| 実行 | 利用者・環境 | 件数 | 結果 |
|---|---|---|---|
| 8種類の検査 | root、手元 | 2134 | すべて成功 |
| 全試験の連続2回 | root | 2134 | 2回とも成功 |
| CI の順序の検査 | `nobody`、`nobody` が所有する clone | — | 秘密情報（commit・index・worktree）、`uv lock --check`、ruff、ruff format、mypy、import-linter が成功。`uv sync --locked --offline --check` は、環境（`.venv`）が root の作業ツリーにあるため `nobody` では実行していない（root で成功） |
| 全試験（`-m` なし） | `nobody` | 2134 | 2129 が成功、特権の5件は `PRIVILEGED_NOT_RUN`（失敗として報告。成功に数えない） |
| `-m "not privileged"` | `nobody` | 2129（5件は除外） | すべて成功（秘密情報の検査の自己検査の3件を含む） |
| `-m privileged` | root（同じ clone） | 5（2129 件は除外） | すべて成功 |
| ネットワークのない環境（`unshare -n`） | `nobody`、`-m "not privileged"` | 2129 | すべて成功（外への接続は "Network is unreachable"） |
| ネットワークのない環境（`unshare -n`） | root、全試験 | 2134 | すべて成功 |

- 試験の数：2126 から 2134（印の試験5件、一般ユーザーでの所有者の比較1件、QR-04 の2件）。

### 9.4 QR-04 の確認

- `_write_order_reasons` は、過去の書き込みが互いに重なることそのものは拒否しない。拒否するのは、**最後の書き込みを証明できないとき**（最後に終わった書き込みが、ほかの書き込みと重なるとき）だけである。前の書き込みどうしが重なっていても、その後に始まって検証の前に終わった書き込みがあれば、それが最後と証明でき、完了の根拠になる。
- 以前の記録（`WP-15_AGENT_LOOP.md` の18章の「2つの書き込みの重なり」、報告書の13章の「重なり」、コミット `e3b75bb` の説明の "two overlapping writes ... refuse"）は、この区別があいまいだった。文書を正しい範囲に直した。コードの変更は不要だった。
- 回帰試験を2件加えた（重なる前の書き込みがあっても最後が証明できる場合は成功、最後の書き込みが重なる場合は拒否）。修正の前から成功する特性確認である。
- 今の Runtime は WP04-R03 により Run の Attempt を直列にしか動かさないため、同じ Run の書き込みが重なることは実際には起こらない。規則は、この制約に頼らずに順序を証明する。

### 9.5 独立再レビューが要る範囲

- `tests/conftest.py` の `pytest_runtest_setup`、`tests/support/privileged.py`、印を付けた5件、`test_the_walk_compares_each_owner_without_privilege`。
- `.github/workflows/ci.yml` の新しい手順（記録、一般ユーザーの試験、root の試験）。
- QR-04 の説明の訂正と、追加した2件の試験。
