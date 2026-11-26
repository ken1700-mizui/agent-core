# WP-11：構造化ログ（実装の記録）

> 文書の状態：**DRAFT**（ChatGPT によるコードレビューを待っている）　作成日：2026-11-26
>
> - この文書は、WP-11 で実装した内容と、**設計資料に書かれていなかったために、WP-11 で決めたこと**を記録する。
> - 正本の規則は `E_INTEGRATION.md` の28章に従う。この文書は、設計資料を上書きしない。
> - 実装：`src/agent_core/types/observability.py`（型）、`src/agent_core/ports/log_sink.py`（接続口）、`src/agent_core/observability/logger.py`、`src/agent_core/observability/audit.py`、`src/agent_core/adapters/jsonl_log.py`、`src/agent_core/adapters/system.py` の `SystemMonotonicClock`。

## 1. 構成

```
agent_core.runtime
        ↓
agent_core.adapters | agent_core.identity | agent_core.tool | agent_core.observability
        ↓
agent_core.ports（LogSink、MonotonicClock、Clock）→ agent_core.types
```

- `agent_core.observability` は、`ports` と `types` だけに依存する。`runtime`、`tool`、`adapters`、SQLite を import できないことを、import-linter の契約と試験で確かめた。
- 出力先は `LogSink` の接続口で抽象化した。試験ではメモリの中の Sink を使う。本体には、ローカルの JSON Lines の Sink がある。

## 2. 記録の型（E の7.7、C2.X4）

`LogRecord` の欄（これ以外はない）：

| 欄 | 内容 |
|---|---|
| `log_schema_version` | 1 |
| `kind` | `STATE_CHANGED`、`OPERATION_FINISHED`、`OPERATION_FAILED` |
| `recorded_at_ms` | 人が見るための時刻（壁時計） |
| `elapsed_ms` | 経過時間（単調な時計）。負の値は持てない |
| `engine` | Engine の名前と版 |
| `task_id`、`run_id`、`step_id`、`attempt_id`、`event_id` | 型付きの ID。`attempt_id` には `step_id`、`step_id` には `run_id`、`run_id` には `task_id` が必要（Task から Attempt までを追える） |
| `entity`、`state` | 対象の種類と状態。状態は、その種類の状態の Enum の値だけ（任意の文字列は拒否する） |
| `reason` | 決まった理由のコード（`ReasonCode`）。メッセージではない |
| `labels` | 許したラベル（`synthetic`、`public`、`internal`）だけ |

- **任意の文字列や辞書を受け取る欄も、メソッドもない。** そのため、次のものは記録の形の上で入れられない。
  - プロンプトや応答の本文。
  - ツールの入力と出力、ファイルの本文。
  - API キー、パスワード、認証のヘッダー。
  - 自由な形の例外のメッセージ、任意の追加の欄。
- 失敗は、型付きのエラーの `code` から決めた理由のコードだけで記録する（`reason_for`）。例外のメッセージは読まない。型付きでない例外は `UNCLASSIFIED_ERROR` にする。
- 機密の検査で見つけた値そのものを記録する欄もない（C2 の4.5）。
- 書き出しは、正規化した JSON（`dump_json`）の1行。

## 3. 保存

- **ローカルの JSON Lines**（`JsonLinesLogSink`）：
  - 外部には送らない。
  - リンクをたどらずに開く（`O_NOFOLLOW`）。リンクが1つの通常のファイルだけを使う。新しいファイルは 0600 で作る。
  - 追記するだけ。1行は改行1つで終わり、途中に改行を含まない。
  - 1件の大きさの上限は 2048 バイト（`StructuredLogger`）。保存の量の上限は `max_total_bytes`。超える行は丸ごと拒否し（`LogCapacityError`）、途中の行は書かない。ローテーションはしない。
- **ログの失敗と Journal の失敗を混同しない：** ログを書けないときは `LogWriteError`（致命的でない）を出す。`JournalError` ではなく、Journal の状態には影響しない。

## 4. 観測のログと監査の記録の区別

| | 観測のログ | 監査の記録 |
|---|---|---|
| 型 | `LogRecord` | `AuditRecord`（C2 の9.2 の一部。本文の欄はない） |
| 書くもの | `StructuredLogger` | `AuditWriter` |
| 書けないとき | `LogWriteError`（致命的でない） | `AuditWriteError`（**致命的**。その操作を止める） |

- **監査が必須の操作では、記録できなければ操作を止める。** WP-11 では、この契約（エラーの型と、致命的であること）を作った。本格的な強制（操作との結び付け、連番、連鎖、署名したチェックポイント）は WP-09 で行う。
- **Audit Writer は、SAG もほかの部品も呼ばない**（C2.X25）。決まった形の記録を書き出して、Sink に渡すだけである。書いている間に、もう一度呼ばれたら `AuditRecursionError` にする（試験で確かめた）。

## 5. 時間の測り方

- 人が見る時刻（`recorded_at_ms`）は壁時計（`Clock`）。経過時間は単調な時計（`MonotonicClock`、本体は `time.monotonic_ns`）。
- 試験では、どちらの時計も注入する。決まった値で、決まった結果になる。
- 単調な時計の値が小さくなっても、経過時間は 0 にする（負の値を記録しない）。壁時計が戻っても、経過時間には影響しない（試験で確かめた）。

## 6. 設計資料との対応

| 設計 | 実装 |
|---|---|
| E の7.7（ID、状態、時間、ラベル、理由の区分だけ。Task から Attempt までを ID でつなぐ） | 2章 |
| C2.X4（Journal、ログ、監査ログに本文を入れない） | 2章（本文の欄がない） |
| C2 の4.5、C2.X25（Audit Writer は SAG を呼ばない。書けなければ操作を止める。見つけた値を出力しない） | 4章 |
| PHASE_1 の WP-11（ID、状態、時間だけ。目印の値の検査） | 試験（合成の秘密の値が、どの行にも出ないこと） |

## 7. 設計資料にない判断（WP-11 で決めたこと）

| 判断 | 理由 |
|---|---|
| 理由のコードを、閉じた Enum にした | 自由な文字列を入れないため。型付きのエラーの `code` と同じ値を使う |
| ログに許すラベルは `synthetic`、`public`、`internal` だけ | 機密や個人のデータのラベルを、ログに出さないため |
| 1件 2048 バイト、保存の量は Sink ごとに設定 | 大きさを必ず限るため |
| ローテーションをしない | Phase 1 では、上限に達したら拒否する方が安全で、単純なため |
| `AuditRecord` は最小限の項目だけ | 本格的な監査の記録は WP-09 |

## 8. 検証の結果

- **TDD：** 試験を先に書き、モジュールがない状態で失敗することを確かめてから、実装した。
- WP-11 で 24 件と、import-linter の契約の試験 3 件を加えた。8種類の品質の検査が、すべて成功した。
- 試験の中では、ネットワークへの接続を遮断している。外部への通信は発生しない。

## 9. 確かめていないこと

- ディスクが一杯の場合や、実際の I/O のエラーでの動き。Sink の失敗は、合成の Sink で試した。
- 本格的な監査の記録（連番、連鎖、署名、操作との結び付け）（WP-09）。
- ログの保存の期間（C2 の候補は 30 日）と、古いログの削除。
