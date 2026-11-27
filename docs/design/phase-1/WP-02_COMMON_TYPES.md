# WP-02：共通の型と Ports（実装の記録）

> 文書の状態：**PROVISIONAL**（レビューは 95/100 の条件付き合格。WP02-R01〜R03 を修正した）　作成日：2026-11-26
>
> - この文書は、WP-02 で実装した内容と、**設計資料に書かれていなかったために、WP-02 で決めたこと**を記録する。
> - 正本の規則は `E_INTEGRATION.md` の28章に従う。この文書は、設計資料を上書きしない。

## 1. モジュールの構成と、依存の方向

```
agent_core.adapters | agent_core.identity   （互いに import しない）
        ↓
agent_core.ports
        ↓
agent_core.types                            （標準ライブラリと Pydantic だけに依存する）
```

| モジュール | 中身 |
|---|---|
| `types.errors` | 型付きのエラー（`code` と `fatal` を持つ） |
| `types.ids` | ID の形式、種類ごとの ID の型、`SiteId` |
| `types.values` | 上限のある整数、ハッシュ、ラベル、予算、金銭以外の上限、Engine の版 |
| `types.base` | `FrozenModel`、`ImmutableRecord`、`StateSnapshot` |
| `types.records` | Task、Run、Step、Attempt、EventDraft、Event、EngineRequest と、状態の Enum |
| `types.serialization` | 正規化した JSON への書き出しと、厳密な復元 |
| `ports` | `Clock`、`RandomSource`、`IdRegistry`、`JournalPort`（基本の契約だけ） |
| `identity.generator` | `IdGenerator`（ID の生成と、重複のときの再試行） |
| `adapters.system` | `SystemClock`、`OsRandomSource`（標準ライブラリだけ） |

- import-linter の層の契約（`ports-and-adapters-layers`）で、この方向を強制する。逆向きの import と、層の間の循環は、試験で検出できることを確かめた。

## 2. ID の仕様（E の2.2）

- 形式：`<種類>_<サイト>_<値>`。例：`tsk_dev01_0000019a3c1e8f000123456789abcdef`（合成の値）。
- 値：128 ビットを、**小文字の16進数の32桁**で表す。上位48ビットはミリ秒の時刻、下位80ビットは OS の暗号用の乱数（`os.urandom`）。
  - **WP-02 で決めたこと：** 設計は文字列の表し方を決めていない。16進数を選んだ理由は、桁数が決まっていて、表し方が1通りで（大文字と小文字の揺れがない）、128 ビットと1対1に対応するため。
- サイト：`dev` か `eval` で始まり、その後に `[0-9a-z]` が1〜12文字。
  - **WP-02 で決めたこと：** 先頭の文字で、開発の側と評価の側を区別できるようにした（`SiteId.role`）。
- 種類（Phase 1 で使うものだけ）：`tsk`、`run`、`stp`、`att`、`evt`、`req`、`prm`、`rcp`、`rsv`、`tnt`、`grt`。
  - **WP-02 で決めたこと：** `req`（EngineRequest）、`tnt`（テナント）、`grt`（RunGrant）は、設計の例にない。`mem`、`del`、`exp` は、使う WP で加える。
- 種類の取り違え：`RunId` の欄に `TaskId` を渡すと、拒否する（`IdKindMismatchError`、Pydantic では `id_kind_mismatch`）。
- 時刻の範囲：生成するときは、`2024-01-01T00:00:00Z` 以上、48 ビットの最大値以下だけを受け付ける。外れたら `ClockOutOfRangeError`（停止する）。
  - **WP-02 で決めたこと：** 下限は設計にない。時計が未設定の場合や、0 に戻った場合を、ID に埋め込む前に見つけるため。
- 時計が戻った場合：ID の生成は続ける。ID の並び順は、実行の順序の正本にしない（順序は `run_seq` と `tenant_seq`）。
- 乱数：失敗した場合や、長さが違う場合は、`RandomSourceError`（停止する）。

## 3. 重複のときの扱い（E の2.2）

- `IdGenerator.issue_unique(id_type, write)` は、新しい ID を `write` に渡す。
- `write` が `DuplicateIdError` を出したら、**時刻はそのままで、乱数の部分だけを作り直して**、書き込み直す。
- 再試行は最大3回（最初と合わせて4回）。超えたら `IdCollisionRetryExhaustedError`（停止する。乱数の生成器の異常として扱う）。
- それ以外のエラーは、再試行せずにそのまま出す。
- **永続的な一意性は、WP-03 の SQLite の UNIQUE の制約と、トランザクションで強制する。** WP-02 の試験は、メモリの中の合成の保存先（`tests/support/fakes.py`）で確かめただけで、再起動の後を含めた一意性は保証しない。

## 4. 型の安全性

| 項目 | 方法 |
|---|---|
| 意図しない型の変換 | Pydantic の strict モード。`str` を Enum に、`list` を `tuple` に、`1.0` や `True` を整数に変えない |
| 不明な欄 | `extra="forbid"` |
| 必須の欄の欠け | すべての欄を必須にした（省略できる欄も、`None` を明示する） |
| 不正な状態の値 | Enum の値だけを受け付ける |
| 負の予算、不正な seq | 予算と回数は 0 以上、seq は 1 以上。上限は 64 ビットの符号付き整数（SQLite の INTEGER と E の4.3） |
| NaN と Infinity | 浮動小数点の欄はない。`allow_inf_nan=False` |
| 可変のコンテナ | `tuple` だけを使う。ラベルと参照は、並べた順で、重複なしを必須にした（正規化） |
| 更新する状態と、不変の記録 | `StateSnapshot`（Task、Run、Step、Attempt）は `evolve` で新しい値を作る。ID などの識別の欄と、一度だけ設定できる欄（`action_hash`、`permit_id`、`receipt_id`、`reservation_id`）は変えられない。`ImmutableRecord`（Event、EngineRequest）には、更新の方法がない |
| 書き出しと復元 | `dump_json` は、鍵を並べた、空白のない UTF-8 の JSON。`load_json` は、失敗したら `RecordValidationError` を出す。エラーには、場所と種類だけを入れ、入力の値は入れない |
| 未知の `schema_version` | Event は、`1` だけを受け付ける |
| 認可 | 型は、データにすぎない。型の検証を通っても、権限は生じない |

## 5. 設計資料にない制約（WP-02 で加えたもの）

| 制約 | 理由 | 影響 |
|---|---|---|
| Task の状態（`CREATED`、`ACTIVE`、`PAUSED`、`COMPLETED`、`FAILED`、`CANCELLED`） | 設計が決めていない | **PROVISIONAL**。WP-04 で確かめる |
| Step の種類（`MODEL_CALL`、`TOOL_CALL`、`VERIFICATION`）と状態 | 設計が決めていない（A の3.1 の Step の状態は、E の3.1 の Attempt の状態に移された） | **PROVISIONAL**。WP-04 と WP-15 で確かめる |
| Attempt の状態と、欄の対応（`CONTENT_FIXED` から `action_hash` が必要。`PERMIT_ISSUED` から `permit_id`。`PERMIT_CONSUMED` から `receipt_id`） | E の3.1 の順序から導いた | 遷移が許されるかどうかは、WP-04 で決める |
| `reservation_id` は、承認の前でも持てる | AUDIT-01（ID の確保は、ActionDescriptor を固定する前） | ID を持つだけでは、何も許されない。有効化は WP-09 |
| Event の `run_id` と `run_seq` は、両方あるか、両方ないか | E の2.3 | — |
| `tenant_seq` が 1 のときだけ、`prev_hash` がない | ハッシュの連鎖の始まり（A.I3） | イベントのハッシュの計算の方法は、WP-03 で決める |
| Event の `recorded_at_ms` | A の2章の `ts`（E の8章の一覧にはない） | 表示のための時刻。順序には使わない |
| EngineRequest の `attempt_id` には `step_id` が、`step_id` には `run_id` が必要 | 参照の階層を保つため | — |
| `done_criteria` は1件以上 | 完了の検証には、基準が必要（E.Z15） | — |
| 金銭以外の上限は、すべて必須 | D.V11（どの上限も欠けないようにする） | 候補の値は、D の11.2 の PROVISIONAL のまま |
| 通貨は `XTU` だけ | Phase 1 は、試験の単位だけ（D の10章） | 実際の通貨は、オーナーの承認の後に加える |
| `policy_version` は文字列（英数字と `._-`、64文字まで） | 設計が形を決めていない | — |

## 5.1 レビューの指摘への対応（WP02-R01〜R03）

| 指摘 | 対応 |
|---|---|
| WP02-R01 JSON の重複したキー | `load_json` は、Pydantic で検証する前に、標準ライブラリの `json` で事前に検査する。どの深さでも重複したキー、`NaN`、`Infinity`、範囲を超える数（`1e400`）、対になっていないサロゲート、正しくない UTF-8、BOM、後ろに続くデータ、制御文字を拒否する。そのうえで、今までどおり Pydantic の strict モードで検証する |
| WP02-R02 検証エラーの情報の保護 | エラーの場所は、宣言した欄の名前と、添字だけで作る。入力に含まれた未知のキーは `<unknown>` に置き換える。エラーの種類だけを持ち、入力の値は持たない。元の例外はつながず（`from None`）、トレースバックにも入力が出ないようにした。エラーは必ず `RecordValidationError` として出す |
| WP02-R03 ID の検証と重複 | 型付きの ID でも、Pydantic の欄に入れるときは、必ず内容を検査し直す（`str.__new__` で作った偽の値を拒否する）。`IdGenerator` の再試行は、「何も保存されなかったことが確かな場合」に出す `DuplicateIdError` だけに限る。結果が分からない書き込みは、別のエラーにして、再試行しない（WP-03 で強制した） |

- **今の JSON の形は、Phase 1 の内部の保存の形である。** Python の `json` で作り、RFC 8785（JCS）に完全に準拠しているかは、確かめていない。

## 6. 後の WP に送ったもの

| 項目 | 送り先 | 理由 |
|---|---|---|
| ModelPort、ModelRequest、ModelResponse | WP-05 | 使う側（Fake Provider）がまだなく、E の8章の型の詳細も WP-05 の範囲 |
| Journal の実装、イベントのハッシュ、保存の形、永続的な一意性 | WP-03 | — |
| イベントの種類ごとの中身（payload）の形 | WP-03・WP-04 | E の8章は、本文を暗号化した blob への参照（`refs`）として扱う |
| 状態遷移の規則 | WP-04 | — |
| Safety、Permit、予算の予約の型 | WP-09 | — |
| Context の型 | WP-14 | — |

## 7. 確かめていないこと

- `os.urandom` が、どの環境でも失敗しないこと（失敗した場合は、停止する作りにした）。
- 再起動をまたいだ ID の一意性（WP-03 で確かめる）。
