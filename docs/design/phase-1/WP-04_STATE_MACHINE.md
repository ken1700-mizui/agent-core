# WP-04：Task、Run、Step、Attempt の状態機械（実装の記録）

> 文書の状態：**DRAFT**（ChatGPT によるコードレビューを待っている）　作成日：2026-11-26
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

## 11. 未解決の事項

- 本物の認可（WP-09）がないため、Phase 1 の本体だけでは、認可された状態へ進めない（意図したとおり）。
- 復元は、テナントの全体を読み直す。イベントの数に比例して時間がかかる（チェックポイントは WP-12 以降で検討する）。
- `RECOVERING` と `RECONCILIATION_REQUIRED` の期限（A.I17）は、まだ強制していない（WP-12）。
- GitHub Actions の上での実行（CI は未実行）。
