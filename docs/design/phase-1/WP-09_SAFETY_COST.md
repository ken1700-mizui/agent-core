# WP-09：Safety、Cost、Permit と Receipt（実装の記録）

> 文書の状態：**PROVISIONAL**（レビューは 88/100 の条件付き合格。WP09-R01〜R07 を修正した。14章）　作成日：2026-11-26
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

## 14. レビューの指摘への対応（WP09-R01〜R07）

この章は、1〜13章の記述より優先する。特に、6.4 の `verify_dispatch`（読み取りだけの確認）はなくした。また、`ActivateReservation` の見積もり、`AllocateReservation` の範囲の指定、`ReleaseReservation` の `not_dispatched_ref`、`ReportUsage` の自由な Usage も、なくした。

### 14.1 TDD の記録

- **WP-09A では、TDD の順序を守れなかった。** 実装を試験より先に書き、Red は、後からパッケージを外して確かめただけである。この記録は残す。
- 今回は、3つの段階のそれぞれで、次の順に進めた。
  1. 再現の試験を先に書く。
  2. Red を確かめる。第1段階と第2段階は、新しい型がないことによる収集の失敗。第3段階は、エラーの型だけを先に加え、改ざんした DB が開けてしまうことによる、振る舞いの失敗（13件中11件）。
  3. 実装する。
- 実装の前の欠陥は、今のコードを調べて記録した（下の表の「修正前」）。

### 14.2 各指摘の修正

| 指摘 | 修正前（実際のコードで確かめた） | 修正後 | 再現の試験 |
|---|---|---|---|
| R01 見積もり | `ActivateReservation.estimate` を呼び出し側が渡し、台帳はそれを計算し直すだけだった | 見積もりの根拠（`cost_basis`）を ActionDescriptor（版 2）に入れた。必要額は、台帳が根拠と、オーナーが導入した合成の価格表から計算する | `test_the_caller_cannot_supply_the_estimate`、`test_tool_required_amount_comes_from_the_trusted_manifest`、`test_model_required_amount_comes_from_the_owner_price_table`、`test_a_model_call_cannot_use_a_tool_estimate`、`test_the_cost_basis_must_match_the_manifest_and_the_policy`、`test_required_amount_is_checked_again_at_issue` |
| R02 Task の予算 | Task の `budget_total` を見ていなかった。実行の時間の窓を設定し直すと、開始の時刻が今に戻った。照合で、訂正の額が消えた | 次の3つを行う。(1) Task の予算と上限を、Journal から台帳に写す。(2) 台帳の上限は、Task の値を超えない。(3) 費用を「元の費用」と「調整の額」に分けた | `test_the_task_budget_total_bounds_a_larger_ledger_cap`、`test_task_non_monetary_caps_bound_the_ledger`、`test_setting_the_run_window_again_does_not_reset_elapsed_time`、`test_reconciliation_keeps_earlier_corrections`、`test_a_correction_beyond_the_budget_is_an_overrun` |
| R03 実行の開始 | `verify_dispatch` は読み取りだけだった。Receipt がないと `_use_receipt` が何もせずに戻った。Receipt に `owner_epoch` がなかった | 合成の実行ゲートを置いた。Receipt の取得と実行の開始を、トランザクションの中で確定する | `test_a_receipt_is_bound_to_the_current_owner`、`test_a_missing_receipt_record_refuses_the_start`、`test_one_receipt_starts_execution_once`、`test_the_actual_operation_must_match_at_execution`、`test_a_stop_right_after_the_claim_prevents_the_start` |
| R04 未実行の証明と費用 | 任意の Digest で、消費済みの予約を解放できた。呼び出し側が自由な Usage を報告できた | ゲートの MAC 付きの報告だけを受け付ける。費用は台帳が計算する。未確定の費用があれば、正常に完了できない | `test_an_arbitrary_digest_does_not_release_a_consumed_reservation`、`test_a_forged_or_foreign_not_dispatched_report_is_refused`、`test_usage_needs_a_trusted_execution_report`、`test_the_cost_comes_from_the_synthetic_billing`、`test_run_and_task_cannot_complete_with_unsettled_costs` |
| R05 停止、キャンセル、期限 | `CANCELLING` で Run を停止しなかった。実行の時間の窓は、予約のときだけ確かめた。時計の逆行で、期限が延びた。オーナーの MAC 付きの操作を、再送できた | 14.4 | `test_cancelling_a_run_stops_it`、`test_lowering_a_budget_cap_revokes_outstanding_permits`、`test_an_expired_run_window_refuses_consumption`、`test_a_clock_moving_back_does_not_extend_a_permit`、`test_an_old_owner_batch_cannot_be_replayed` |
| R06 範囲と対象 | 予算の範囲（Task、ユーザー、期間）と `action_kind` を、呼び出し側が渡した。対象の資源も呼び出し側が渡し、空にできた | 14.5 | `test_budget_scopes_come_from_trusted_state`、`test_targets_are_derived_from_the_arguments`、`test_a_descriptor_with_emptied_targets_is_not_recorded`、`test_a_descriptor_outside_the_attempt_chain_is_not_recorded`、`test_the_action_kind_comes_from_the_descriptor` |
| R07 復元の整合 | Journal と台帳が食い違う DB でも開けた | 開くときに確かめ、食い違えば開かない。古い所有者の未使用の Permit と Receipt を失効させる | `tests/unit/test_wp09_restart.py` の13件 |

### 14.3 信頼する見積もり（WP09-R01）

- **ツール：** 必要額は、登録簿の信頼する Manifest の `cost_estimate`。PDP は、Descriptor の根拠が Manifest と一致しなければ拒否する。
- **モデル：** 必要額は、次の値から台帳が計算する。
  - オーナーが導入した合成の価格表（`InstallPriceTable`。`status=test`。一度導入した版は変えられない）。Policy が、使う価格表を名指しする。
  - 対象のモデル。
  - 要求の記録と、描画した入力から確かめた上限。入力は描画した入力の UTF-8 のバイト数で、トークンの数の保守的な上限とする。出力は要求の `max_output_tokens`。
  - トークンの計測（WP-14）は前倒ししていない。
- 呼び出し側は、価格も必要額も渡せない。モデルの呼び出しに、ツールの根拠を使うことは、型で拒否する。
- 根拠（価格表がない、モデルの価格がない、判定の記録がない）がなければ、予約しない（`CostBasisMissingError`）。
- 必要額 ≦ 予約額 ≦ `cost_cap` を、予約、Permit の発行、消費で確かめる。

### 14.4 実行のゲート、報告、停止（WP09-R03〜R05）

- **ゲート**（`agent_core.runtime.execution_gate.SyntheticExecutionGate`）。プロセスの中の合成の PEP で、次の順に進む。
  1. `claim`：実際の操作（引数）から Descriptor を作り直し、記録した Descriptor と一致すれば、Receipt を `CLAIMED` にする。
  2. `start`：同じトランザクションで、Receipt を1回だけ `USED` にする。停止、失効の世代、所有者の epoch、ゲートのインスタンス、期限、経過時間、承認を、もう一度すべて確かめる。
  3. コールバックを1回だけ呼ぶ。渡す `DispatchAuthorization` は情報で、どこにも戻せない。
  4. `finish`：MAC 付きの結果の報告で記録する。コールバックが例外を出したら、結果不明にする（押さえた額は保つ）。
  - 停止はゲートのロックを通るので、同じプロセスの中では、開始の確定とコールバックの間に、停止が入らない。コールバックからのゲートの再入は、`GATE_BUSY` で拒否する。
  - Journal の SQLite の接続は1つのスレッドのものなので、ほかのスレッドからは使えない（安全側に失敗する）。
- **Receipt** に、消費したときの `owner_epoch` を入れた。再起動の後は、古い PEP の ID で示しても拒否する。Receipt の記録がなければ、開始しない。
- **未実行の報告：** ゲートが、P1 の鍵の MAC を付ける。次のすべてを満たさなければ、消費済みの予約を解放しない。
  - Receipt、Permit、Attempt、予約、今の所有者の epoch、ゲートのインスタンスに結び付くこと。
  - 開始されていない Receipt であること。
  - 報告が、まだ使われていないこと。
- **費用の報告：** ゲートの MAC 付きの結果の報告だけを受け付ける。
  - 費用は、台帳が合成の課金の規則で計算する。ツールは Manifest の見積もり。モデルは、既知のトークンの数 × 価格表の単価で、切り上げる。
  - 述べた費用が、計算した額と違えば拒否する。
  - 価格表の `final_usage_is_complete`（合成の BillingScheme）が真で、報告が最終のときだけ、残りを解放する。そうでなければ、押さえた額を保つ（`UNKNOWN`）。
- **照合、訂正、説明できない差額** は、オーナーの経路だけで受け付ける。
- **正常な完了の禁止：** 未確定の費用（`RESERVED`、`CONSUMED`、`ACTIVE`、`UNKNOWN`、または押さえた額がある）が残る Run と Task は、`COMPLETED` にできない。
- **停止とキャンセル：**
  - Run の `CANCELLING`（と終わりの状態）は、同じトランザクションで Run を停止する（K1）。キャンセルした Run は、再開できない。
  - 予算、回数、実行の時間の窓の上限を下げたら、その範囲の発行済みの Permit と Receipt を失効させる。
- **期限：** 実行の時間の窓と、Grant の期限を、発行、消費、取得、開始のすべてで確かめる。承認と Receipt の期限は、両方を守る。
- **時計：** 台帳は、使った最も遅い時刻を記録する（高水位）。壁時計が戻っても、期限は延びず、経過時間も戻らない。
- **オーナーの操作の再送：** オーナーの操作のまとまりに、増えていく番号（`owner_seq`）を付け、永続化した。同じものも古いものも、二度と受け付けない。承認は、`approval_id` で一度だけ記録できる（変えていない）。

### 14.5 範囲と対象の結び付け（WP09-R06）

- **Journal の写し：** Task の予算と上限、Run、Step、Attempt の親子と状態を、イベントと同じトランザクションで、台帳の表（`rt_*`）に写す。台帳は、呼び出し側の主張ではなく、この写しだけを読む。
- **予算の範囲：** 予約の ID の確保（`AllocateReservation`）は、範囲を持たない。
  - Task、Run、Step、テナントは、写しから取る。
  - ユーザー、合成の期間、実験は、Run の Grant から取る。
  - 呼び出し側は、別の範囲を選べない。
- **操作の種類**（`action_kind`）は、記録した Descriptor から取る。
- **対象の資源：** Manifest の入力のスキーマで、資源を指す欄（`FieldSpec.resource`。必須の文字列）に印を付ける。信頼する Descriptor の作り手が、その引数から対象を導く。
  - 資源を宣言したツールには、その欄が必須である。
  - 呼び出し側は対象を渡せず、空にもできない。
  - 実際のパスの安全な解決は、WP-08 で行う。
- **判定の記録：** 台帳は、次の2つを確かめる。
  - Descriptor が、その Attempt の Journal の親子（Step、Run、Task、テナント）に属すること。
  - 実際の内容（引数、または要求の記録と描画した入力）から作り直した Descriptor と、完全に一致すること。
- 承認、Intent、Permit、Receipt は、同じ操作（`action_hash`、Attempt、予約）に属することを、それぞれの時点で確かめる。

### 14.6 復元の時の整合（WP09-R07）

Journal を開くとき、Journal の検証の後、所有者の epoch を取る前に、同じトランザクションで次を確かめる。1つでも外れれば、開かない（`LedgerInconsistencyError`）。ファイルは変えない（試験で、バイト列が同じことを確かめた）。

1. Journal のイベントから求めた、Task、Run、Step、Attempt の状態と、台帳の写し。
2. Attempt の状態と、予約の状態の対応。消費の後は、Permit と Receipt の結び付きも見る。
3. 消費した Permit と、Receipt の1対1の対応。
4. 承認の使用の回数と、その承認で消費した Permit の数。
5. 予約の、押さえている額、費用、調整の額と、台帳のエントリー。
6. 必須の監査の記録の、ハッシュの連鎖。連番の抜け（削除）と、内容の変更を見つける。
7. 停止の状態。全体の項目があること。`stop_seq` と停止の監査の数。状態と、最後の停止や再開の監査の一致。
8. 失効の世代と、その監査の記録。巻き戻りを見つける。

- 確かめた後、新しい所有者の epoch で、古い所有者の未使用の Permit と Receipt を、すべて失効させる（`RESTART_REVOKED`）。消費済み、開始済みの操作は、そのまま保つ（押さえた額は保ち、照合を待つ）。
- **主張しないこと：** これは、食い違いを見つけるだけで、直さない。DB を持つ者が、すべてを矛盾なく書き換えたことは、見つけられない。完全な Recovery Engine、独立した証人、バックアップは、後の WP と Phase に残した。

### 14.7 トランザクションと、実行のゲートの境界

| 境界 | 一体として確定すること | 一体にできないこと |
|---|---|---|
| Journal のトランザクション（`BEGIN IMMEDIATE`） | 次のすべて。どれかが失敗すれば、すべて取り消す。head と所有者の照合、写し、台帳の操作、監査、イベント、時計の高水位、オーナーの操作の番号 | — |
| 実行のゲート（プロセスの中のロック） | 開始の確定（`USED`）から、コールバックの呼び出しまで。停止も同じロックを通る | DB の記録と、実際の操作（OS のファイル操作など）。**完全に原子的にはできない** |
| 異常終了 | 開始の確定の直前に止まった場合、何も開始されていない（消費済みのまま。生きているゲートの報告がなければ、解放しない）。開始の確定の後に止まった場合、結果不明として扱う（`DISPATCH_STARTED` → 復旧で `UNKNOWN`。押さえた額は保つ） | 開始したかどうかを、後から確かめる方法はない（実際の操作は WP-08） |

- 実際のプロセスの停止の試験：子のプロセスで、開始を確定した直後に `os._exit` で止めた。親のプロセスで、次を確かめた。
  - `DISPATCH_STARTED`、Receipt の `USED`、予約の `ACTIVE` が残ること。
  - 未実行の報告を拒否すること（`ALREADY_STARTED`）。
  - 復旧で `UNKNOWN` になり、押さえた額が保たれること。

### 14.8 AUDIT-01 の再検証

| 条件 | 結果 |
|---|---|
| ID の確保だけでは、予算も実行の権限も生じない | 成功（変えていない） |
| 予約の額は、**信頼する必要額**以上、`cost_cap` 以下 | 成功。必要額の入力は、信頼する Manifest、またはオーナーの価格表と、確かめた上限から決める。呼び出し側は入力を渡せない（14.3） |
| 予約に失敗したら、実行しない | 成功 |
| 内容、費用の上限、Attempt、**見積もりの根拠**を変えると、`action_hash` が変わり、承認が無効になる | 成功（根拠が `action_hash` に入る） |
| 発行と消費のときに、承認、Descriptor、予約、必要額が一致する | 成功 |
| 古い ID の再使用、二重の予約、付け替えを拒否する | 成功 |

AUDIT-01 は、Phase 1 の合成の範囲で、実装と試験を記録した。実 API での検証（Phase 3A 以降）は含まない。

### 14.9 Journal の形式の版

- 形式の版を **5** にした。版 5 で、写し、価格表、回数の使用、費用の分割、使用済みの未実行の報告、時計の高水位、オーナーの操作の番号を加えた。
- 版 1〜4 のファイルは拒否する。移行も削除もしない（Phase 1 のファイルは、合成の試験のデータだけ）。
- ActionDescriptor は版 2 にした。版 1 は受け付けない。

### 14.10 試験の数

| 段階 | 追加 | 合計 |
|---|---|---|
| 第1段階（R01・R02・R06） | 22 | 1447 |
| 第2段階（R03・R04・R05） | 25（ゲートなど 24、プロセスの停止 1） | 1472 |
| 第3段階（R07） | 13 | 1485 |

- 既存の試験のうち、台帳の行を直接作っていた単体の試験を書き直した。費用と解放の報告がゲートの MAC 付きの報告だけになったため、Journal とゲートの経路を通すようにした。確かめる内容は変えていない。
- 開くときの検査で、改ざんした DB が開けなくなったため、次の試験を直した。
  - 2件は、改ざんする値を、台帳のエントリーと揃えた。オーバーフローの検査と、発行のときの必要額の再確認に届かせるためである。
  - 3件は、期待する状態を直した。再起動の後の、古い Permit と Receipt を `REVOKED` とした。
- 実際の SQLite の競合の試験と、実際のプロセスの停止の試験は、残して、加えた。

### 14.11 残る制約（主張しないこと）

- 実行のゲートは、プロセスの中の合成のものである。DB の記録と実際の操作の原子性はない。開始の直前と直後の異常終了は、結果不明として保守的に扱う。
- ゲートの報告の MAC と、P1 の Permit の鍵は、同じプロセスの中にある。別のプロセスの PEP、Lease、OS の隔離、停止の状態のファイルの署名は、後の段階。
- 合成の課金の規則と価格表だけ。実際の請求との照合と、独立した台帳は、Phase 3A 以降。
- トークンの上限は、描画した入力のバイト数による保守的な値。実際の計測は WP-14。
- 対象の資源のパスの安全な解決（リンク、ルートの外）は、WP-08。
- 開くときの整合の検査は、食い違いを見つけるだけで、矛盾のない書き換えは見つけられない。
- 承認とオーナーの操作の MAC は、対称鍵。別の装置での署名は、後の段階。
