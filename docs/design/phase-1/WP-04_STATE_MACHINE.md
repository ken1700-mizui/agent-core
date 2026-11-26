# WP-04：Task、Run、Step、Attempt の状態機械（実装の記録）

> 文書の状態：**PROVISIONAL**（レビューは 92/100 の条件付き合格。WP04-R01〜R06 を修正した。12・13章）　作成日：2026-11-26
>
> - この文書は、WP-04 で実装した内容と、**設計資料に書かれていなかったために、WP-04 で決めたこと**を記録する。
> - 正本の規則は `E_INTEGRATION.md` の28章に従う。この文書は、設計資料を上書きしない。
> - 実装：`src/agent_core/runtime/state_machine.py`（純粋な判断）、`src/agent_core/runtime/service.py`（Journal への記録）、`src/agent_core/types/transitions.py`（遷移の記録の型）、`src/agent_core/types/events.py`（イベントの版 2）、`src/agent_core/ports/evidence.py`（証拠の検証の接続口）。

## 1. 構成

```
agent_core.runtime    状態機械（純粋）と、その記録のサービス
        ↓（ports と identity と types だけを使う。adapters と SQLite は使わない）
agent_core.adapters | agent_core.identity
        ↓
agent_core.ports      JournalPort、EvidenceVerifier など
        ↓
agent_core.types      記録、遷移の記録、イベント
```

- import-linter の契約で強制する：層の契約に `agent_core.runtime` を最上位として加えた。`runtime` から `sqlite3`、`fcntl`、`agent_core.adapters` への import は禁止（`core-independent-of-storage`）。
- **判断と永続化を分けた。**
  - `state_machine.py`：今の状態と入力から、遷移してよいかを決める純粋な関数。I/O を持たない。
  - `service.py`：判断 → 証拠の確認 → Journal への記録 → 状態の更新、の順に行う。

## 2. 遷移表

**Run（A の6章）**

| 今の状態 | 遷移してよい先 |
|---|---|
| `CREATED` | `QUEUED`、`FAILED`、`ABORTED_SAFETY` |
| `QUEUED` | `RUNNING`、`RECOVERING`、`FAILED`、`ABORTED_SAFETY` |
| `RUNNING` | `WAITING_TOOL`、`WAITING_APPROVAL`、`PAUSED_BUDGET`、`VERIFYING`、`CANCELLING`、`RECONCILIATION_REQUIRED`、`RECOVERING`、`FAILED`、`ABORTED_SAFETY` |
| `WAITING_TOOL`、`WAITING_APPROVAL`、`PAUSED_BUDGET` | `RUNNING`、`CANCELLING`、`RECOVERING`、`FAILED`、`ABORTED_SAFETY` |
| `VERIFYING` | `COMPLETED`、`RUNNING`、`RECOVERING`、`FAILED`、`ABORTED_SAFETY` |
| `RECOVERING` | `QUEUED`、`RECONCILIATION_REQUIRED`、`FAILED`、`ABORTED_SAFETY` |
| `CANCELLING` | `CANCELLED`、`RECONCILIATION_REQUIRED`、`FAILED`、`ABORTED_SAFETY` |
| `RECONCILIATION_REQUIRED` | `RUNNING`、`CANCELLED`、`FAILED`、`ABORTED_SAFETY` |
| `COMPLETED`、`CANCELLED`、`FAILED`、`ABORTED_SAFETY`（終了状態） | **なし**（`FAILED` と `ABORTED_SAFETY` へも移れない。A.I7） |

- `COMPLETED` へは、`VERIFYING` からだけ移れる（試験で、表の全体から確かめた）。

**Attempt（E の3.1。E の38章で読み替えたもの）**

| 今の状態 | 遷移してよい先 |
|---|---|
| `PROPOSED` | `VALIDATED`、`ABORTED_BEFORE_DISPATCH` |
| `VALIDATED` | `CONTENT_FIXED`、`ABORTED_BEFORE_DISPATCH` |
| `CONTENT_FIXED` | `POLICY_ALLOWED`、`ABORTED_BEFORE_DISPATCH` |
| `POLICY_ALLOWED` | `APPROVAL_PENDING`（承認が必要な経路）、`BUDGET_RESERVED`（承認が要らない経路）、`ABORTED_BEFORE_DISPATCH` |
| `APPROVAL_PENDING` | `APPROVED`、`ABORTED_BEFORE_DISPATCH` |
| `APPROVED` | `BUDGET_RESERVED`、`ABORTED_BEFORE_DISPATCH` |
| `BUDGET_RESERVED` | `INTENT_RECORDED`、`ABORTED_BEFORE_DISPATCH` |
| `INTENT_RECORDED` | `PERMIT_ISSUED`、`ABORTED_BEFORE_DISPATCH` |
| `PERMIT_ISSUED` | `PERMIT_CONSUMED`、`ABORTED_BEFORE_DISPATCH` |
| `PERMIT_CONSUMED` | `DISPATCH_AUTHORIZED`、`OUTCOME_UNKNOWN`、`ABORTED_BEFORE_DISPATCH` |
| `DISPATCH_AUTHORIZED` | `DISPATCH_STARTED`、`OUTCOME_UNKNOWN`、`ABORTED_BEFORE_DISPATCH` |
| `DISPATCH_STARTED` | `COMPLETED`、`FAILED_DEFINITE`、`OUTCOME_UNKNOWN` |
| `OUTCOME_UNKNOWN` | `COMPLETED`、`FAILED_DEFINITE`（照合の証拠があるときだけ） |
| `COMPLETED`、`FAILED_DEFINITE`、`ABORTED_BEFORE_DISPATCH`（最終の状態） | なし |

**Step（WP-04 で確定。PROVISIONAL から変えた）**

| 今の状態 | 遷移してよい先 |
|---|---|
| `PLANNED` | `IN_PROGRESS`、`CANCELLED` |
| `IN_PROGRESS` | `COMMITTED`、`FAILED`、`RECONCILIATION_REQUIRED`、`CANCELLED` |
| `RECONCILIATION_REQUIRED` | `COMMITTED`、`FAILED`、`CANCELLED` |
| `COMMITTED`、`FAILED`、`CANCELLED`（終了状態） | なし |

- Step の種類は `MODEL_CALL`、`TOOL_CALL`、`VERIFICATION` のまま確定した。
- Step の全体の状態と、個々の Attempt の状態は、別のものとして扱う。Step の中の操作の段階は、Attempt が持つ。

**Task（WP-04 で確定）**

| 今の状態 | 遷移してよい先 |
|---|---|
| `CREATED` | `ACTIVE`、`CANCELLED`、`FAILED` |
| `ACTIVE` | `PAUSED`、`COMPLETED`、`FAILED`、`CANCELLED` |
| `PAUSED` | `ACTIVE`、`FAILED`、`CANCELLED` |
| `COMPLETED`、`FAILED`、`CANCELLED`（終了状態） | なし |

## 3. 遷移の前提条件

**Attempt**

| 遷移先 | 必要な証拠と条件 | 決まる欄 |
|---|---|---|
| `VALIDATED` | `ValidationEvidence` | — |
| `CONTENT_FIXED` | `ContentFixedEvidence`。予約の ID は、ほかの Attempt で使われていないこと（AUDIT-01。ID の確保だけで、予算も権限も生じない） | `action_hash`、`reservation_id` |
| `POLICY_ALLOWED` | `PolicyDecisionEvidence`（`action_hash` が一致する） | `approval_required` |
| `APPROVAL_PENDING` | `approval_required` が真であること | — |
| `APPROVED` | `ApprovalEvidence`（`action_hash` と `reservation_id` が一致する） | — |
| `BUDGET_RESERVED` | `ReservationEvidence`（確保した `reservation_id` と、`action_hash` が一致する）。`POLICY_ALLOWED` から直接来るときは、`approval_required` が偽であること（**承認を飛ばせない**） | — |
| `INTENT_RECORDED` | `IntentEvidence`（`action_hash` が一致する） | — |
| `PERMIT_ISSUED` | `PermitIssuedEvidence`（`action_hash` と `reservation_id` が一致し、`permit_id` が未使用） | `permit_id` |
| `PERMIT_CONSUMED` | `PermitConsumedEvidence`（`permit_id` が一致し、`receipt_id` が未使用） | `receipt_id` |
| `DISPATCH_AUTHORIZED`、`DISPATCH_STARTED` | `DispatchEvidence`（`receipt_id` が一致する） | — |
| `COMPLETED`、`FAILED_DEFINITE`、`OUTCOME_UNKNOWN`（送信の後） | `OutcomeEvidence`（`receipt_id` が一致する） | — |
| `COMPLETED`、`FAILED_DEFINITE`（`OUTCOME_UNKNOWN` から） | `ReconciliationEvidence`（照合した結果が、遷移先と一致する） | — |
| `ABORTED_BEFORE_DISPATCH` | `AbortEvidence`。`PERMIT_CONSUMED` と `DISPATCH_AUTHORIZED` からは、生きている PEP の「送っていない」という報告（`not_dispatched_ref`）が必要（E の4.1） | — |

- Run が動いている（`RUNNING`、`WAITING_TOOL`、`WAITING_APPROVAL`）ときだけ、Attempt は先に進める。結果（最終の状態と `OUTCOME_UNKNOWN`）は、`CANCELLING`、`RECONCILIATION_REQUIRED`、`RECOVERING` でも受け取る。
- 証拠が要らない遷移に、証拠を付けることも拒否する。
- **Permit の消費などは、列挙の値を変えるだけでは成り立たない。** それぞれ、型の付いた証拠と、2つの確認（ID とハッシュの一致、`EvidenceVerifier` による確認）が必要である。
- **型の付いた証拠があることは、認可が成り立ったことを意味しない。** 本物の確認（承認、予約、Permit の MAC など）は WP-09 で実装する。Phase 1 の本体には、証拠を受け入れる `EvidenceVerifier` の実装がない。そのため、試験の外からは、認可された状態へ進めない。

**Attempt の再試行**

- 失敗した Attempt を再び使うことはない。最終の状態からは、どこへも移れない。
- 再試行には、新しい `attempt_id` の Attempt を作る。作れるのは、次のすべてを満たすときだけ。
  - Step が `IN_PROGRESS` で、Run が `RUNNING` であること。
  - それまでの Attempt が、すべて最終の状態であること（**`OUTCOME_UNKNOWN` が残っていれば、作れない。** A.I4）。
  - 最後の Attempt が `COMPLETED` でないこと。

**Step**

| 遷移先 | 条件 |
|---|---|
| `IN_PROGRESS` | Run が `RUNNING` |
| `COMMITTED` | Run が `RUNNING` か `RECONCILIATION_REQUIRED`。Attempt がすべて最終の状態で、最後の Attempt が `COMPLETED` |
| `FAILED` | Run が `RUNNING` か `RECONCILIATION_REQUIRED`。Attempt がすべて最終の状態 |
| `RECONCILIATION_REQUIRED` | `OUTCOME_UNKNOWN` の Attempt がある |
| `CANCELLED` | Run が `RUNNING` か `CANCELLING`。Attempt がすべて最終の状態 |

**Run**

| 遷移先 | 条件 |
|---|---|
| `QUEUED`、`RUNNING` | Task が `ACTIVE` |
| `VERIFYING` | Attempt がすべて最終の状態で、Step がすべて終了状態 |
| `COMPLETED` | `VERIFYING` から。`VerificationEvidence` が合格で、Task の完了条件（`done_criteria`）を、過不足なく満たしている。成果物のハッシュが、期待した値と一致する。ApplyPlan は `COMMITTED` か「不要」。Attempt はすべて最終の状態（結果不明も、送信中もない）。Step はすべて終了状態 |
| `RUNNING`（`VERIFYING` から） | 不合格の `VerificationEvidence` |
| `CANCELLED` | Attempt がすべて最終の状態で、Step がすべて終了状態。`RECONCILIATION_REQUIRED` からは、`CANCEL` の解決が必要 |
| `RECONCILIATION_REQUIRED` | `ReconciliationTrigger`。理由が `OUTCOME_UNKNOWN` のときは、結果が分からない Attempt があること |
| `RUNNING`（`RECONCILIATION_REQUIRED` から） | `RESUME` の解決。結果が分からない Attempt が残っていないこと |
| `QUEUED`（`RECOVERING` から） | 結果が分からない Attempt が残っていないこと（あれば `RECONCILIATION_REQUIRED` へ） |
| `FAILED`、`ABORTED_SAFETY` | `Termination`（理由） |

- **未確定の予約と費用：** 費用の台帳は WP-09 で実装する。WP-04 では、「最終の状態でない Attempt」（予約した後で、まだ決着していないもの）が残っていないことで代える。

**Task**

| 遷移先 | 条件 |
|---|---|
| `COMPLETED` | Run が1つ以上あり、最後の Run が `COMPLETED` で、すべての Run が終了状態 |
| `CANCELLED`、`FAILED`、`PAUSED` | すべての Run が終了状態 |
| Run の追加（`start_run`） | Task が `ACTIVE`（`CREATED` なら、同じイベントで `ACTIVE` にする）。開いている Run がないこと（1つの Task に、同時に1つの Run）。新しい Run の `parent_run_id` は、直前の Run（引き継ぎ） |

- **Task の全体の予算と上限は、元に戻らない。**
  - `budget_total` と `caps_total` は、Task の識別の欄として扱う。`evolve` でも変えられない。
  - Run を作り直しても、Task の予算と上限は同じものが残る（E の9章）。
  - Run ごとの残りの計算は、WP-09 の費用の台帳で行う。

## 4. 結果不明とキャンセル

- **結果不明（`OUTCOME_UNKNOWN`）：**
  - 成功にも失敗にもしない。
  - 同じ Step で、新しい Attempt を作れない（自動で再実行しない）。
  - 解決には、照合の証拠（`ReconciliationEvidence`）が必要（WP-12 の Recovery が出す）。
  - Run と Step は、`RECONCILIATION_REQUIRED` へ移る。照合の証拠がなければ、`RUNNING` に戻れない。
  - 結果不明が残っていても `FAILED` にはできる（「永久に確認できない」の受け入れ。理由は `UNRESOLVED_EXTERNAL_EFFECT`。A の6.1）。`COMPLETED` と `CANCELLED` にはできない（A.I16）。
- **キャンセル：**
  - `CANCELLING` と `CANCELLED` は別の状態である。
  - `CANCELLING` の間に届いた Attempt の結果は、記録する（遷移の記録の `late` が真になる。A の6章の `LATE_RESULT`）。ただし、処理を進めるためには使わない（Step を `COMMITTED` にできず、新しい Attempt も作れない）。
  - 結果が分からない操作が残っていれば、`CANCELLED` にできない。`RECONCILIATION_REQUIRED` へ移す。

## 5. Journal との接続

- **イベントの形（版 2）：** `type` は `runtime.transition`、`schema_version` は 2。`payload` に `RuntimePayload`（1つ以上の遷移の記録）を入れる。
  - 遷移の記録には、前と後のスナップショット、操作（作成、状態の変更、Run の追加）、証拠、`late` を入れる。どれも ID、ハッシュ、列挙、真偽、整数だけで、**本文や秘密は入れない。**
  - ラベルは `synthetic`（Phase 1 は合成のデータだけ）。
- **版の互換性：**
  - 版 1 のイベント（WP-03 の形）は、`payload` を持たない。版 1 の書き出しには、`payload` の鍵を出さない。そのため、版 1 のバイト列とハッシュは変わらない（試験で確かめた）。
  - 版 1 に `payload` があれば拒否する。版 2 で `payload` がなければ拒否する。未知の版は拒否する。
  - Journal（WP-03）の検証は、版 2 のイベントにもそのまま効く（ハッシュの計算は、イベントの全体に対して行う）。
- **記録の順序（`service.py`）：**
  1. 純粋な関数で遷移を判断する（遷移表、前提条件、証拠の一致）。失敗すれば、何も書かない。
  2. `EvidenceVerifier` で証拠を確かめる。
  3. Journal に1つのイベントとして追記する（複数の遷移は、1つのイベントで原子的に記録する）。
  4. **Journal が保存したイベントを返した後で、そのイベントを状態に畳み込む**（`rebuild` と同じ `apply_event` を使う）。書き込みに失敗すれば、状態は変わらず、スナップショットも返さない。
- **古い状態と二重の更新：** 呼び出し側が渡すスナップショットが、今の状態と完全に一致しなければ拒否する（`StaleStateError`）。同じ遷移を2回行うと、2回目は古い状態になるので拒否される。`evolve` で作ったスナップショットも、正式な遷移の代わりにはならない（今の状態と一致しない）。
- **Step の `run_seq`：** Step を作るイベントの、Journal の中の `run_seq`。作るときに、Run の次の位置を予測して入れ、畳み込むときに、イベントの `run_seq` と一致することを確かめる。一致しなければ、状態が Journal と食い違ったとして止まる（`StateDivergenceError`）。

## 6. 状態の復元

- `rebuild(events)` は、イベントを `tenant_seq` の順に畳み込む。同じイベントからは、いつも同じ状態になる（決定的）。
- 各イベントの遷移は、記録された `after` をそのまま使わずに、**同じ規則で計算し直して、完全に一致することを確かめる。** そのため、Journal の中のイベントが、許されない遷移や、ほかの欄の変更を含んでいれば、検出できる。
- 次のものは、無視せずに `StateRebuildError` で止める。
  - 未知のイベントの種類、版 2 でないもの、`payload` がないもの。
  - 許されない遷移、記録と計算の不一致。
  - 同じイベントの繰り返し、順序の入れ替え、`tenant_seq` の飛び。
  - `run_id` と遷移の不一致、`run_seq` の飛び、複数のテナントのイベント。
- `RuntimeStateMachine.load` は、Journal から全体を読んで復元する。SQLite の Journal を閉じて開き直した後も、同じ状態になることを確かめた。
- **後続の WP に残したもの：** 本格的な Rebuild（メモリの中の折りたたみの全体）、Replay、ProjectionRepair（A の2.2、E の2.4）。

## 7. 設計資料との対応

| 設計 | 実装 |
|---|---|
| A の6章（Run の状態遷移） | 2章の Run の表 |
| A の3章、A の15章（Step と Attempt の対応） | Step の表と、Attempt の表 |
| E の3.1（Attempt の状態）、E の38章（予約の ID の確保と有効化。AUDIT-01） | Attempt の表と、`CONTENT_FIXED` と `BUDGET_RESERVED` の前提条件 |
| E の4.1（送っていないことの報告） | `ABORTED_BEFORE_DISPATCH` の前提条件 |
| E の7.3、29.5（完了の検証、ApplyPlan） | `COMPLETED` の前提条件 |
| E の9章（Task の上限は Run を作り直しても戻らない） | Task の識別の欄と、Run の追加の条件 |
| A.I4（結果不明を自動で再実行しない）、A.I7（終了状態から遷移しない）、A.I16（結果不明が残れば完了もキャンセルもしない） | 3・4章 |
| E の2.1（Journal が正本） | 5・6章 |

## 8. 設計資料にない判断（WP-04 で決めたこと）

| 判断 | 理由 |
|---|---|
| Task と Step の状態の表（2章） | WP-02 の PROVISIONAL を確定した |
| `PAUSED_BUDGET` から `CANCELLING` を許す | A の6章の図は `WAITING_*` だけだが、予算の待ちでもキャンセルできるべきため |
| `RECOVERING` へ移れるのは、`QUEUED`、`RUNNING`、`WAITING_*`、`PAUSED_BUDGET`、`VERIFYING` から。`RECOVERING` からは `QUEUED`、`RECONCILIATION_REQUIRED`、`FAILED`、`ABORTED_SAFETY` へ | A の6章の図から読み取った（`CANCELLING` の再開は、WP-12 で扱う） |
| 1つの Task に、同時に開いている Run は1つ | 初期は単一のエージェント（オーナーの方針） |
| Attempt に `approval_required` の欄を加えた | 承認が必要な経路と要らない経路を区別し、承認を飛ばせないようにするため。`POLICY_ALLOWED` で一度だけ決まる |
| イベントの版 2 と `payload` | 状態を復元するのに必要な、型の付いたメタデータを Journal に残すため（5章） |
| 結果の受け取りを許す Run の状態 | 4章 |
| 未確定の予約と費用は、最終でない Attempt で代える | 費用の台帳は WP-09 |
| `EvidenceVerifier` の接続口 | 型の付いた証拠と、実際の認可を区別するため |

## 9. 後続の WP への引き継ぎ

| 項目 | 送り先 |
|---|---|
| 承認、予約、Permit の本物の確認（`EvidenceVerifier` の実装、MAC、台帳） | WP-09 |
| 費用の台帳（未確定の予約と費用の厳密な計算、AUDIT-01 の `ID_ALLOCATED` → `RESERVED`） | WP-09 |
| 結果不明の照合（`ReconciliationEvidence` を出す）、`RECOVERING` の運用、期限による `RECONCILIATION_REQUIRED` | WP-12 |
| 修正のループの回数の上限（暫定で 3 回。E の7.3） | WP-15 |
| 本物の Verification（完了条件、試験、成果物の検査） | WP-15 |
| `access_epoch` と `revocation_gen` の強制（今は 0 を記録する） | WP-09 |

## 10. 検証の結果

手元の環境：Ubuntu 24.04、Python 3.12.3、SQLite 3.45.1。

- **TDD：** 試験を先に書き、モジュールがない状態で失敗する（収集のエラー）ことを確かめてから、実装した。
- **遷移表の全体：** Task、Run、Step、Attempt のすべての状態の組み合わせ（524 組）について、許す、または拒否することを確かめた。
- **前提条件の主な試験：**
  - 承認の経路の飛ばし。承認と予約の不一致。
  - 証拠の省略、種類の違い、検証の拒否。
  - Permit と Receipt の不一致。
  - 送信の後の中止。結果不明の照合。
  - 再試行で Attempt を使い回さないこと。結果不明が残る間は、新しい Attempt を作れないこと。
  - 検証の前の `COMPLETED`。完了条件の過不足。
  - 結果不明が残る間の `COMPLETED` と `CANCELLED`。キャンセル中に遅れて届いた結果。
  - 終了状態からの遷移。
  - Task の予算と上限が変わらないこと。1つの Task に、同時に1つの Run。
  - 古いスナップショット、二重の更新、`evolve` で作ったスナップショット、未知の実体。
- **Journal との接続：**
  - 書き込みに失敗したときに、状態が変わらないこと（例外の注入）。
  - 1つの変更が1つのイベントになること。
  - 復元の決定性。SQLite の Journal を開き直した後の復元。
  - 未知の種類、版 1 の偽装、許されない遷移、欄の変更、繰り返し、順序の入れ替え、`run_id` の不一致を、拒否すること。
  - 版 1 のイベントのバイト列とハッシュが変わらないこと。
- 試験は 995 件。8種類の品質の検査が、すべて成功した（STATUS.md）。

## 12. レビューの指摘への対応（WP04-R01〜R04）

この章は、3〜6章の記述より優先する。

### 12.1 古い Runtime の状態からの書き込みの防止（WP04-R01）

- **Journal の契約を変えた。** `append(draft, *, expected)` は、期待する head（`HeadExpectation`。`tenant_seq` と `head_hash`。イベントがなければ 0 と空）を**必須**とする。期待のない追記の手段はない。
  - SQLite の Journal は、`BEGIN IMMEDIATE` の中で、所有者の確認の後に、実際の head と比べる。一致しなければ、何も保存せずに `JournalHeadMismatchError`（致命的でない）を出す。事前の確認だけには頼らない（試験では、トランザクションの中で head が変わった場合も拒否されることを確かめた）。
  - 合成の Journal も、同じ契約に従う。
- **Runtime の状態と head を結び付けた。**
  - `load` で作った状態は、読んだ最後のイベントの `tenant_seq` とハッシュに結び付く。
  - 追記が成功し、保存されたイベントを畳み込んだ後にだけ、状態と head を新しくする。保存されたイベントが、期待した次の位置（`tenant_seq` が1つ先で、`prev_hash` が今の head）でなければ、食い違いとして止まる。
- **止まる場合：** 次の場合は、Runtime が `RuntimeReloadRequiredError` で以後の操作をすべて拒否し、Journal から読み直すことを求める。
  - head の不一致（古い状態）。このときは、`StaleStateError` として報告する。
  - 結果が分からない書き込み、想定しない例外。
  - コミットした後の畳み込みの失敗（`StateDivergenceError`）。
  - 例外：何も保存されていないことが確かなエラー（`JournalStorageError`、`JournalArgumentError`、`JournalClosedError`、`StaleEventEpochError`、`RunTenantMismatchError`）だけは、止めずにそのまま出す。
- **証拠の確認と書き込みの間の変化：** 証拠を確かめている間に、ほかの書き手が Journal を変えた場合も、head の比較で拒否される（試験で確かめた）。
- **WP-09 との関係：** head の比較は、書き込みのトランザクションの1つの段階である。WP-09 では、認可、Permit の消費、費用の台帳を、同じトランザクションに加えられる。

### 12.2 状態の復元とイベントの整合（WP04-R02）

- **空の状態からの完全な復元は、`tenant_seq` 1 から始まらなければならない。** 連続していて、1つのテナントだけで、ハッシュの連鎖（各 `prev_hash` が直前のイベントのハッシュ）になっていることも確かめる。
- 途中からの復元は、検証済みの Snapshot を使う、別の契約とする（WP-12 以降。今は提供しない）。
- **読んでいる間に Journal が変わる場合：** `load` は、最後のページの後で head を確かめる。読んだ件数より先に進んでいれば、続きを読む。最後に、head が、読んだ最後のイベントと一致することを確かめる。
- **Run の作成と Task への追加は、同じイベントで成り立たなければならない。** 作った Run と、追加した Run が、同じイベントの中で一致しなければ拒否する。そのため、孤立した Run は復元できない。親子の関係（`parent_run_id` は、Task の直前の Run）は、計画の段階で確かめる。
- Run を作るイベントの `owner_epoch` と、Run の `owner_epoch` が違えば拒否する。

### 12.3 結果不明と新しい Run（WP04-R03）

- **終了状態にしても、未解決の操作は消えない。** `FAILED` と `ABORTED_SAFETY` の Run にも、結果が分からない Attempt や、送信中の Attempt が残りうる。
- **保守的に拒否する（初期の実装）：** Task のどの Run にでも、最終の状態でない Attempt が残っていれば、次を拒否する。
  - 新しい Run の作成。
  - Task の `COMPLETED`、`CANCELLED`、`PAUSED`、`ACTIVE` への遷移。
  - `FAILED`（永久に確認できない場合の受け入れ）は、そのまま許す。
- **同じ Run の中での迂回の防止：** Run の中に、最終の状態でない Attempt があれば、新しい Step を作れない。
- **照合の記録を残せる：** 終了状態の Run は再開しない。ただし、その Attempt の決着（結果、`OUTCOME_UNKNOWN`、中止、照合）だけは、記録できる。
  - `PERMIT_CONSUMED` 以降の Attempt を先に進めること（`DISPATCH_AUTHORIZED` など）は、拒否する。
  - これらの記録は遅れた結果（`late`）として扱い、処理を進めるためには使わない。
  - 本格的な照合、費用の確定、期限は、WP-09 と WP-12 に残した。

### 12.4 キャンセルと遅れた結果（WP04-R04）

- **キャンセルの意図を、Run の記録に持たせた。** `cancel_requested` の欄を加えた。`CANCELLING` に入るときに真になり、二度と偽に戻らない。
  - 計画の関数が値を決め、復元のときにも計算し直して一致を確かめる。
  - `CANCELLING` と `CANCELLED` の Run は、この欄が真でなければ、記録として不正になる。そのため、Journal のイベントで意図を消すこともできない。
  - Journal から、そのまま復元できる。
- **キャンセルの意図がある Run では、次をしない。** `RECONCILIATION_REQUIRED` に移った後も同じ。
  - 新しい Attempt を作らない。
  - Attempt を先に進めない。
  - Step を `COMMITTED` にしない。
  - `RESUME` で `RUNNING` に戻さない。
- 遅れた結果（キャンセルの意図の後に届いた結果と、終了状態の Run への結果）は、記録して Attempt を決着させる。ただし、作業を進めるためには使わない。
- 結果が分からない Attempt が残っていれば、`CANCELLED` にしない（`CANCEL` の解決があっても同じ）。

### 12.5 検証

- 試験を先に書いた。古い Runtime の実装で、新しい試験（17 件）がすべて失敗することを確かめてから、直した。
- 同じ Journal を使う2つの Runtime の試験は、合成の Journal と SQLite の Journal の両方で行った。古い Runtime の操作は拒否され、不正なイベントは Journal に1件も残らない。SQLite の Journal を開き直すと、正しく検証され、復元できることも確かめた。

## 13. 追加の修正（WP04-R05、R06）

この章は、5・12章の記述より優先する。

### 13.1 イベントの版の互換性（WP04-R05）

- **調べたこと：** WP-04 の初版（`9f1a799`）のコードで、合成の版 2 のイベントを5件作り、`tests/fixtures/wp04_v2_events.jsonl` に、そのままのバイト列で残した（SHA-256 は試験で固定した）。
  - このイベントの Run には、`cancel_requested` がない。
  - 一方、`635e0b3` も、`cancel_requested` を持つ Run を、同じ版 2 として書いていた。**同じ版の番号が、開発の途中で2つの意味を持っていた。**
- **決めたこと（最小の方式）：**
  - **版 2 は廃止した。** 読むときは `superseded_schema_version` という理由を示して拒否し、別の意味に読み替えない。
  - 安全に復元できない理由：Run の形が違うだけでなく、遷移の規則（WP04-R01〜R04）も変わった。そのため、古いイベントを今の規則で畳み込んでも、当時の意味と同じにはならない。キャンセルの意図も、`CANCELLING` に入ったことから導けるが、規則が違うので、導いた状態を信用できない。
  - **今の Runtime のイベントは、版 3 とした。** Run は `cancel_requested` を持つ。キャンセルの意図は、各イベントの Run の記録から、そのまま復元する（導き出さない）。
  - 読める版は 1 と 3。版 1（WP-03 の形）のバイト列とハッシュは、変えていない。
  - 版 2 を書いたのは、開発の途中の合成のデータだけである。移行の仕組みは作らない。版 2 を含む Journal は、何も変えずに、開くことを拒否する（試験で確かめた）。合成の DB を消したり、初期化したりして、問題を隠すことはしない。

### 13.2 証拠の確認の間の状態の変化（WP04-R06）

- **1つの Runtime では、同時に1つの命令だけを実行する。** 命令が実行中に、ほかの命令が呼ばれたら、`RuntimeReentryError` で拒否する。証拠の検証からの呼び戻しや、ほかのスレッドからの呼び出しが、これに当たる。何も書かない。
- 遷移を計画した状態と head は、命令の始め（証拠の確認の前）に固定する。
  - 追記で期待する head には、この固定した値を使う。
  - 証拠の確認の後で、状態か head が変わっていれば、追記せずに `StaleStateError` とし、読み直しを求める。
- SQLite のトランザクションの中での head の比較は、そのまま保つ。
- コミットした後の畳み込みで、想定しない例外が出た場合も、読み直しを求める（`StateDivergenceError`。割り込みなどの例外は、そのまま出す）。

## 11. 未解決の事項

- 本物の認可（WP-09）がないため、Phase 1 の本体だけでは、認可された状態へ進めない（意図したとおり）。
- 復元は、テナントの全体を読み直す。イベントの数に比例して時間がかかる（チェックポイントは WP-12 以降で検討する）。
- `RECOVERING` と `RECONCILIATION_REQUIRED` の期限（A.I17）は、まだ強制していない（WP-12）。
- GitHub Actions の上での実行（CI は未実行）。
