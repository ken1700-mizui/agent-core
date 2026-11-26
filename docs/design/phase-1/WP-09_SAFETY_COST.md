# WP-09：Safety、Cost、Permit と Receipt（実装の記録）

> 文書の状態：**DRAFT**（ChatGPT のレビューを待っている）　作成日：2026-11-26
>
> - この文書は、WP-09（A：Safety、B：Cost、C：Permit・Receipt・停止）で実装した内容と、**設計資料に書かれていなかったために、WP-09 で決めたこと**を記録する。
> - 正本の規則は `E_INTEGRATION.md` の28章に従う。この文書は、設計資料を上書きしない。
> - 対象は、管理された Linux の合成の環境、単一の Runtime、Fake と Replay、XTU だけ。実 API の費用は 0 ドル。外部への通信はない。
> - 実装：
>   - `src/agent_core/types/safety.py`、`types/cost.py`、`types/ledger.py`（型と、台帳の操作）
>   - `src/agent_core/safety/`（`pdp.py`、`descriptor.py`、`approval.py`、`sag.py`、`paths.py`）
>   - `src/agent_core/cost/rules.py`
>   - `src/agent_core/adapters/sqlite_ledger.py`（台帳と権限の表、SQL）
>   - `src/agent_core/adapters/sqlite_journal.py`（トランザクションの境界）

## 1. 構成

```
agent_core.runtime
        ↓
agent_core.adapters（sqlite_journal、sqlite_ledger） | identity | tool | observability
        ↓
agent_core.safety | agent_core.cost        （純粋な判断。入出力なし。互いに import しない）
        ↓
agent_core.ports → agent_core.types
```

- `safety` と `cost` は、新しい層として import-linter の契約に加えた。SQLite も adapters も import できないことを、契約と試験で確かめた。
- SQLite の台帳は adapter（`sqlite_ledger`）にある。Journal のトランザクションの中で、`safety` と `cost` の純粋な判断を使う。
- 依存関係は加えていない。HMAC は標準ライブラリの `hmac` と `hashlib` を使う。

## 2. Safety の認可の契約（WP-09A）

### 2.1 型

| 型 | 内容 |
|---|---|
| `Policy` | オーナーの静的な、版付きのポリシー。ツールの固定（`tool_id@version@manifest_hash` と `artifact_hash`）、許す効果、承認が要る効果とツール、モデルの種類（`FAKE`、`REPLAY` だけ。`LIVE` は型で拒否）、拒否リスト、オーナーが固定した合成の作業ツリー、Permit と承認の期限の上限。**既定値はない**（どの値も明示する。候補の値を、承認済みの値とは扱わない） |
| `RunGrant` | 1つの Run に許す範囲（C1 の4.2）。ツール、読み書きの範囲、モデルのプロファイル、1回の操作の費用の上限、`policy_version`、`access_epoch_at_grant`、期限。実行中の Run の Grant は置き換えない（広げない） |
| `ActionDescriptor` | 3章 |
| `PolicyDecision` | 許すかどうか、拒否の理由（すべて）、`action_hash`、ポリシーと Grant のハッシュ、承認が要るかどうか |
| `ExactApproval`、`SignedApproval` | 4章 |
| `StopEntry`、`SafetyContext` | 停止の状態と、判定に使う今の信頼する状態 |
| `DenyReason`、`ApprovalReason`、`PermitReason` | 決まった理由のコード |
| `SecretKey`、`LedgerKeys` | 鍵。`repr`、`str`、pickle、記録の書き出しに、値が出ない |

### 2.2 PDP（`safety.pdp.decide`）

信頼する入力（Policy、RunGrant、信頼する登録簿の `ToolRegistration`、信頼する経路で作った `ActionDescriptor`、台帳から読んだ `SafetyContext`）だけから決める純粋な関数である。

- **デフォルトは拒否。** すべての検査に通ったときだけ許す。外れた理由は、すべて報告する。
- 拒否するもの：
  - 未登録のツール、未承認の版、ポリシーや Grant に固定されていないツール、未知の操作。
  - `builtin` 以外のツール、接続先のあるツール、Sandbox のプロファイル（任意のシェル）。
  - 実 API（`LIVE`）、Grant にないモデル。
  - マニフェストや Grant の範囲を外れる資源、拒否リストの資源、今削除されている資源。
  - Grant を超える費用の上限、期限切れの Grant と Descriptor、Grant より長く生きる Descriptor。
  - テナント、Task、Run の不一致。
  - 古い `policy_version`、`access_epoch`、`owner_epoch`。
  - 全体、テナント、Run のどれかが停止している場合。全体の項目がない場合（不整合）。
  - 鍵付きの HMAC の方式（Phase 1 では拒否する）。
- **モデルの主張を信じない：** 権限を主張する入力そのものがない。Tool Registry の提案の `requires_approval` は参考で、PDP は承認が要るかどうかを自分で決める（試験で、提案の値を書き換えても結果が変わらないことを確かめた）。
- **合成のデータの信頼：** `synthetic` のラベルだけでは信頼しない。資源を扱う操作は、オーナーが固定した合成の作業ツリーの digest が要る（`UNTRUSTED_WORKSPACE`）。最小限の保存の関門（`safety.sag.admit_synthetic`）も同じ規則である。署名した作業ツリーと、実際の実行物の照合は、後の WP がこの固定の一覧で強制できる構造にした。

## 3. ActionDescriptor と ExactApproval

### 3.1 ActionDescriptor（C1 の7.1、E の38章）

- 信頼する経路（`safety.descriptor.tool_action`、`model_action`）で、実際の内容から作る。
  - マニフェストと実行物のハッシュは、登録簿の `ToolRegistration` から取る。
  - 引数のハッシュは、正規化した入力のバイト列から計算し直す。入力は、正規化した形でなければ拒否する。
  - 提案（`ToolProposal`）があれば、その値をすべて照合する。提案のハッシュは使わない。違えば拒否する。
  - モデルの呼び出しの `request_hash` は、要求の記録のバイト列から計算する。
- 結び付けるもの：
  - Run、Step、Attempt。
  - ツールの ID、版、`manifest_hash`、`artifact_hash`。
  - 引数のハッシュ、対象の資源と読み書き、`request_hash`、効果の分類、作業ツリー。
  - `policy_version`、`access_epoch`、`cost_cap`、`reservation_id`、期限。
- `action_hash` は、ドメインのタグ付きの、正規化した Descriptor の SHA-256。**合成のデータだけに使う**（`HashScheme.SYNTHETIC_SHA256`）。機密のデータ用の鍵付きの HMAC（`KEYED_HMAC_SHA256`）は、型として区別し、Phase 1 では拒否する。

### 3.2 ExactApproval

- `action_hash` に結び付く。1回だけ（`max_uses` は型で 1 に固定）。承認者の役割、期限、使用の回数、取り消しの状態を確かめる。
- **信頼するオーナーの経路だけ：** オーナーの経路（`OwnerApprovalChannel`）が、オーナーの鍵で MAC を付ける。モデル、提案、Runtime は、この経路を持たない。
- 型付きの承認のオブジェクトを作っただけでは、権限は生じない。台帳が MAC を確かめて記録し、承認の確認、Permit の発行、消費、Receipt の取得のたびに、今の有効性を確かめる。
- 引数、実行物、対象の資源、費用の上限、Attempt が変われば `action_hash` が変わり、承認は当てはまらない（試験で、内容、費用の上限、Attempt を変えた3つの場合を確かめた）。
- Phase 1 では、`OWNER` の役割の承認だけを受け付ける。ScopedApproval と SpendApproval は実装していない。

## 4. 予算の台帳と状態遷移（WP-09B）

### 4.1 保存

- Journal と同じ SQLite の DB に表を置く。新しい依存関係も、別の DB も加えていない。
- **Journal の形式の版を 3 にした。** 版 3 は、WP-09B の台帳の表と、WP-09C の権限の表を持つ。版 1 と版 2 のファイルは拒否し、移行しない（Phase 1 のファイルは合成の試験のデータだけ）。WP-03 の記録の、形式の版の記述は、この章が優先する。

### 4.2 状態

```
ID_ALLOCATED → RESERVED | VOID
RESERVED     → CONSUMED | RELEASED
CONSUMED     → ACTIVE | UNKNOWN | RELEASED（生きている PEP が「送っていない」と報告した場合だけ）
ACTIVE       → USAGE_REPORTED | UNKNOWN
USAGE_REPORTED → RECONCILED
UNKNOWN        → RECONCILED
RECONCILED、RELEASED、VOID：終わり
```

- 遷移は表（`cost.rules.RESERVATION_TRANSITIONS`）で明示し、それ以外は拒否する。
- 調整の記録は、状態とは別のエントリーとして追記する。
  - `CORRECTION`：正の訂正だけ。記録した費用を下げる訂正は、オーナーが要るので実装せず、型で拒否する。
  - `UNEXPLAINED`：Run、Task、実験の新しい予約を止める。
  - `OVERRUN`：実額が予約を超えたとき。実額をそのまま記録し、Run、Task、実験、期間の新しい予約を止める。
- **使った費用と、押さえている額を分ける。** 予約ごとに `hold`（押さえている額）と `spent`（記録した費用）を持つ。範囲の使用量は `hold + spent` の合計である。解放は `hold` を 0 にするだけで、`spent` は消さない。

### 4.3 AUDIT-01（E の38章）の強制

1. `CONTENT_FIXED`：`reservation_id` を確保する（`ID_ALLOCATED`）。予算も実行の権限も生じない（`hold` は 0）。
   - `reservation_id` と `attempt_id` は、どちらも UNIQUE。
2. ActionDescriptor に、その ID を含める（`action_hash` に入る）。
3. `POLICY_ALLOWED`：台帳が、トランザクションの中で PDP をもう一度実行し、Descriptor を記録する。必要なら `APPROVED` で承認を確かめる。
4. `BUDGET_RESERVED`：同じ ID で、1回だけ有効にする（`ID_ALLOCATED → RESERVED`）。次を確かめる。
   - 記録した判定、承認、`cost_cap`。
   - 必要額 ≦ 予約額 ≦ `cost_cap`。必要額は、台帳が見積もりの入力（合成の価格とトークンの上限、または固定の額）から計算し直す。見積もりの入力そのものは、Runtime の側が渡す（13章）。
5. `INTENT_RECORDED`：Intent を記録する。予約が `RESERVED` であることが要る。
6. `PERMIT_ISSUED`：Permit を発行する（6章）。
7. `PERMIT_CONSUMED`：消費するときにも、すべての一致を確かめる。

- ID の使い回し、二重の予約、ほかの Attempt への付け替えは拒否する。Runtime（WP-04）と台帳の両方で拒否することを、試験で確かめた。
- 期限切れの `ID_ALLOCATED` は `VOID` にする。`VOID` の ID は二度と使えない。

### 4.4 予算の階層

- 範囲：Step、Run、Task、ユーザー、テナント、合成の期間（`synthetic-YYYY-MM`）、実験（ある場合）。呼び出しと Attempt の上限は、Descriptor の `cost_cap` で決める。
- 予約を有効にするとき、**関係するすべての範囲を、同じトランザクションで確かめる**（D.V10）。上限が設定されていない範囲があれば拒否する（fail closed）。
- 新しい Run を作っても、Task の全体の使用量と上限は、そのまま数える（試験で確かめた）。
- 上限は、オーナーの経路（MAC 付き）で明示して設定する。既定値はない。

### 4.5 金額

- XTU だけ。最小の単位（micro）の整数。浮動小数点数は使わない。
- 64 ビットの上限を検査する。足し算、掛け算、合計を、オーバーフローを検査しながら計算する（`CostOverflowError`。E.Z9）。
- 予約の額は切り上げる。
- 負の数は型で拒否する。不明な使用量を、確定した 0 とは扱わない（`known_cost` は `None` を返す）。
- **Replay の記録元の費用を、今回の費用として数えない：** 使用量の報告（`ReportUsage`）は今回の呼び出しの Usage だけを持ち、記録元の Usage の欄がない。Replay の今回の Usage は、確定した 0（WP06-R04）。

### 4.6 予約と照合

- 予算が足りなければ、予約は失敗し、実行しない（その Attempt は `ABORTED_BEFORE_DISPATCH` にできる）。
- 予約、消費、費用の報告、照合を、別の状態として区別する。
- 結果が分からない場合は、押さえた額をそのまま保つ（`UNKNOWN`）。照合するまで解放しない。
  - 途中の使用量は、下限（`spent_lower`）として記録する。
  - 最終でない報告、費用が不明な報告は、確定した費用として扱わない。
- 送っていないことが確かな場合だけ、解放する。
  - `RESERVED` は、`ABORTED_BEFORE_DISPATCH` と一緒に解放できる。
  - `CONSUMED` の解放には、生きている PEP の「送っていない」報告が要る（E.Z6）。
  - `ACTIVE` と `UNKNOWN` は解放しない。
- 二重の報告（同じ `usage_event_id`）、二重の解放を拒否する。
- 照合で、記録した確定の費用より下げることは拒否する（オーナーが要る。未実装）。
- **最終の使用量だけでは、追加の費用がないと確定できない場合**（E の4.2）：Phase 1 の合成の価格表には、ツールの料金や最小の料金がないため、最終で既知の費用なら、残りを解放する。価格のずれの上乗せは 0 とした。実際の BillingScheme での判断は、Phase 3A 以降に残した。
- 外部の請求との照合と、独立した台帳は、Phase 3A 以降。

### 4.7 金銭以外の上限

- 強制するもの：モデルの呼び出しの回数、ツールの実行の回数、再試行の回数、総作業単位、Run の経過時間。範囲は Run と Task。上限が設定されていなければ拒否する。
- 型と会計の契約だけのもの（まだ実行されない機能）：文脈の組み立て直し、計画の立て直し、記憶の操作。
- 数えた回数は、解放しても減らさない（試みた回数として数える）。

## 5. Journal とのトランザクションの境界

- **台帳の操作は、型付きのデータ（`LedgerOp`）である。** 権限は持たない。台帳は、同じトランザクションの中で、自分の行に対してすべての条件を確かめ直す。時刻は、台帳の信頼する時計から取る。
- **組の強制：** Attempt の次の遷移は、対応する台帳の操作と一緒でなければ、Journal に記録できない。逆に、遷移に結び付く操作は、その遷移なしには受け付けない。
  - `CONTENT_FIXED`：ID の確保。
  - `POLICY_ALLOWED`：判定の記録。
  - `APPROVED`：承認の確認。
  - `BUDGET_RESERVED`：有効化。
  - `INTENT_RECORDED`：Intent。
  - `PERMIT_ISSUED`：発行。
  - `PERMIT_CONSUMED`：消費。
  - `DISPATCH_AUTHORIZED`：Receipt の取得。
  - `DISPATCH_STARTED`：実行の開始。
  - 普通の `append` も、この組の検査を通る。そのため、迂回の経路はない。
  - これで、状態だけ進んで台帳が残ること、その逆を防ぐ。
- 1つの `BEGIN IMMEDIATE` の中で、次の順に行う。どれかが失敗すれば、すべて取り消す。
  1. Journal の期待する head を照合する。
  2. Runtime の所有者（`owner_epoch` と instance）を照合する。
  3. 台帳の操作を行う。
  4. イベントを書く。
  5. 所有者をもう一度照合する。
  6. コミットする。
- トランザクションの中で、別のトランザクションを始めない。
- 台帳の拒否（`CostError`、`SafetyError`）は、何も残さない。Runtime は、状態を変えずに続けられる。コミットの結果が分からない場合は、Runtime を読み直す必要がある（WP-04 のまま）。
- **WP-04 の試験の変更：** SQLite の Journal で Attempt を承認済みの状態まで進めていた試験2件（`test_rebuild_from_sqlite_after_restart`、`test_runtime_events_survive_the_sqlite_journal_verification`）は、本物の台帳の経路で進めるように直した。試験の目的（再起動の後の復元、Journal の検証）は変えていない。

## 6. Permit と Receipt（WP-09C）

### 6.1 発行（`IssuePermit`）

次のすべてを、トランザクションの中で確かめたときだけ発行する。

- 記録した Descriptor について、今の Safety の判定をもう一度行い、許されること（停止、拒否リスト、epoch、期限を含む）。
- 必要な ExactApproval が、今有効であること。
- Descriptor が記録済みであること。
- 同じ Attempt の予約が `RESERVED` で、`action_hash` が同じで、額が `cost_cap` 以下であること。
- Intent が記録済みであること。
- 消費する主体は Broker だけ（Phase 1 には Gateway がない）。
- 期限は、ポリシーの上限以内。Descriptor の期限を超えない。
- Permit の中身：`permit_id`、テナント、Run、Attempt、`action_hash`、`reservation_id`、消費する主体、`access_epoch`、`owner_epoch`、`revocation_gen`、`policy_version`、期限、MAC。
- **MAC：** P1 だけが持つ Permit 用の鍵で、HMAC-SHA256 を計算する。鍵は、モデル、ログ、Journal の本文、監査、試験の報告に出さない（試験で、DB のファイルに鍵の値がないことを確かめた）。本格的な鍵の管理は後の段階。

### 6.2 原子的な消費（`ConsumePermit`）

1つの `BEGIN IMMEDIATE` の中で、次を一体として行う。

1. Journal の期待する head の照合。
2. Runtime の所有者の照合。
3. 示された Permit の MAC と、記録との一致、実際の操作の `action_hash` の一致。
4. Permit が `ISSUED` で、使われていないこと。
5. 消費する主体、期限、`access_epoch`、`owner_epoch`、`revocation_gen`、停止の状態、拒否リスト。
6. 必要な ExactApproval の有効性。
7. 同じ Attempt の有効な予約。
8. Intent。
9. Permit を `CONSUMED` にする。
10. 承認の使用の回数を 1 増やす。
11. 予約を `CONSUMED` にする。
12. Receipt を `ISSUED` で記録する。
13. 対応する Journal のイベントと、必須の監査の記録を書く。
14. コミット。

- すべての検査は、書き込みの前に行う。外れた理由は、すべて報告する。
- **一度だけ：** 書き込むのは P1 だけで、`BEGIN IMMEDIATE` で直列になる。そのため、同じ Permit を同時に使っても、成功するのは1回だけである。

### 6.3 Receipt

- Attempt、Permit、予約、消費する主体、PEP のインスタンス、失効の世代、期限に結び付く。
- `DISPATCH_AUTHORIZED` で、`ISSUED → CLAIMED` に1回だけ進める（`ClaimReceipt`）。示された Receipt が記録と一致することを確かめる。そのうえで、PEP のインスタンス、失効の世代、期限、停止の状態、承認の有効性も確かめる。
- `DISPATCH_STARTED` で `USED` にする。同じ Receipt は二度使えない。
- 型付きのオブジェクトを偽造しても、記録と一致しなければ使えない。

### 6.4 WP-08 との契約

- PEP（WP-08 のファイルの Broker）が受け取るのは `DispatchAuthorization`（Receipt、`action_hash`、記録した Descriptor）である。
- **実際の実行の直前に**、`SqliteJournal.verify_dispatch(receipt_id, pep_instance_id, action_hash)` で、信頼する記録と照合する。照合する内容は、Receipt が `CLAIMED` であること、PEP のインスタンス、失効の世代、停止の状態、期限、承認、`action_hash` である。
- ファイルの操作そのものは実装していない。

### 6.5 必須の監査の記録

- 発行、消費、Receipt の取得、承認、停止と再開、失効、ポリシーと Grant の導入を、同じトランザクションの中で、`audit_log` の表に書く。
  - 書く内容：ID、決まったコード、epoch。本文、引数、鍵は入れない。
  - 記録は、直前の記録のハッシュを含む連鎖にする。
- 監査の記録が書けなければ、トランザクションの全体を取り消す（試験で確かめた）。
- **監査の書き手の再帰を拒否する**（`AuditRecursionError`）。監査の書き手は、台帳も SAG も呼ばない。
- WP-11 の JSON Lines のログは、この必須の監査の代わりにしない。
- 監査の署名、外部の証人、外部への写しは、後の段階に残した。

## 7. 停止と失効

- **停止の範囲：** Run（K1）、テナント（K2）、全体（K3）。`StopScope` は、オーナーの経路（MAC 付き）だけで受け付ける。停止すると、次のことを行う。
  - その範囲の `stop_seq` を増やし、`STOPPED` にする。
  - `revocation_gen` を 1 増やす。テナントと全体の停止では、`access_epoch` も増やす。
  - 発行済みで未使用の Permit と、未使用の Receipt を `REVOKED` にする。
- **競合：** 停止、失効と、発行、消費は、同じ DB の上で、どちらかが必ず先になる。
  - 停止が先なら、発行も消費も、Receipt の取得も拒否する。
  - 消費が先なら、その操作は「停止の前に始めた操作」として残る。予約は `CONSUMED` のまま保ち、Receipt は無効にする。
  - 実際の SQLite で、消費と停止を同時に走らせ、使える Receipt が残らないことを確かめた。
- `revocation_gen` を永続化する。失効の原因（権限の失効、拒否リスト、承認の取り消し、停止、ポリシーの変更）のどれでも、1 増やす。
- **再起動だけでは、停止を解除しない**（DB に残る。試験で確かめた）。
- 解除（`ResumeScope`）は、オーナーの経路だけで、`stop_seq` の一致が要る。解除の後も、古い Permit と Receipt は使えない。
- 停止の前に始めた操作を、自動で「未実行」とはみなさない。
- **再起動の後：** `owner_epoch` が変わる。そのため、古い Permit はすべて無効（`STALE_OWNER`）。PEP のインスタンスも変わるので、古い Receipt は使えない。消費済みで結果の分からない予約は、`RecoverInFlight` で `UNKNOWN` にし、押さえた額を保つ。
- **Lease の期限だけで、失効が強制されたとは判定しない：** Phase 1 の PEP はプロセスの中の合成のもので、Lease も IPC もない。失効は、Receipt の取得と `verify_dispatch` が、同じ DB の今の世代を確かめることで強制する。E の3.3 の `REVOCATION_RECORDED` と `REVOCATION_ENFORCED` の2段階、別のプロセスの Lease の管理は、後の段階に残した。

## 8. AUDIT-01 の試験の結果

| 条件 | 試験 | 結果 |
|---|---|---|
| ID の確保だけでは実行できない | `test_allocated_id_holds_nothing_and_grants_nothing`、`test_an_allocated_id_alone_cannot_get_a_permit` | 成功（Runtime と台帳の両方で拒否） |
| 予約に失敗したら実行しない | `test_a_failed_reservation_means_no_execution`、`test_amount_must_cover_...`、`test_every_level_is_checked_...` | 成功 |
| 内容、費用の上限、Attempt を変えると、承認が無効 | `test_changing_content_cost_or_attempt_voids_the_approval`（3つの場合） | 成功 |
| 古い ID の再使用、二重の予約、付け替えを拒否 | `test_reservation_ids_cannot_be_reused_or_moved`、`test_activation_happens_once`、`test_expired_allocations_are_voided_and_never_used` | 成功 |
| 発行と消費のときに、承認、Descriptor、予約が一致 | `test_a_changed_operation_cannot_consume`、`test_a_descriptor_that_is_not_the_fixed_one_is_not_decided`、`test_an_approval_for_other_content_does_not_apply` | 成功 |

AUDIT-01 は、**Phase 1 の合成の範囲での実装と試験**を記録した。実 API での検証（Phase 3A 以降）は含まない。

## 9. 障害の注入と競合の試験

**区別：** 次の3つの種類は、別のものとして報告する。

| 種類 | 試験 | 確かめたこと |
|---|---|---|
| **実際のプロセスの停止**（子のプロセスが `os._exit` で終わる。後処理なし） | `test_process_stop_right_after_consumption` | コミットの直後に止まっても、Journal と台帳が一致する。新しい PEP は古い Receipt を使えない。予約は `UNKNOWN` で保つ |
| 同上 | `test_process_stop_inside_the_consumption_transaction` | トランザクションの途中で止まると、何も残らない。古い Permit は `STALE_OWNER` |
| **実際の SQLite での競合**（別の接続、スレッド、`BEGIN IMMEDIATE`） | 予約の同時の有効化、Permit の同時の消費、Receipt の同時の取得、消費と停止の同時の実行 | どれも成功は1回だけ。使える Receipt は残らない |
| **例外の注入**（同じプロセスの中で、関数を差し替える） | 監査の失敗、途中の失敗、監査の再帰、COMMIT の失敗、COMMIT の後の失敗 | すべて取り消し、または一致した状態。結果が分からないときは、読み直しが必要 |

- 競合の試験は、Journal の OS のロック（単一の書き手）を意図して迂回し、台帳の関数を別の接続から直接呼ぶ。そのため、DB の層での保証を確かめるものである。通常の構成では、2つ目の書き手は OS のロックで拒否される（WP-03）。

## 10. 試験の数

| 段階 | 追加 | 合計 |
|---|---|---|
| 限定修正（WP06-R01〜R04、WP11-R04） | 38 | 1275 |
| WP-09A | 59（Safety 53、import-linter の契約 6） | 1334 |
| WP-09B | 48（規則 5、台帳 43） | 1382 |
| WP-09C | 43（Permit 41、プロセス 2） | 1425 |

## 11. 設計資料にない判断（WP-09 で決めたこと）

| 判断 | 理由 |
|---|---|
| 台帳の操作を型付きのデータにし、遷移との組を Journal が強制する | 状態と台帳のずれを、構造で防ぐため |
| 判定を、台帳がトランザクションの中でもう一度行う | 呼び出し側の判定の結果を信じないため |
| 承認とオーナーの操作の MAC に、オーナーの鍵を使う（台帳も同じ鍵で確かめる。対称鍵） | Phase 1 は単一のホスト。別の装置での署名は後の段階 |
| 停止の状態を、Journal と同じ DB の表に置く（停止の状態のファイルと署名は作らない） | Phase 1 には別のプロセスの PEP がない。消費と同じトランザクションで確かめられる |
| Permit の期限は、ポリシーの上限と Descriptor の期限の短い方 | 期限の順序（C1 の4.2）を保つため |
| Phase 1 の消費する主体は Broker だけ | Gateway がないため |
| 時刻は、台帳の時計（壁時計）で確かめる | Phase 1 の PEP はプロセスの中にある。単調な時計と余裕による関門の確認は、後の段階 |
| ポリシーを変えたら、`access_epoch` と `revocation_gen` を増やす | 狭める変更か広げる変更かを見分けず、安全側に倒すため |
| 価格のずれの上乗せを 0 にした | 合成の価格表だけのため |

## 12. 現段階の保証の範囲

- 合成の環境、単一の Runtime、プロセスの中の合成の PEP、Fake と Replay、XTU で、次のことを試験で確かめた。
  - デフォルトの拒否。
  - AUDIT-01 の一致の確認。
  - 予算の階層の同時の検査と、整数の計算。
  - Permit の1回だけの原子的な消費。
  - Receipt の1回だけの使用。
  - 停止と失効と、発行、消費の順序。
  - 再起動の後の古い Permit の拒否。
- 「試験で確かめた」とは、上の試験の範囲での確認であり、形式的な証明ではない。

## 13. 後の段階に残した、確かめていないこと

- 外部への送信の関門（Egress Gateway）、OS の隔離、別のプロセスの PEP と Lease の管理、`REVOCATION_ENFORCED` の2段階（Phase 3A）。
- 停止の状態のファイルと署名、Gateway と Broker の独立した確認（C1 の11.4）。
- 監査の署名、外部の証人、外部への写し。
- 本格的な鍵の管理（鍵の生成、保管、更新）。今は、呼び出し側が鍵を渡す。
- `permit_id` を 128 ビットの乱数にすること。今は ID の生成器の値で、MAC が偽造を防ぐ。
- ScopedApproval、SpendApproval、Run ごとの承認（D5）。
- 機密の検査、暗号化、完全な SAG（C2 の4.1）。
- 署名した合成の作業ツリーと、実際の実行物のハッシュの照合（WP-08 以降）。
- Runtime の Run の `CANCELLING` と、Run の停止（K1）の自動の結び付け。今は、オーナーが `StopScope(RUN)` を出す。
- Task の記録の `budget_total` と、Task の範囲の上限の自動の結び付け。今は、オーナーが上限を設定する。
- 価格表とツールの見積もりを台帳に登録し、見積もりの入力そのものを台帳の側で決めること。今は、Runtime の側が渡す入力から必要額を計算し直すだけである。
- 実際の BillingScheme での「追加の費用がない」ことの判断、外部の請求との照合、独立した台帳（Phase 3A 以降）。
- 月をまたぐ Run（AUDIT-02）。Phase 1 は合成の期間の ID だけ。
