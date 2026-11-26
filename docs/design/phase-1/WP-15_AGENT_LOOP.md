# WP-15 エージェントのループ（単一の Runtime、単一のエージェント、単純な Task）

> 文書の状態：**DRAFT**（91/100 の条件付き合格の後、限定修正 WP15-R01〜R05 を15章に記録した）　最終更新：2026-11-26
>
> - 起点：`47c4643`（WP-14）。作業の前に、リモートとの一致、作業ツリーが空であること、`main`、設計のブランチ、1789 件の試験と8種類の検査の成功を確かめた。
> - 段階1：`9f1c41f`（WP-14 の限定修正 R01〜R05。試験 1824 件）。記録は `WP-14_CONTEXT.md` の15章。
> - 段階2：`f3647f3`（ループと検証。試験 1833 件）。
> - 段階3：この文書のコミット（統合のシナリオ S1〜S9 と障害の試験。試験 1866 件）。
> - 対象：`PHASE_1_IMPLEMENTATION_PLAN.md` の WP-15。A の3章、E の7.1〜7.3、29.5、E.Z15。
> - **WP-10 には進んでいない。** WP-12、WP-13 の本実装、複雑な計画、Memory、自己改変、実モデル、実 API、実データ、任意のシェル、外部の作業領域への適用も、していない。

## 1. 範囲

| 実装した | 実装していない（範囲の外） |
|---|---|
| 単純な Task のための、1つの Run を進めるループ。Context の組み立てと検証、モデルの呼び出し、ツールの提案の検査、ツールの呼び出し、done criteria の検証、修復、上限、停止と待ちの状態、結果不明の扱い | 計画（複雑な Task は明示的に拒否する）、複数のエージェント、Memory、文脈の圧縮、再計画、自動の承認、結果不明の自動の再実行、再照合（WP-12）、別のプロセスでの継続、実モデル、外部の作業領域への適用、Phase 2 の Evaluation |

コード：

- ループ：`src/agent_core/runtime/agent_loop.py`（`AgentLoop`、`LoopConfig`、`LoopDeps`、`LoopLimits`、`LoopOutcome`、`TaskProfile`）
- 1つの Attempt の認可の経路：`src/agent_core/runtime/pipeline.py`（`AttemptPipeline`）
- 信頼する入力の組み立て：`src/agent_core/runtime/context_assembly.py`（段階1、WP14-R01）
- done criteria の仕様：`src/agent_core/runtime/verification.py`（`FileCriterion`、`CriteriaCatalog`）
- 試験の世界：`tests/support/loop_world.py`
- 試験：`tests/unit/test_wp15_loop.py`（段階2、9件）、`tests/unit/test_wp15_scenarios.py`（段階3、33件）

## 2. ループの構造

`AgentLoop.run(run_id, approval_id=None)` は、1回の呼び出しで Run を進める。完了、失敗、または待ちの状態（承認、予算、停止、再照合）に着いたら、`LoopOutcome` を返して制御を戻す。待っている間に回り続けること（busy loop）はない。

1回の反復の順序（指示の17の手順との対応）：

| # | 手順 | 実装 |
|---|---|---|
| 1 | Run の状態の確認 | 終わった状態と待ちの状態はすぐ返す。未解決の Attempt（開始済み・結果不明）があれば `RECONCILIATION_REQUIRED`。停止があれば `CANCELLING` を経て `CANCELLED`。上限の確認（`_check`） |
| 2 | Context の組み立て | `assemble_context_inputs`（Journal、台帳、Run の許可、信頼する文面だけから。WP14-R01） |
| 3 | Context の検証 | `build_context` の後に `verify_build`（Manifest、IR、描画、トークンの確認、信頼する入力からの決定的な再構築との比較。WP14-R03/R04） |
| 4 | MODEL_CALL の Step と Attempt | `create_step(MODEL_CALL)`、`AttemptPipeline.start` |
| 5 | Safety | `model_action` の記述子、`bind_model_descriptor`（要求のハッシュの結び付け）、`RecordDecision`（台帳が PDP を再実行する） |
| 6 | Cost | `required_for`（記録した見積もりの根拠と価格表から）、`ActivateReservation` |
| 7 | Intent、Permit、Receipt | `RecordIntent`、`IssuePermit`、`ConsumePermit` |
| 8 | 実行のゲート | `gate.execute`。送る要求は、許可された要求と同じもの（要求のハッシュを照合） |
| 9 | 応答の検証 | 型、`model_request_id` の一致。合わなければ結果不明 |
| 10 | ツールの提案 | 1つだけ受け付ける。登録済みで、Run の許可とポリシーの両方が固定したツールだけ |
| 11 | TOOL_CALL の Step と Attempt | 新しい Step と Attempt。同じ認可の経路を通る |
| 12 | 承認 | 必要なら `APPROVAL_PENDING`、Run は `WAITING_APPROVAL` で制御を戻す。自動の承認はしない |
| 13 | ブローカー | `run_file_operation` から `gate.execute_file`（ファイルの操作はこの経路だけ。WP08-R06） |
| 14 | 記録 | Step の終わり、ツールの結果を TOOL_RESULT の turn として記録 |
| 15 | 検証 | モデルが終わりと言ったら、done criteria をループが自分で読んで確かめる（6章） |
| 16 | 完了 | すべての基準が成り立ち、費用が確定していれば `VERIFYING` から `COMPLETED` |
| 17 | 修復 | 失敗なら `VERIFYING` から `RUNNING` に戻し、失敗の事実を観測として Context に入れ、上限の中で次のモデルの呼び出しへ（7章） |

計画：`TaskProfile.complexity` が `simple` の Task だけを扱う。`complex`、またはプロファイルがない Task は、モデルを呼ばずに `FAILED`（`PLANNING_UNSUPPORTED`）にする。

## 3. エンジンとの接続

| エンジン | 接続 | 注記 |
|---|---|---|
| Context Engine（WP-14） | `assemble_context_inputs`、`build_context`、`verify_build`、`bind_model_descriptor` | Core は Fake のプロバイダーを import しない（`ModelPort` だけ）。import の契約で確かめた |
| Safety（WP-09） | 記述子（`model_action`、`tool_action`）、`RecordDecision` の中の PDP の再実行、承認の要否は PDP の規則（`safety.pdp.approval_required`） | 承認が要るモデルの呼び出しは Phase 1 では扱わず、安全のための停止にする |
| Cost（WP-09） | 予約の割り当て、`required_for`、活性化、Gate の使用量の報告 | Step の上限は、オーナーの `SetStepCapDefault`（段階2、Journal の形式の版 7） |
| Permit・Receipt（WP-09） | `AttemptPipeline.authorize` | Phase 1 の台帳は消費者 `BROKER` だけを受け付けるので、モデルの呼び出しも `Consumer.BROKER` で消費する（`GATEWAY` は `CONSUMER_MISMATCH`）。外部への送信の関門は後の段階 |
| 実行のゲート（WP-09C、WP08-R06） | モデルは `execute`、ファイルは `execute_file` だけ | |
| ファイルのブローカー（WP-08） | `run_file_operation`、管理された合成の木の中だけ | |
| ツールの登録（WP-07） | `ToolRegistry.propose`（引数のスキーマの検査、正規化） | |

## 4. モデルの呼び出しの経路

1. 信頼する入力から Context を作り、検証する（失敗は例外で止まる。送らない）。
2. `ModelContent`（要求の記録と描画した入力）と記述子を作り、要求のハッシュを記述子に結び付ける。
3. 決定、予約、Intent、Permit、Receipt。どこかで拒否されたら、Attempt は `ABORTED_BEFORE_DISPATCH`、予約は解放（割り当てだけなら無効化）。何も送っていない。
4. ゲートの中で、許可の記述子の要求のハッシュと、送る要求のハッシュを照合してから `ModelPort.complete` を呼ぶ。
5. 結果の分類：
   - 送っていない（`ModelNotSentError`）：`FAILED_DEFINITE`、費用は確定した 0。観測（「The model call did not complete.」）を残して、次の反復へ。
   - そのほかの例外、型が違う応答、別の要求への応答：結果不明。ゲートが保留を残し、Run は `RECONCILIATION_REQUIRED`。
   - 正常な応答：使用量をゲートが報告する。
6. 応答は data（D4）。テキストは MODEL_RESPONSE の turn（アシスタントの発言）として記録する。ツールの要求が2つ以上なら全部を拒否し、拒否の観測を残す。

## 5. ツールの呼び出しの経路

1. ツールの呼び出しの上限を、呼び出しの**前**に確かめる。
2. ツールの名前が、登録済みで、Run の許可とポリシーの両方が固定したツールでなければ `TOOL_NOT_AVAILABLE` を TOOL_RESULT として返す（Step は作らない）。
3. `ToolRegistry.propose` で引数を検査し、`tool_action` で記述子を作る（対象の資源は引数と信頼する状態から導く）。範囲の外のパス（許可の外、拒否する資源、`..`、絶対パス）は、ここか決定で拒否され、Attempt は `ABORTED_BEFORE_DISPATCH`。拒否の理由を TOOL_RESULT として返す。
4. 承認が要るとき（`write_file` は IRREVERSIBLE）、Attempt は `APPROVAL_PENDING`、Run は `WAITING_APPROVAL`。`LoopOutcome.pending_action_hash` に承認すべき行為のハッシュを返す。
5. 再開は `run(run_id, approval_id=...)`。台帳の `ConfirmApproval` が承認を検査する（別の行為、期限切れ、停止は拒否）。拒否されたら、その Attempt を終わらせ、拒否を TOOL_RESULT として返し、ツールは実行しない。
6. 予約、Permit、Receipt、`execute_file`。結果（読んだ内容、または書き込みの要約）を TOOL_RESULT として返す。

## 6. 検証（VERIFYING）

- done criteria は Task の digest。Phase 1 では、各 digest が、信頼するカタログの中の合成の仕様（`FileCriterion`：管理された木の中の1つのファイルの内容の SHA-256）を指す。digest は仕様の正規の JSON の SHA-256 なので、仕様は取り替えられない。
- モデルの「終わった」という発言は主張であって証明ではない。ループは、基準ごとに VERIFICATION の Step を作り、`read_file` を同じ認可の経路とブローカー（安全な読み取りの経路）で読み、内容の digest を比べる。モデルの自己評価は使わない。
- 読み取りは Run が `RUNNING` の間に行い、すべての Attempt と Step が確定してから `VERIFYING` に移す（状態機械の規則）。
- 完了の条件：すべての基準が成り立ち、`VerificationEvidence` が Task の done criteria とちょうど同じ集合を覆い、確定していない予約が残っていないこと。状態機械も、この条件を自分で確かめる。
- 失敗なら、`VERIFYING` から失敗の `VerificationEvidence` を付けて `RUNNING` に戻す。期待した digest と見つけた digest（または missing）を観測として Context に入れる。
- これは管理された木の中の検証であり、外部の作業領域への後の適用の検証（E 29.5）でも、Phase 2 の独立した Evaluation でもない。

## 7. 修復のループと上限

`LoopLimits` のすべての値を明示する（既定値はない）：

| 上限 | 意味 | 超えたとき |
|---|---|---|
| `max_repairs` | 検証の失敗の後に始める修復の回数 | `FAILED`（`VERIFICATION_FAILED`、理由 `REPAIR_LIMIT`） |
| `max_model_calls` | モデルの呼び出しの回数 | `FAILED`（`LIMIT_EXCEEDED`） |
| `max_tool_calls` | ツールの呼び出しの回数（呼び出しの前に確かめる） | `FAILED`（`LIMIT_EXCEEDED`） |
| `max_iterations` | 反復の回数（拒否だけの turn も数える） | `FAILED`（`LIMIT_EXCEEDED`） |
| `max_elapsed_ms` | Run の開始からの経過時間 | `FAILED`（`LIMIT_EXCEEDED`） |

- 設計の「修復 3 回」は試験の値（暫定）で、オーナーが承認した運用の値ではない。
- 予算は台帳が強制する（Run、Task などの範囲の上限、モデルとツールの呼び出しの数、再試行の数、Run の時間の窓）。超えたら `PAUSED_BUDGET`。
- 再試行：送っていないことが確定した呼び出しだけが、新しい Attempt として次の反復で再び行われうる（台帳の呼び出しの数とループの上限で制限される）。結果不明の作業を自動で再実行することはない。

## 8. 停止、待ち、結果不明

| 状態 | 入る条件 | ループの振る舞い |
|---|---|---|
| `WAITING_APPROVAL` | 承認が要るツールの行為。または Context の予算の不足（B1 4.3） | 制御を戻す。承認なしで呼んでも待ったまま。Context の予算の不足では、モデルを呼ばず、再開の経路は Phase 1 にない（オーナーの判断） |
| `PAUSED_BUDGET` | 予算の不足。または確定していない費用（`UNSETTLED_COSTS`） | 制御を戻す。再び呼んでも何もしない。予算を元に戻すことも、二重に数えることもない |
| `CANCELLING` → `CANCELLED` | GLOBAL、TENANT、または RUN の停止。承認を待っている間の停止も含む（承認より停止を優先する） | 待っている Attempt を `ABORTED_BEFORE_DISPATCH`（`STOPPED`）にし、その Step を `CANCELLED` にして、Run を `CANCELLED` にする（状態機械が、確定していないものがないことを確かめる） |
| `RECONCILIATION_REQUIRED` | 結果不明の Attempt（モデル、ファイル） | 保留を解放しない。再び呼んでも何もしない。完了しない。再照合は WP-12 |
| `FAILED` | 上限、修復の上限、計画の拒否、モデルの呼び出しの拒否 | Termination の理由を付ける |
| `ABORTED_SAFETY` | Permit や Receipt の拒否（例：合わない Permit） | Attempt を終わらせ、予約を解放する |
| `COMPLETED` | 6章の条件 | — |

## 9. シナリオの結果

試験：`tests/unit/test_wp15_scenarios.py`。すべて、本物の台帳、Runtime、実行のゲート、Context Engine、ファイルのブローカーと、呼び出しの順に台本を与えた Fake のモデルで動かした。

| シナリオ | 試験 | 結果 |
|---|---|---|
| S1 通常の作業 | `test_s1_normal_work` | 成功。`COMPLETED`、モデル2回、ツール1回、修復0回、保留は残らない |
| S2 失敗からの修復 | `test_s2_a_failed_verification_is_repaired` | 成功。検証 `[False, True]`、修復1回。失敗の事実（期待と実際の digest）が次の要求に入る |
| S3 偽りの完了の主張 | `test_s3_a_false_completion_claim_is_never_accepted` | 成功。「完了した」と言い続けても `COMPLETED` にならず、`REPAIR_LIMIT` で `FAILED`（モデル4回、修復3回） |
| S4 上限の超過 | 修復、モデル呼び出し、ツール呼び出し（呼び出しの前）、反復（拒否だけの turn）、経過時間の5件 | 成功。すべて有限の回数で `FAILED` |
| S5 安全 | 未登録のツール、範囲の外のパス（4種類）、予算の超過、合わない Permit、承認なし・別の行為の承認、期限切れの承認、開始前の停止、待ちの間の停止、モデルによる偽の system の指示 | 成功。何も実行されず、ファイルは作られず、保留は残らない。偽の指示は、次の要求でアシスタントの発言としてだけ現れ、system の規則、ポリシー、ProtectedFact、ユーザーの発言には入らない |
| S6 結果不明 | モデル（結果不明、ストリームの中断、不正な応答、プロバイダーの拒否）、ファイルの書き込みの中断、新しいループのインスタンス、送っていない呼び出し | 成功。誤った解放なし、自動の再実行なし、完了なし。送っていない呼び出しだけは確定した失敗として次へ進む |
| S7 Context の予算の不足 | `test_s7_a_context_that_does_not_fit_sends_nothing` | 成功。同じ窓で短い Task は送れるが、長い保護された目標は削らずに、モデルを呼ばず `WAITING_APPROVAL`（`CONTEXT_BUDGET_EXCEEDED`） |
| S8 決定性 | `test_s8_the_same_script_gives_the_same_run` | 成功。2つの別の世界で、意味のある遷移、Attempt の終わりの状態、成果物、費用、検証の結果、要求のメッセージ数が一致する（ID は比べない） |
| S9 費用の整合 | 4件（10章） | 成功 |

### 9.1 実行例：通常の作業（S1）

```
outcome: COMPLETED, reason=VERIFIED, model_calls=2, tool_calls=1, repairs=0
trace:    MODEL_CALL:COMPLETED -> TOOL_CALL:COMPLETED -> MODEL_CALL:COMPLETED -> VERIFICATION:COMPLETED
statuses: WAITING_APPROVAL -> RUNNING -> VERIFYING -> COMPLETED
verification: [True]
spent (XTU micro): MODEL 180, TOOL 0, MODEL 140, VERIFICATION 0 = Run 320
```

1回目の `run` は、`write_file` の承認を待って `WAITING_APPROVAL` で戻る。オーナーがその行為のハッシュを承認し、`run(approval_id=...)` で再開すると、書き込み、2回目のモデルの呼び出し（終わりの主張）、検証の読み取りを経て完了する。

### 9.2 実行例：修復のループ（S2）

```
outcome: COMPLETED, reason=VERIFIED, model_calls=4, tool_calls=2, repairs=1
trace:    MODEL_CALL -> TOOL_CALL(draft) -> MODEL_CALL -> VERIFICATION
          -> MODEL_CALL -> TOOL_CALL(replace) -> MODEL_CALL -> VERIFICATION   (all COMPLETED)
statuses: WAITING_APPROVAL -> RUNNING -> VERIFYING -> RUNNING -> WAITING_APPROVAL
          -> RUNNING -> VERIFYING -> COMPLETED
verification: [False, True]
spent: 180 + 140 + 180 + 140 = 640 (model only; the file tools cost 0)
3rd request contains: "Verification failed: workspace/out/report.txt: expected sha256:0ddd…, found sha256:e554…"
```

## 10. 費用の整合（S9）

- モデルの各 Attempt の予約の `spent_micro` は、その応答の使用量の費用と一致する。すべての予約の費用は確定している。Run の範囲の費用は、すべての Attempt の費用の和と一致する（二重に数えない）。
- 台帳の数：Run の `MODEL_CALLS` はモデルの呼び出しの回数、`TOOL_CALLS` は書き込みと検証の読み取りの回数（検証の読み取りも台帳ではツールの呼び出しとして数える。ループの `tool_calls` は数えない）。
- 修復の費用も数える（S2 は S1 より多く、和と一致する）。
- 使用量が最終でない応答（費用が未確定）があると、完了せず、`PAUSED_BUDGET`（`UNSETTLED_COSTS`）で止まり、再び呼んでも何も実行しない。
- `PAUSED_BUDGET` の後に何度呼んでも、費用は増えず、予算は元に戻らない。

## 11. 段階3で見つけて直した不具合

統合のシナリオを先に書き、赤（18件の失敗）を確かめた。そのうち試験の側の誤り（状態の列の起点、別の作業ディレクトリの作成、合成の文面の長さの上限、偽の指示の現れる場所の想定）を直し、次の実装の不具合を直した：

1. 割り当てだけの予約（活性化の前）を持つ Attempt を中止すると、解放を台帳が拒否していた（`ID_ALLOCATED_TO_RELEASED`）。決定での拒否、予算の不足、承認の拒否、停止のすべての経路が例外で落ちていた。`AttemptPipeline.abort` は、割り当てだけなら `VoidAllocation`、予約済みなら解放にした。
2. 送っていないモデルの呼び出しの使用量を作るときに型の誤りがあり、結果不明として扱われていた。プロバイダーの例外の使用量（確定した 0）を使うようにした。
3. Permit の消費の拒否が、例外のままループの外に出ていた。Attempt を終わらせ、予約を解放し、`ABORTED_SAFETY`（`PERMIT_REFUSED`）にした。
4. 修復の回数が1つ多く数えられていた。上限の中で始めた修復だけを数えるようにした。
5. ツールの呼び出しの上限を、呼び出しの後に確かめていた。呼び出しの前に確かめるようにした。
6. 停止で `CANCELLING` に留まり、承認を待っている行為が残っていた。承認より停止を優先し、待っている Attempt を中止して `CANCELLED` まで進めるようにした。
7. 確定していない費用があるとき、Run を `RUNNING` のまま返していた（次の呼び出しで、さらにモデルを呼びうる）。`PAUSED_BUDGET` で止めるようにした。
8. 予算の不足で中止した Attempt の Step を終わらせていなかった（コードを読んで見つけた）。

最後に加えた2件（プロバイダーの拒否、新しいループのインスタンス）は、最初の実行で成功した（今の振る舞いを確かめる試験）。

## 12. 試験の数

| 時点 | 件数 |
|---|---|
| 起点（`47c4643`） | 1789 |
| 段階1（`9f1c41f`） | 1824（+35） |
| 段階2（`f3647f3`） | 1833（+9） |
| 段階3 | 1866（+33） |

8種類の検査（秘密の走査、`uv lock --check`、`uv sync --check`、ruff check、ruff format、mypy、import-linter、pytest）が、すべて成功した。`uv.lock` は変えていない。

## 13. 保証すること、しないこと、未検証の項目

保証する（試験で確かめた範囲）：

- モデルとツールの各呼び出しが、それぞれの Step と Attempt として、決定、承認、予約、Intent、Permit、Receipt、実行のゲートを通ること。ファイルの操作は `execute_file` だけ。
- 送る要求が、検証した Context で、許可された要求と同じであること。
- 承認が要る行為を自動で承認しないこと。停止を承認より優先すること。
- モデルの主張ではなく、ファイルの実際の内容で完了を決めること。
- 結果不明の作業を再実行しないこと、保留を誤って解放しないこと、完了しないこと。
- すべての上限で有限の回数で止まること。

保証しない・未検証：

- **会話の記録はプロセスの中だけ。** 別のプロセス（再起動）は Run を続けず、待っている行為も実行しない。永続化と再開は後の段階（WP-12 など）。
- **事実の解決は無効**（WP14-R02）。そのため、例えば検証で存在しないファイルを読んだ失敗（`FAILED_DEFINITE`）は、その後の Context に OPEN_ERROR として残り続ける。
- Context の予算の不足で `WAITING_APPROVAL` に入った後の再開の経路はない（B1 4.3 のオーナーの判断の手順は未実装）。
- `PAUSED_BUDGET` からの再開（オーナーが上限を上げた後）は未実装。
- プロバイダーの拒否（送った後の拒否）は、費用が未確定なので結果不明と同じに扱い、再照合を求める（慎重な側）。確定した失敗として扱うかは、WP-12 で決める。
- 承認が要るモデルの呼び出しは扱わない（安全のための停止）。
- 1回の応答で1つのツールの要求だけ。
- 合成のデータ、Fake のモデル、合成の推定器だけ。実際のトークン数、実モデルの振る舞い、Windows での決定性、OS の隔離は未検証。
- ハッシュは合成のデータ向け。実データでは鍵付きのハッシュが必要（C2 6.2）。
- 消費者 `BROKER` での消費は Phase 1 の台帳の制約による。外部への送信の関門（GATEWAY）は後の段階。
- done criteria の種類は `file_content` だけ。

## 14. 残っている課題

- AUDIT-02〜04、A-P6、CI（GitHub Actions は未実行）、OS の隔離、実データのための鍵付きのハッシュ（変わらず残っている）。
- 13章の「保証しない・未検証」の各項目。
- 修復の上限 3 は暫定の試験の値。運用の値はオーナーの決定を待つ。

## 15. 限定修正 WP15-R01〜R05（91/100 の条件付き合格の後）

起点：`7ec1e7b`。回帰の試験 `tests/unit/test_wp15_fixes.py`（30件）を先に書き、赤を確かめた（最初は全件。`LoopDeps.monotonic` だけを足した後も、29件が振る舞いの理由で失敗した。その中には、再入の呼び出しで Run が `RECONCILIATION_REQUIRED` に変わる不具合と、開始済みの Attempt で状態機械が例外を出す不具合があった）。

### 15.1 R01 再起動と実行の上限

- `AgentLoop` は、自分が始めた Run だけを続ける。Attempt がすでにある Run（ループの Step には必ず Attempt がある）に、このプロセスの会話の記録がなければ、何も変えずに `SESSION_UNAVAILABLE` を返す。回数、修復の回数、経過時間は初期化されない（新しい Run として扱わない）。
- 同じ Run への再入と同時の呼び出しは `RUN_BUSY` で拒否し、何も変えない。
- 結果不明の Attempt があれば `RECONCILIATION_REQUIRED`（RUNNING、CANCELLING から）。開始済みで結果不明として記録されていない Attempt（プロセスの終了など）は、状態機械が再照合の遷移を認めないので、何も変えずに `UNRESOLVED_ATTEMPT` を返す（WP-12 へ）。
- このループが記録を終えられなかった失敗の後は、その Run を二度と触らない（`LOOP_BROKEN`）。
- `LoopOutcome` は、このプロセスの数（`model_calls`、`tool_calls`、`verification_reads`、`repairs`）と、台帳に永続化された数（`ledger_model_calls`、`ledger_tool_calls`）を分けて返す。
- 会話の記録の永続化と、別のプロセスからの再開は WP-12 に残す。

### 15.2 R02 承認待ちの間の停止

- 停止（RUN、TENANT、GLOBAL）の確認を、承認 ID の有無より先に行う。停止は承認より優先する。
- 対象は `RUNNING`、`WAITING_APPROVAL`（承認待ち、Context の予算の不足の両方）、`PAUSED_BUDGET`、`CANCELLING`。
- 停止のとき、まだ始まっていない Attempt を、Permit の消費の前なら `AttemptPipeline.abort`、消費の後なら Gate の未実行の報告で中止し、開いている Step を `CANCELLED` にする。セッションのない新しいループのインスタンスでも停止できる。
- 結果不明があれば `RECONCILIATION_REQUIRED`（正常な `CANCELLED` にしない）。費用が確定していなければ `CANCELLING` のまま（理由 `UNSETTLED_COSTS`）。どちらも WP-12 に引き継げる状態を保つ。

### 15.3 R03 検証と完了の判定

- まず、すべての基準のファイルを、正式な認可の経路とブローカーで読む（安全な読み取りの経路。直接は読まない）。
- すべてが成り立ったときだけ、すべてのファイルをもう一度読み（確認の読み取り）、1回目と一致しなければ完了を拒否する（`FAILED`、`ARTIFACT_CHANGED`）。複数のファイルを読む間に、ほかのファイルが変わった場合も、これで見つかる。
- ApplyPlan との対応：基準のファイルに確定した書き込みがあるなら、その内容の digest が読んだ digest と同じでなければならない（違えば `APPLY_PLAN_MISMATCH`）。
- 判定の直前に、停止、未確定の費用、経過時間、Run の許可とポリシー（なければ `ABORTED_SAFETY`、`GRANT_MISSING`）、アクセスの epoch（変わっていれば `ACCESS_CHANGED`）を確かめ直す。
- `VerificationEvidence` の `artifact_hash` は、各基準の「パス=読んだ digest」の列のハッシュで、`expected_artifact_hash`（「パス=期待の digest」）と一致したときだけ合格になる（型の検査）。
- 協調する書き込み元との排他：ループは同期で動き、Runtime の OS のロックで単一の書き込み元である。ブローカーは各操作の間、作業領域の `flock` を持つ。
- **限界（ADR-0008 を維持）：** 最後の読み取りと `COMPLETED` の記録の間に、協調しない書き込み元がファイルを変えることは防げない。ブローカーのロックは1回の操作の間だけで、検証の全体にまたがらない。

### 15.4 R04 各上限の強制

- モデルの呼び出し、ツールの呼び出し、検証の読み取りの前に、毎回、停止、未確定の費用、経過時間、その操作の回数を確かめる（`_guard`）。承認待ちから再開するときも、承認された行為を実行する前に確かめ、超えていれば待っている Attempt を中止してから `FAILED`（`LIMIT_EXCEEDED`）にする。
- 経過時間は単調時計（`LoopDeps.monotonic`）で測り、見た最大の値を保つ。壁時計の逆行や、単調時計の値の後退では、時間は延びない。
- 検証の読み取りも、台帳ではツールの呼び出しとして数える（台帳の `TOOL_CALLS` の上限が、検証の読み取りも止める。そのときは `PAUSED_BUDGET`）。ループの `max_tool_calls` は、モデルが求めたツールの呼び出しだけに使う。
- 費用が確定していない Attempt があれば、次の操作をせずに `PAUSED_BUDGET`（`UNSETTLED_COSTS`）で止まる。
- ループの上限と台帳の上限の両方を保つ。

### 15.5 R05 例外が起きたときの状態

| 障害 | 扱い |
|---|---|
| Context の組み立てや検証の誤り | 送らない。Attempt を中止し、Step を終わらせ、`ABORTED_SAFETY`（`CONTEXT_INVALID`） |
| モデルの応答の不正 | 結果不明（Gate が保留を残す）、`RECONCILIATION_REQUIRED` |
| 承認の拒否 | その行為を実行せず、拒否を TOOL_RESULT として返す |
| Permit の拒否 | 消費の前なら中止と解放、`ABORTED_SAFETY`（`PERMIT_REFUSED`）。停止によるものなら `CANCELLED` |
| Receipt の拒否（消費の後、開始の前） | Gate の未実行の報告で中止と解放、`ABORTED_SAFETY`（`NOT_DISPATCHED`）。停止によるものなら `CANCELLED` |
| ブローカーの想定外の障害（開始の前） | 同上。`LoopOutcome.error` に例外のクラス名だけを入れる |
| ブローカーの障害（開始の後） | 結果不明、`RECONCILIATION_REQUIRED` |
| 台帳や Journal の書き込みの失敗 | 元の例外をそのまま出し、その Run を二度と触らない（`LOOP_BROKEN`） |
| 後処理の失敗 | 元の例外に、後処理の例外のクラス名を note として付けて、元の例外を出す |
| 割り込み（Ctrl+C など） | 安全な記録をした後、割り込みをそのまま渡す |

未実行が確実な操作（Permit の消費の前、または Gate の未実行の報告ができる消費の後・開始の前）と、結果不明の操作（開始の後）を区別する。完全な Recovery Engine は実装していない。

### 15.6 既存の試験の変更

- 合格する検証には確認の読み取りが加わるので、`test_wp15_loop.py` の1件と `test_wp15_scenarios.py` の2件で、期待する Step の列に VERIFICATION を1つ足した（設計の変更による）。
- 経過時間の上限の試験（`test_s4_the_elapsed_time_limit_ends_the_run`）は、壁時計ではなく単調時計を進めるように変えた（設計の変更による）。
- 試験の削除、スキップ、条件の緩和はしていない。

### 15.7 試験の数

1866 件から 1896 件（+30）。8種類の検査が、すべて成功した。`uv.lock` は変えていない。

## 16. 限定修正 WP15-R06：最終の検証と完了

起点：`88ab790`。回帰の試験 `tests/unit/test_wp15_r06.py`（10件）を先に書いた（`CompletionRefusedError` がなく、収集の時点で失敗）。

- **協調する書き込み元との排他：** 確認の読み取りから完了の記録までの間、実行のゲート（この Runtime のツールの呼び出しの唯一の PEP）が「書き込みの保留」（`verification_hold`）に入り、変更を伴う操作（書き込みのファイル操作、READ_ONLY でないツール）を `GATE_BUSY` で拒否する。読み取りは続く。再入はできない。同じホームに Runtime は1つだけ（OS のロック）なので、協調する書き込み元はすべてこのゲートを通る。
- **完了の記録と検証した対象の対応：** `VerificationEvidence` に `verified_reads`（確認の読み取りの Attempt）を加えた。
- **Journal の完了のトランザクションでの強制：** Run が COMPLETED になるイベントの追記と同じトランザクションの中で、台帳（`sqlite_ledger._completion_basis`）が次を確かめ、満たさなければ `CompletionRefusedError` で全体を取り消す。
  - `verified_reads` が空でない。どれもこの Run の、完了した Attempt。記録された記述子が、READ_ONLY のツールで、対象がすべて READ のファイルの読み取り（変更なし）。予約は使用量の報告済みで、費用が確定している。
  - 読み取りの記述子の policy_version とアクセスの epoch が、今のものと同じ。
  - Run の許可がある、期限内（台帳の時刻で）、policy_version が今のポリシーと同じ、許可のときのアクセスの epoch が今と同じ（失効していない）。
  - Run、テナント、全体のどれも停止していない。
  - 費用の確定していない予約がない（既存の `UNSETTLED_COST` の検査）。
- 作っただけの `VerificationEvidence`（読み取りを指さない、または書き込みの Attempt を指す）では完了できない（試験で確かめた）。ループは、この拒否を `ABORTED_SAFETY`（`COMPLETION_REFUSED`）にする。
- 既存の試験の変更：`test_wp09_execution.py` の1件は、作っただけの証拠で Run を完了させていた。それが拒否されることを確かめる形に変え、Task の完了の成功の経路は、本物の検証の読み取りを伴う新しい試験 `test_a_verified_run_lets_its_task_complete` に移した。`test_runtime_service.py` の2件（Rebuild と Journal の検証）は、作っただけの証拠の補助関数 `completed_scenario` の代わりに、ループが本物の読み取りで完了させた Run（`loop_world.completed_world`）を使うようにした。
- **ADR-0008 の限界は変えない：** 協調しない書き込み元（このゲートを通らない別のプロセス、同じユーザーの直接の書き込み）が、最後の読み取りと完了の記録の間にファイルを変えることは防げない。

