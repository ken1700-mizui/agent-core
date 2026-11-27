# WP-12 障害からの復旧（Recovery）

> 文書の状態：**DRAFT**（90/100 の条件付き合格の後、限定修正 WP12-R01〜R05 を14章に記録した）　最終更新：2026-11-26
>
> - 起点：`88ab790`（WP-10）。作業の前に、リモートとの一致、作業ツリーが空であること、`main`（`aa089ae`）、設計のブランチ（`e6b70c0`）、1937 件の試験と8種類の検査の成功を確かめた。
> - 段階1：`b23b0ac`（WP-10・WP-15 の限定修正。記録は `WP-10_CLI.md` の15章、`WP-15_AGENT_LOOP.md` の16章）。
> - 段階2：`2f4f9c1`（Rebuild、ProjectionRepair、復旧と照合）。
> - 段階3：この文書のコミット（CLI との接続、障害の注入、実際のプロセスの停止の試験）。
> - 対象：`PHASE_1_IMPLEMENTATION_PLAN.md` の WP-12。A の6章・7章、A.I9、A.I17、E の2.4・3章・16章、E.Z17、E.Z32。
> - **WP-13 には進んでいない。** 完全なバックアップと復元、実モデル、実 API、実データ、外部への通信は、ない。

## 1. 範囲と責務の分離

| 責務 | どこで | 内容 |
|---|---|---|
| Runtime の所有権の取得 | `SqliteJournal.open`（既存、WP-03・WP09-R07） | OS のロック、全体の検証、`verify_consistency`、新しいオーナーの epoch、古い所有者の未使用の Permit と Receipt の失効（`revoke_stale`） |
| Rebuild | `rebuild_with_head`、`RuntimeStateMachine.load`（既存） | 検証した Journal のイベントから状態を畳み込む純粋な処理。書き込まない。モデルもツールも呼ばない。外部に通信しない |
| 読み取り専用の状態確認 | `SqliteJournal.open_read_only`（段階1） | 所有権を取らない。epoch も失効も回復もしない |
| 評価 | `recovery.assess`（新規） | 終わっていない Run と Attempt を、復旧の表で分類する。読むだけ |
| 復旧 | `recovery.recover`（新規） | オーナーの明示的な操作。表に従い、1つの手順ごとに Journal の1つのトランザクション（ペアリングが求める台帳の操作と一緒に）。2回目は何も変えない |
| 照合 | `recovery.reconcile`、`declare_unresolved`（新規） | 信頼できる証拠でだけ、結果不明を確定する |
| 一時ファイルの回収 | `recovery.recover_temporaries`（新規。WP08-R07 の `recovery_receipts` と `recover_temporaries` に接続） | オーナーの明示的な操作。`recover` は、ファイルを消さない |
| ProjectionRepair | `SqliteJournal.repair_projections`、`sqlite_ledger.repair_mirror`（新規） | 投影だけを Journal から作り直す。排他のロックの下の1つのトランザクション |

既存の仕組み（開くときの検査、`verify_consistency`、`revoke_stale`、`RecoverInFlight`、`Reconcile`、`recovery_receipts`、`recover_temporaries`）は、そのまま使い、同じ役割の仕組みを重ねて作っていない。

## 2. Rebuild と ProjectionRepair の区別

- **正本：** Journal のイベント（順序、版、ハッシュの連鎖）。費用（予約、台帳の記録）、承認、Permit、Receipt、停止、失効、監査の記録も、それぞれが正本であり、投影ではない。
- **投影：** Phase 1 にある投影は、台帳の中の Runtime の写し `rt_tasks`、`rt_runs`、`rt_steps`、`rt_attempts` だけ（WP09-R02 で、各イベントと同じトランザクションで書かれる）。Memory Store と検索の索引はまだないので、扱わない。
- **Rebuild** は、メモリの中だけで状態を作る。イベントの順序、連続性、テナント、ハッシュの連鎖、版を検査し、知らないイベントの種類や版は拒否する（試験：順序の入れ替え、版、連鎖、種類の4つ）。同じ記録からは同じ状態になる。Journal には何も書かない（読み取り専用の Journal で行い、DB の内容が変わらないことを確かめた）。
- **ProjectionRepair**（`repair_projections`）：
  1. Runtime の OS のロックを取る（他の書き込み元はいない）。
  2. 1つの書き込みのトランザクションで、まず正本のイベントを全部検証する（`quick_check`、外部キー、各テナントの連鎖）。
  3. `rt_*` のうち、イベントが記録するものと違う表だけを作り直す。既にある行の `created_at_ms` は残し、ない行はその作成のイベントの記録時刻を使う（推測しない）。
  4. その後、`verify_consistency` のすべての検査（予約、Permit、台帳の記録、監査、停止と失効）が成り立たなければ、全体を取り消す。
  5. 正本の破損（イベントの改変、費用の記録の改変など）は、自動で直さない。何も変えずに拒否し、Journal は開かない（隔離・停止）。
- 2回実行しても、2回目は何も変えない（空の結果）。途中の異常終了では、SQLite のトランザクションにより、元のままになる。
- 所有権の epoch は取らず、何も失効させない（保守の操作）。

## 3. クラッシュ地点ごとの復旧表

`recovery.RECOVERY_TABLE`。復旧は、再起動の後（新しいオーナーの epoch で、古い未使用の Permit と Receipt は失効済み）に、オーナーが明示的に行う。

| Attempt の状態 | 未実行を証明できるか | 古い Permit と Receipt | 予算の予約 | 結果不明として保持 | Step | 規則 |
|---|---|---|---|---|---|---|
| PROPOSED、VALIDATED | できる（Permit がない） | ない | ない | しない | FAILED | `ABORT_NOTHING_HELD` |
| CONTENT_FIXED、POLICY_ALLOWED、APPROVAL_PENDING、APPROVED | できる（Permit がない） | ない | 割り当てだけ：無効化（VOID） | しない | FAILED | `ABORT_NOTHING_HELD` |
| BUDGET_RESERVED、INTENT_RECORDED | できる | ない | 解放（RELEASED） | しない | FAILED | `ABORT_RELEASE` |
| PERMIT_ISSUED | できる（消費は PERMIT_CONSUMED と同じトランザクションで記録される。発行済みの Permit は再起動で失効済み） | 失効済み | 解放 | しない | FAILED | `ABORT_RELEASE` |
| PERMIT_CONSUMED、DISPATCH_AUTHORIZED | できる：Journal に DISPATCH_STARTED がない（ゲートとブローカーは、DISPATCH_STARTED の記録の後でだけ操作を行う）。Receipt が失効しているという事実だけでは判断しない | Receipt は失効済み | 結果不明にしてから、記録した0の費用で照合（RECONCILED） | しない | FAILED | `PROVEN_NOT_STARTED` |
| DISPATCH_STARTED | できない | 使用済み | 保持（UNKNOWN） | する | RECONCILIATION_REQUIRED | `KEEP_UNKNOWN` |
| OUTCOME_UNKNOWN | 履歴に DISPATCH_STARTED がなければできる（上と同じ証明）。あればできない | 使用済み | 保持（UNKNOWN） | する（証明できる場合を除く） | RECONCILIATION_REQUIRED | `KEEP_UNKNOWN` |
| COMPLETED、FAILED_DEFINITE、ABORTED_BEFORE_DISPATCH | 終わっている | — | 確定済み | — | 変えない | `FINAL` |

未実行と確実に証明できない場合は、すべて結果不明にする。

## 4. Run と Step

- 中断された Run（QUEUED、RUNNING、WAITING_*、PAUSED_BUDGET、VERIFYING）は、RECOVERING にする。停止されていて CANCELLING に移れる Run は、CANCELLING にする（停止と `cancel_requested` を保つ）。
- Attempt を表に従って処理した後：
  - RECOVERING：結果不明があれば RECONCILIATION_REQUIRED（`OUTCOME_UNKNOWN`）、なければ RECONCILIATION_REQUIRED（`SESSION_LOST`、新しい理由）。
  - CANCELLING：結果不明があれば RECONCILIATION_REQUIRED、なければ Step を CANCELLED にし、確定していれば CANCELLED。
  - RECONCILIATION_REQUIRED：開いている Step を閉じる（結果不明があれば RECONCILIATION_REQUIRED）。結果不明がなく、費用が確定していれば、続ける方法がない（会話の記録がない）ので、FAILED（`RECOVERY_FAILED`）で終える。再開は決してしない。
- **期限（A.I17）：** RECOVERING と CANCELLING に、`RecoveryDeadlines` を超えて留まった Run は、RECONCILIATION_REQUIRED（`DEADLINE_EXCEEDED`）にする。期限の値は明示的な入力で、承認された運用の値ではない（CLI の値は合成の試験の値）。
- **時刻：** 経過時間の「今」は、壁時計と、記録された最後のイベントの時刻の大きい方。壁時計が戻っても、期限は延びない（試験で確かめた）。
- **上限：** 復旧は、上限を初期化しない。新しい Attempt を作らない。古いオーナーの epoch の Permit と Receipt は使えない（開くときに失効済み）。
- 会話の記録や Fake の台本を推測で作り直して、途中の Run を続けることはしない（`SESSION_UNAVAILABLE` を保つ）。完全な会話の記録の永続化と長い Task の継続は Phase 4。

## 5. 費用の整合

- 消費済みの費用はそのまま。結果不明の保留は、無断で解放しない。使用量が不明なものを0とみなさない。
- 解放は、未実行を証明できる場合だけ、既存の規則で行う：消費の前なら解放か無効化、消費の後なら結果不明を経て、記録した0の費用でオーナーの照合（`Reconcile`）。
- 二重の解放と二重の計上は、台帳が拒否する（試験：2回目の `recover`、解放の再送、照合の再実行）。
- Task の予算と範囲は、Run をまたいで残る（結果不明の保留も Task の範囲で数える）。新しい Run で上限を回避できない。
- 台帳の UNKNOWN と Attempt の OUTCOME_UNKNOWN を合わせる：台帳だけが UNKNOWN の Attempt（`RecoverInFlight` の後）は、台帳の操作なしで Attempt を結果不明にし、その後、表に従う。
- 同じトランザクションにできないもの：オーナーの照合（`Reconcile`、オーナーの batch）と、Attempt の結果の遷移は、別のトランザクション（ペアリングがオーナーの操作を遷移と一緒にすることを禁じる）。間で異常終了しても、2回目の照合は、既に照合済みの予約を照合し直さず、遷移だけを行う。

## 6. 結果不明と照合

`recovery.reconcile(attempt, evidence)` は次を区別する。

| 結果 | 条件 |
|---|---|
| `EXECUTED` | ゲートが署名した実行報告（`ExecutionReport`、Permit の鍵の MAC）が、その Attempt、Receipt、予約に一致し、結果が COMPLETED で、費用が最終で分かっている。Attempt は COMPLETED、予約はその費用で照合 |
| `FAILED` | 同じ報告で、結果が FAILED_DEFINITE |
| `NOT_EXECUTED` | 証拠なしで、Journal に DISPATCH_STARTED がない（未実行の証明）。Attempt は FAILED_DEFINITE、0で照合 |
| `STILL_UNKNOWN` | 確かな証拠がない。何も変えない（保留を保つ） |
| `ALREADY_SETTLED` | 既に確定している（2回目は数えない） |

- 拒否する証拠（`ReconciliationEvidenceError`）：MAC が合わない、別の Attempt・Receipt・予約の報告、最終でない費用、実行報告でないもの。単なる Digest やモデルの判断は、証拠の型として受け付けない。今のファイルが期待する内容と一致するだけでは、過去の操作が実行されたとは断定しない（そのような証拠の型はない）。
- 永久に確認できない場合：オーナーが `declare_unresolved` で、Run を FAILED（`UNRESOLVED_EXTERNAL_EFFECT`、設計 A）にする。結果不明の保留はそのまま残す。
- 実際のプロバイダーへの請求の照会は前倒ししていない。

## 7. 停止と失効

- 停止は Journal（台帳）に残り、再起動や復旧で初期化されない。停止されていた Run は CANCELLING を経て CANCELLED になる。
- 古い Permit と Receipt は、開くときに失効する（`revoke_stale`）。失効した Permit の消費は拒否される（試験で確かめた）。
- 古い承認は再使用できない：中止された Attempt の ExactApproval は、その行為のハッシュに結び付いているので、新しい Attempt（新しい予約、新しいハッシュ）には使えない（試験で確かめた）。

## 8. 冪等性

`recover` を2回続けて実行すると、2回目は状態も予約も変えない（`changed=False`）。復旧の途中の異常終了（例外の注入、実際の SIGKILL）の後の `recover` は、途中から続けて、中断のない場合と同じ結果になる。

## 9. 一時ファイルの回収

- `recover` は、ファイルを消さない。
- `recover-temporaries`（オーナーの明示的な操作）は、WP08-R07 の設計で許された範囲だけを回収する：この作業木の書き込みのうち結果不明のもの（`recovery_receipts`）の Receipt を列挙し、ブローカーの `recover_temporaries` が、今のポリシーの信頼する木、ルートとマニフェスト、所有者、名前の形、Receipt のタグを書き込みのロックの下で確かめて、その一時ファイルだけを消す。
- どの Receipt にも当たらない一時ファイルは消さず、報告する。対象のファイルは消さない。結果不明の操作は、結果不明のまま（一時ファイルを消すことは、何かを証明も実行もしない）。

## 10. CLI の操作

| コマンド | 内容 |
|---|---|
| `recovery --home DIR [--json]`（最上位）、`recovery`（シェル） | 復旧が必要な Run、各 Attempt の規則と予定の処理、結果不明の Attempt と保留、回収の対象の Receipt。読むだけ（DB を変えないことを確かめた） |
| `recover` | オーナーの明示的な復旧。Run ごとの前後の状態を表示する |
| `reconcile <attempt>` | 証拠なしの照合（未実行の証明ができれば `NOT_EXECUTED`、できなければ `STILL_UNKNOWN`）。実行報告による照合は API だけ（CLI から証拠のファイルを読む機能は加えていない） |
| `declare-unresolved <run>` | `UNRESOLVED_EXTERNAL_EFFECT` で Run を失敗にする（保留は残る） |
| `recover-temporaries` | 9章 |

状態の確認（`status`、`recovery`）だけで、復旧を始めることはない。復旧を承認しただけで、結果不明の外部の操作を未実行として扱うことはない。

## 11. 障害の試験

| 種類 | 地点 | 結果 |
|---|---|---|
| 例外の注入（同じプロセスの中、段階2） | 予約の前（PROPOSED〜APPROVED）、予約の後、Intent の後、Permit の発行の後、Permit の消費の後、Receipt の取得の後、開始の直後、結果不明の後、復旧の途中の Journal の書き込みの失敗 | 表のとおり。復旧の途中の失敗の後、2回目の `recover` で、中断のない場合と同じ結果 |
| 例外の注入（段階3） | COMMIT の結果不明（開始の記録が書かれた後に、COMMIT が失敗を報告） | 記録された開始が決める：結果不明、RECONCILIATION_REQUIRED |
| 実際のプロセスの停止（SIGKILL、段階3） | モデルの呼び出しの最中 | 結果不明、保留を保持、RECONCILIATION_REQUIRED |
| 同上 | 承認した書き込みのブローカーの準備の最中（消費の後、開始の前） | 未実行の証明、0で照合、FAILED（`RECOVERY_FAILED`）。対象のファイルは作られていない |
| 同上 | ブローカーの書き込みの最中（開始の後） | 結果不明（予約は UNKNOWN）、RECONCILIATION_REQUIRED。対象のファイルは作られていない |
| 同上 | 結果を受け取った後、Journal に保存する前 | 結果不明、保留を保持、RECONCILIATION_REQUIRED |
| 同上 | オーナーの `recover` の最中 | 新しいプロセスの `recover` が続けて終える。3回目は変化なし |

ファイルの確定（rename）の前後での停止のうち、「前」は上の「書き込みの最中」で確かめた。rename の後・Journal の記録の前の停止は、ブローカーの内部の地点なので、この段階では例外の注入でも子プロセスでも試していない（WP-08 の試験 `test_wp08_write.py` の、子プロセスの停止の試験を参照。未検証の項目に残す）。

## 12. 試験の数

| 時点 | 件数 |
|---|---|
| 起点（`88ab790`） | 1937 |
| 段階1（`b23b0ac`） | 1983（+46） |
| 段階2（`2f4f9c1`） | 2028（+45） |
| 段階3 | 2038（+10） |

8種類の検査が、すべて成功した。`uv.lock` は変えていない（新しい依存関係はない）。

## 13. 保証の範囲と未検証の事項

保証する（試験で確かめた）：

- Rebuild は決定的で、何も書かない。不正なイベントを拒否する。
- ProjectionRepair は投影だけを直し、冪等で、正本の破損は直さずに拒否する。
- 未実行を証明できない Attempt は結果不明のまま、保留を解放しない。結果不明の作業を自動で再実行しない。
- 不正な照合の証拠を拒否する。二重の解放と二重の計上を拒否する。停止と失効は再起動で消えない。古い Permit、Receipt、承認は使えない。
- 期限を超えた RECOVERING を RECONCILIATION_REQUIRED にする。時計の逆行で延びない。
- CLI の状態確認は副作用を持たない。

保証しない・未検証：

- 協調しない書き込み元（ADR-0008）。同じユーザーの直接の改変、DB を持つ者の一貫した書き換え。
- ブローカーの rename の後・Journal の記録の前の停止（11章）。
- 実際のプロバイダーの請求の照会、実行報告の永続化（報告はプロセスの中にしかなく、異常終了で失われる。CLI からは証拠を渡せない）。
- 期限と上限の運用の値（オーナーの決定を待つ）。
- 独立した証拠の検証器（Phase 1 は合成の受け入れ）。
- Windows、macOS、ネットワークのファイルシステム。
- 完全なバックアップと復元（Phase 4）。

残っている課題（変わらない）：AUDIT-02〜04、A-P6、CI（GitHub Actions は未実行）、OS の隔離、実データのための鍵付きのハッシュ。

## 14. 限定修正 WP12-R01〜R05（90/100 の条件付き合格の後）

起点：`b441b03`。回帰の試験 `tests/unit/test_wp12_fixes.py`（WP15-R07 の6件を含む）を先に書き、振る舞いの赤を確かめた（新しい引数がないことによる失敗のほか、作成時刻の修復、確定した書き込みと検証の不一致、時計の逆行の後の期限が、振る舞いとして失敗した）。

- **R01 復旧の対象：** `recover` は、対象の Run を明示的に受け取る（`run_ids`、CLI は `recover <run>` か `recover all`）。この Runtime のセッションが動かしている Run（`active`）は `RUN_ACTIVE` で拒否する（止めるなら正式な停止の経路）。復旧の再入は `RECOVERY_BUSY` で拒否する。未実行の証明は、その Receipt が以前のオーナーの epoch のもので、失効済み（一度も USED になっていない）ときだけ行う。今の epoch の未完の操作は、結果不明のまま残す。
- **R02 照合の原子性：** 新しい台帳の操作 `ReconcileOutcome` を、Attempt の OUTCOME_UNKNOWN → COMPLETED / FAILED_DEFINITE の遷移と同じトランザクションで行う（ペアリングで必須）。台帳が自分の記録で決める：未実行の証明は Receipt が REVOKED（USED でない）かつ以前の epoch のときだけ（費用0、結果 FAILED）。実行報告は MAC、Receipt、Attempt、予約、owner_epoch、PEP を照合し、費用は既存の信頼された料金計算（`_billed_cost`）で導き、使用量が FINAL で請求が完全でなければ拒否する（`report.final` だけでは判断しない）。費用だけを別に確定した後は、どの証拠でも結果を変えられない（`NOT_UNKNOWN`）。確定済みの結果は、別の証拠で変わらない（`ALREADY_SETTLED`）。
- **R03 未確定の費用と終了状態：** 割り当てだけの予約、保留、UNKNOWN、最終でない使用量（0でも）を未確定とする。終わった Attempt に残った割り当ては VOID にする。予約 ID があるのに予約の記録がなければ `RESERVATION_MISSING` で拒否する。未確定の費用を残したまま正常に CANCELLED や FAILED にしない。終わった Run に残った未完の操作や費用も、評価と照合の対象にする。キャンセルが求められていた Run は CANCELLED、停止された Run は FAILED（`OWNER_CANCELLED`）にする（停止の意図を失わない）。`changed` は実際に変えたときだけ True にする。
- **R04 期限：** 「今」は台帳の時刻の high-water（`observe_time` が、その時の壁時計を記録する）。状態に入った時刻は、台帳が状態の変化のときだけ記録する（`run_status_times`。同じ状態の別のイベントでは変わらない）。期限切れ（`escalated`）と通常の復旧を区別して報告する。常時の監視はなく、明示的な復旧のときに検査する契約とする（Phase 4 のスケジューラーは前倒ししない）。
- **R05 ProjectionRepair：** 投影の値は、すべて正本から導く。tenant、Task の上限、Step の種類は Journal から。作成時刻は、台帳が最初に見た時刻の記録（`entity_times`、正本）から。正本にない値は推測せず拒否する。開くときの検査も、投影の作成時刻を正本と照合する（壊れた時刻で時間の上限が緩まない）。正本の破損は、引き続き直さずに拒否する。
- Journal の形式を版 8 にした（`rt_steps.kind`、`entity_times`、`run_status_times`、`operation_results`）。版 7 以前は開かない（合成のデータだけなので移行は作らない）。


## 15. 独立品質レビューの修正 QR-01〜QR-03（Codex の独立品質レビュー、FAIL の後）

> 状態：**DRAFT**（再レビュー待ち）。起点 `98f5fd1`。QR-01 は `dc2da98`、QR-02・QR-03 は `d88165d`。独立品質レビューであり、包括的なセキュリティ監査ではない。

### 15.1 QR-01（HIGH）結果と費用の原子的な照合

- **問題：** 旧来のオーナーの操作 `Reconcile` は、結果不明（OUTCOME_UNKNOWN）の Attempt の予約を、費用だけで RECONCILED にできた。その後は `ReconcileOutcome` が `NOT_UNKNOWN` で拒否され、結果を確定する手段がなくなった。旧試験 `test_r02_a_cost_settled_apart_blocks_any_outcome` は、この問題のある動作を期待していた。
- **修正：**
  - `Reconcile`（費用だけ）は、Attempt の結果が確定している（COMPLETED、FAILED_DEFINITE）ときだけ受け付ける。それ以外は `OUTCOME_NOT_SETTLED` で拒否し、何も変えない。Attempt の状態は `rt_attempts`（イベントと同じトランザクションで書き、開くたびに Journal と照合する投影）から読む。
  - `ReconcileOutcome` は、既知の下限（部分的な報告の費用）を下回る費用を `BELOW_LOWER_BOUND` で拒否する。結果の digest は、すべての検査の後にだけ記録する。
  - 既存の正常な費用の調整（結果が確定した Attempt の、最終でない請求の照合）は残した。確定した費用より下、下限より下は、従来どおり拒否する。
  - 以前の版が残した「結果不明なのに費用だけ確定した」状態は、`RecoveryReport.conflicts` で報告し、`reconcile` は `COST_SETTLED_APART` で拒否する。復旧も照合も、その状態を直さない（オーナーの判断に残す）。CLI の `recovered` の出来事にも出す。
- **試験：** `tests/unit/test_qr01_reconcile.py`（9件）、`test_wp12_cli_and_faults.py` の実際の SIGKILL（照合のトランザクションの中、確定の前。子プロセスの停止点 `during_settle`）。旧試験2件の期待値を正しい契約に変えた（`test_wp12_fixes.py` の R02、`test_budget_ledger.py` の `test_unknown_outcome_holds_until_reconciled`）。
- **修正前の赤：** 拒否、保留の維持、下限、以前の状態の報告の4件が失敗（`DID NOT RAISE`、`AttributeError`）。原子的な確定、二重の計上の防止、トランザクションの中の障害、正常な費用の照合と下限、SIGKILL の6件は修正の前から成功した（特性確認）。

### 15.2 QR-02（MEDIUM）tenant の整合

- Journal のイベントの `tenant_id` を、Task・Run・Step・Attempt の tenant の正本とする。同じ実体が2つの tenant で記録されていれば `TENANT_MISMATCH`。親がない（`PARENT_MISSING`）、親と子の tenant が違う（`TENANT_MISMATCH`）も拒否する。
- `rt_tasks`・`rt_runs` の `tenant_id` を、通常の open と読み取り専用の open の両方で照合する（以前は照合していなかった）。
- ProjectionRepair は、Journal の連鎖そのものが矛盾していれば、投影を書き直さずに拒否する（正本の異常を隠さない）。投影の tenant の破損は Journal から直し、2回目は何もしない。

### 15.3 QR-03（MEDIUM）追加した表の位置付けと整合

| 表 | 区分 | 再導出 | 開くときに確かめること |
|---|---|---|---|
| `rt_tasks`、`rt_runs`、`rt_steps`、`rt_attempts` | 投影 | できる（イベントから） | 内容、tenant、親子（ProjectionRepair が書き直す） |
| `entity_times` | 正本（台帳の時計） | できない（イベントの時刻は別の時計） | Task と Run ごとに1行、孤立した行がない、台帳の high-water 以下、Run が Task より前でない、投影の作成時刻と一致 |
| `run_status_times` | 正本（台帳の時計） | できない | Run ごとに1行、孤立した行がない、今の Run の状態と一致、Run の作成時刻以上・high-water 以下。読み取り（`run_status_since`）も、状態が違えば拒否する |
| `operation_results` | 正本（MAC 付きの実行報告から。報告とバイトは保存しない） | できない | 既知の Attempt のもの、digest の形式、開始が記録された Attempt（Receipt が USED、状態が DISPATCH_STARTED・COMPLETED・FAILED_DEFINITE・OUTCOME_UNKNOWN）、完了した Run の検証の証拠が名指す読み取りの結果があり、観測の digest と一致 |

- **必要な結果の条件（既存の契約から導いた）：** すべての Attempt に結果の digest が要るのではない。必要なのは、完了した Run の `VerificationEvidence` が `read_attempt_id` で名指す読み取りだけ（WP15-R07 の完了の根拠）。
- **確かめられないこと（主張しない）：** `since_ms` と `created_at_ms` の正確な値（境界と状態だけを確かめる）、`result_digest` と実際のバイトの一致（バイトも報告も保存しない）。
- **記録の形式の変更は見送った：** `run_status_times` に状態に入ったイベントの位置（`run_seq`）を加えれば、どのイベントで状態に入ったかまで照合できる。しかし時刻の値そのものは再導出できず、Journal の版 9 への移行も要る。今回は境界の検査にとどめ、候補として残す。
- ProjectionRepair は、これらの正本を推測で作らない（欠けていれば拒否し、何も書かない）。
- **試験：** `tests/unit/test_qr02_qr03_consistency.py`（23件）。修正前は18件が失敗（`DID NOT RAISE`）。作成時刻の欠落、親の付け替え、修復の冪等性、同じ状態のイベント、整った状態の open の5件は修正の前から成功した（特性確認）。

### 15.4 FINAL-R05：並行実行を入れる前に必要な Recovery API の契約

今の公式の CLI の経路では、修正の必要は見つからなかった。CLI の命令は1つのプロセスの1つのセッションで順に実行される。`recover` は `session.continues` で自分の生きている Run を拒否する。別のプロセスは OS のロックで排除される。並行実行（同じ Runtime の中で複数の Run やスレッドを動かすこと）を入れる前には、次の契約が必要である（今は未実装）。

1. 再入の防止を、状態機械の単位（今の `_RECOVERING`、プロセスの中だけ）から Run の単位にし、Journal に記録する所有（Lease）にする。
2. 「生きている Run」の判定（`active`）を、メモリの中の呼び出し元の申告ではなく、Journal に記録された Lease から導く。
3. `reconcile` と `_settle` は、今と同じく1つのトランザクションで、台帳が Attempt と予約の状態を再検査する（今の `NOT_UNKNOWN`、`OUTCOME_NOT_SETTLED` を保つ）。
4. 復旧と、同じ Run の新しい Step の作成が競合しないこと（WP04-R03 の検査をトランザクションの中で行う）。
5. 期限の検査（A.I17）を、明示的な復旧の時だけでなく、停止とタイムアウトの経路から呼べるようにするかを、Phase 2 の前に再評価する（6章の A.I17）。

### 15.5 sqlite_ledger.py の技術的負債（今回は大きな分割をしない）

- 約 3,500 行に、予算、権限（Permit・Receipt・承認・停止）、投影、完了の根拠、照合、開くときの検査、修復が集まっている。
- **将来の分割の案：** `ledger_cost`（予約と照合）、`ledger_authority`（決定、承認、Permit、Receipt、停止）、`ledger_mirror`（投影、修復、`entity_times`・`run_status_times`）、`ledger_completion`（完了の根拠、書き込みの順序）、`ledger_consistency`（開くときの検査）。
- **原子性を保つ条件：** 分割した後も、すべての操作は呼び出し元の1つのカーソル（同じ SQLite のトランザクション）の中で動き、モジュールが自分で接続やトランザクションを開かない。Journal の追記（`append_with_ledger`）が、`mirror`、`apply_op`、イベントの挿入を1つのトランザクションで呼ぶ順序を変えない。モジュールの間の依存は一方向にし、import-linter の契約で強制する。
