# WP2-02 P2 Broker と SandboxPort（偽の Sandbox）（実装の記録）

> 文書の状態：**DRAFT**（実装と試験の記録。レビュー待ち）　作成：2026-11-28
> 承認：D16（WP2-02 の限定的な実装、S-5：必要最小限の AF_UNIX の IPC に限った import の契約の変更）
> 設計：`PHASE_2_SANDBOX_DESIGN.md` の 7.6、7.6.1。**Docker には接続していない。何も実行していない**（偽の Sandbox）。

## 1. 実装したもの

| 対象 | ファイル | 内容 |
|---|---|---|
| IPC のメッセージ | `agent_core/types/broker_ipc.py` | 型付きの要求と応答（1か所）。長さ付きの枠（上限 64 KiB）、厳密な JSON、要求の種類の固定、HMAC-SHA256（向きごとの領域、定数時間の比較）。ソケットは扱わない（純粋） |
| P2 の実行の記録 | `agent_core_broker/record.py` | 追記だけの JSON 行、ハッシュの連鎖、追記ごとに `fsync`。`ACCEPTED` は実行の**前**に同期する。`create` で1回作り、`open` は存在と全件の正しさを要求する。owner_epoch の高水位 |
| 偽の Sandbox | `agent_core_broker/fake.py` | 決定的。何も実行しない。`exit0`、`exit1`、`hang`（停止を確認できる）、`stubborn`（停止を確認できない） |
| Broker（P2） | `agent_core_broker/server.py`、`__main__.py` | 要求の処理（下の順序）、P2 の期限による停止（`tick`）、AF_UNIX の待ち受け（ソケットは 0660、1つのソケットに Broker は1つ：ロックのファイル）、`init` と `serve` のコマンド |
| P1 の client | `agent_core/adapters/broker_client.py` | `SandboxPort` の実装。送る前に相手の UID（`SO_PEERCRED`）、応答の MAC・種類・`request_id`・`attempt_id`・Receipt を照合。失敗は `SandboxUnavailableError`（`sent` で、届いた可能性を示す）。AF_UNIX だけ |
| P1 の実行の経路 | `agent_core/runtime/sandbox_execution.py`、`runtime/execution_gate.py` | `run_sandbox_operation`：Phase 1 のゲートの経路（Permit、Receipt、報告）を P2 に通す。ゲートの `execute` に v4 の結果の記録（`outcome`）を渡す引数を足した |
| 中止の理由 | `types/transitions.py` | `AbortReason.SANDBOX_UNAVAILABLE` |
| import の契約 | `pyproject.toml` | S-5：`agent_core.adapters.broker_client -> socket` だけを例外にした（他のモジュールは socket を import できない）。新しい契約：P1 は P2 を import しない（`p1-does-not-run-p2`）。P2 は `agent_core.types` だけを使い、Journal・台帳・Runtime・ネットワーク・プロセスに届かない（`p2-broker-isolation`）。契約は 6 → 8 |

## 2. Broker の要求の処理の順序（最初の失敗で、何も変えずに拒否）

1. 相手が P1 の利用者（`SO_PEERCRED`。読む前に確かめる）→ 違えば `PEER`。
2. 1つの枠、大きさの上限、厳密な JSON、MAC、型、固定の種類、要求の本体の型、外側と本体の ID と epoch の一致 → `MALFORMED` か `AUTHENTICATION`。
3. 実行の記録が使える → 使えなければ `RECORD_UNAVAILABLE`（すべての `start` を拒否）。
4. owner_epoch が記録の高水位より古くない → 古ければ `STALE_EPOCH`。
5. 種類ごとの規則：Sandbox の設定と固定のプログラムが許可されている、Receipt ごとに `start` は1回、`collect` は終わった後だけ、`reap` は何も動いていないときだけ。

その後、認証された要求でだけ高水位を上げ、`start` では `ACCEPTED` を記録して同期してから、偽の Sandbox に実行を頼む。

## 3. 重複の実行の防止の契約（実装と試験）

| 場面 | 実装 | 試験 |
|---|---|---|
| 同じ Receipt の2回目の `start` | 実行の記録にあれば `DUPLICATE` と今の状態を返し、何も実行しない | `test_a_receipt_starts_once` |
| コンテナの回収の後 | 回収しても記録は残る（`REAPED`）。ラベルを読む相手がなくても重複を拒否 | `test_a_reaped_receipt_never_starts_again` |
| P2 の再起動の後 | 記録を読み直す。記録にある Receipt は実行しない。終わった結果は記録から返す | `test_a_restarted_broker_remembers_every_receipt`、SIGKILL と再起動（別のプロセス）`test_p2_runs_as_its_own_process_and_survives_sigkill` |
| `ACCEPTED` の後に P2 が止まった | 再起動の後も `ACCEPTED`（実行したか分からない）。二度と実行せず、終わったとも言わない | `test_an_accepted_start_without_a_result_stays_unknown_after_a_restart` |
| 応答の消失 | P2 は処理済み、P1 は答えを受け取っていない。`status` で記録の状態が分かる。送り直した `start` は `DUPLICATE` | `test_a_lost_response_never_leads_to_a_second_run`、`test_a_closed_connection_without_an_answer_is_unknown_to_the_client` |
| 記録が使えない | ない・途中で切れた・書き換えた・順序を入れ替えた、のどれでも開かず、すべての `start` を拒否 | `test_an_unusable_record_refuses_every_start`（4つ）、`test_p2_refuses_to_serve_without_its_record` |
| 同期の順序 | `fsync` が実行より先 | `test_the_record_is_synced_before_the_run` |
| owner_epoch | 高水位は記録にあり、再起動の後も古い epoch を拒否。MAC の正しくない要求は高水位を上げられない | `test_an_older_owner_epoch_is_refused_and_stays_refused_after_a_restart`、`test_an_unauthenticated_request_cannot_raise_the_epoch`、別のプロセスでも確認 |
| P2 は Journal を変えない | P2 は `agent_core.types` だけを import する（契約）。Journal・台帳には P1 が検証した結果だけを書く | import の契約の試験（埋め込んだ import で契約が壊れること） |

## 4. Phase 1 の経路との接続（受入の条件）

`run_sandbox_operation`（`test_wp2_02_sandbox_gate.py`）：

- **正常：** `prepare` → ゲートの claim・start（Receipt は `USED`、1回）→ P2 の `start`（同じ Receipt）→ 終わるまで `status` → `collect` → 署名付きの報告で `COMPLETED`（終了の符号 0 以外は `FAILED_DEFINITE`）→ `reap`。保留は確定する。
- **P2 が使えない（start の前）：** `ABORTED_BEFORE_DISPATCH`（`SANDBOX_UNAVAILABLE`）、Receipt は `NOT_DISPATCHED`、保留は解放、何も実行していない。古い epoch も `prepare` で拒否され、同じ扱い。
- **start の後に P2 を失った：** `OUTCOME_UNKNOWN`、保留は `UNKNOWN` のまま、再試行しない。再起動と Recovery の後も同じ（既存の扱いのまま）。
- **停止の要求と確認：** 確認できない停止は `OUTCOME_UNKNOWN`。確認できた停止は `FAILED_DEFINITE`。P2 は期限を過ぎた実行を自分の単調な時計で止める（P1 に依らない）。
- **評価の Run：** 結果の記録（Journal v4）の `wall_ms` は、終わった実行では P2 が報告した値、止めた実行では P1 の単調な時計で計った値（作った値ではない）。偽の Sandbox の `wall_ms` は、表の固定の模擬の値であり、実際の計測ではない。

承認、費用の保留、結果不明の扱いは変えていない（既存の回帰の試験はすべて成功）。

## 5. 確かめていないこと（未実行として記録する）

| 項目 | 状態 | 理由 |
|---|---|---|
| P1 と P2 を**別の OS 利用者**で動かす試験 | **未実行** | OS 利用者の作成（E-2）は承認されていない。今回は同じ利用者で、P2 に別の UID を期待させて、`PEER` で拒否されることだけを確かめた |
| Docker の Sandbox、実際のコンテナのラベル | 未実行（範囲の外） | WP2-03。daemon の起動（E-1）は承認されていない |
| 実際の `wall_ms` の計測 | 未実行 | 偽の Sandbox は模擬の値を返す |
| ソケットのディレクトリの権限 | 一部 | ソケットのファイルの権限（他の利用者は不可）だけを確かめた。ディレクトリの所有者と権限は E-2 の後 |
| 鍵の配布と保管 | 設計だけ | 鍵のファイル（0600、他の利用者は読めないことを確かめる）。本格的な鍵の管理は後の Phase |

## 6. 既存の試験の変更（削除・無効化はない）

| 試験 | 変更 |
|---|---|
| `test_import_contracts.py` の `_copy_real_package` | 写す包みに `agent_core_broker` を加えた（契約が名指すため） |
| `test_no_process_execution.py` | 調べる範囲に `agent_core_broker` を加えた（範囲を広げただけ） |
| `tests/support/ledger_world.py` の `fixed_attempt` | 任意の引数 `content_provenance` を加えた（評価の Run の試験のため。既存の呼び出しは変わらない） |

## 7. 試験の件数

WP2-02 の新しい試験：57 件（Broker 33、ゲートの経路 9、別のプロセス 6、import の契約 9）。全体 2505 件。
