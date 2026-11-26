# WP-10 最小の CLI（単一の合成 Runtime）

> 文書の状態：**DRAFT**（ChatGPT のレビュー待ち）　最終更新：2026-11-26
>
> - 起点：`25ca4a3`（WP-15 の限定修正 WP15-R01〜R05）。その前の `7ec1e7b` で、リモートとの一致、作業ツリーが空であること、`main`（`aa089ae`）、設計のブランチ（`e6b70c0`）、1866 件の試験と8種類の検査の成功を確かめた。
> - 対象：`PHASE_1_IMPLEMENTATION_PLAN.md` の WP-10（E の7章、C1.S2、C1.S17）。参照：`WP-09_SAFETY_COST.md`、`WP-15_AGENT_LOOP.md`。
> - **WP-12（Recovery）、WP-13 には進んでいない。** Web UI、遠隔操作、実モデル、実 API、実データ、任意のシェル、外部の作業領域への反映、新しい依存関係は、ない。

## 1. 範囲

| 実装した | 実装していない（範囲の外） |
|---|---|
| 合成のホームの作成（`init`）、Task と Run の状態の確認（`status`）、1つのプロセスの対話のセッション（`shell`）。セッションの中で、合成のデモの Task の開始、状態、Step と Attempt、予算と使用済みの費用と未確定の保留、承認待ちの操作の確認、ExactApproval、Run・テナント・全体の停止、明示的な停止の解除、安全な終了 | 別のプロセスからの操作、永続のセッション、遠隔操作、Web UI、FastAPI、本格的な鍵の管理、会話の記録の永続化と再開（WP-12）、外部の作業領域への反映、実データや任意の外部パスの処理 |

コード（`agent_core` の外の新しいパッケージ `src/agent_core_cli`）：

- `app.py`：入口（argparse）、`init`、`status`、`shell`、終了コード
- `environment.py`：ホーム、鍵、オーナーの連番、セッション（Runtime の組み立て、オーナーの操作、承認）
- `demo.py`：合成のデモの一覧（ツール、ポリシーの値、価格表、モデルのプロファイル、上限、3つのデモの台本）
- `views.py`：表示する内容（信頼する記録だけから）
- `render.py`：出力（固定の JSON の行、または人が読む文、制御文字の無害化）
- 試験：`tests/unit/test_wp10_cli.py`（31件）、`tests/unit/test_wp10_cli_process.py`（7件、実際の子プロセス）、`tests/unit/test_import_contracts.py`（3件を追加）、`tests/unit/test_no_process_execution.py`（対象に CLI を追加）

## 2. 構成

- CLI は合成の根（composition root）である。Journal を開き（OS のロックを取る）、Runtime の状態を Journal から作り直し、実行のゲート、合成のファイルのブローカー、Run ごとのエージェントのループを組み立てる。
- WP-08 の契約（`agent_core` のどのモジュールもファイルのブローカーを import しない。ブローカーを作れるのは `agent_core` の外の合成の根だけ）に合わせて、CLI を `agent_core` の外のパッケージにした。
- import-linter：
  - `root_packages` に `agent_core_cli` を加えた。
  - 新しい契約 `core-independent-of-cli`：`agent_core` のどのモジュールも `agent_core_cli` を import しない。
  - `phase1-no-network-or-process` の対象に `agent_core_cli` を加えた（ネットワーク、プロバイダーの SDK、プロセスの起動を CLI も持たない）。
  - 静的な検査 `test_no_process_execution.py` の対象にも CLI を加えた。
- CLI の独自の認可はない。すべての変更は、オーナーの署名付きの台帳の操作（`LedgerBatch`、オーナーの連番）か、オーナーの経路の ExactApproval であり、台帳と Safety・Cost の規則が決める（C1.S2：モデルはポリシー、RunGrant、マニフェスト、承認を変えられない。C1.S17：ポリシーの変更はオーナーの認証された経路だけ）。
- `AgentLoop` に `continues(run_id)` を加えた（このループが Run の会話の記録を持ち、続けられるか）。CLI は、ループが使えない承認を記録しないために、承認の前にこれを確かめる。

## 3. 起動方法

`uv` の環境で、`src` をパスに入れて起動する（このリポジトリはパッケージをインストールしない）。

```bash
PYTHONPATH=src uv run --locked --offline python -m agent_core_cli init --home /path/to/new/home
```

```bash
PYTHONPATH=src uv run --locked --offline python -m agent_core_cli shell --home /path/to/new/home
```

- `--home` は新しいディレクトリ（親は存在すること）。`init` はそれを 0700 で作る。
- 端末から使うときは、プロンプト `agent-core> ` を出す（標準入力が端末のときだけ）。
- `--json` で、出力を固定の JSON の行（1つの出来事に1行）にする。

## 4. コマンド一覧

最上位：

| コマンド | 内容 |
|---|---|
| `init --home DIR` | 新しい合成のホームを作る（鍵、設定、管理された作業木、Journal、オーナーの価格表・ポリシー・広い範囲の上限） |
| `status --home DIR [--json]` | Task と Run の状態を表示して終わる（OS のロックを短い間だけ取る。ループは作らない。Run を進めない） |
| `shell --home DIR [--json]` | Runtime を開き、標準入力から1行ずつコマンドを読む |

シェルの中：

| コマンド | 内容 |
|---|---|
| `start <report\|repair\|falseclaim>` | 合成のデモの Task と Run を作り、ループを動かす（待ちになれば戻る） |
| `status` | すべての Task と Run（Task の状態と Run の状態を分けて表示） |
| `steps [run]` | Step と Attempt の進み（状態、予約の状態、費用、保留、費用の確定） |
| `budget [run]` | Run と Task の範囲の上限、使用済み、保留、未確定の費用の一覧 |
| `pending` | 承認待ちの操作（承認画面） |
| `approve <action_hash>` | 表示された action_hash の操作だけを承認し、ループを再開する |
| `stop run [run\|last]`、`stop tenant`、`stop global` | オーナーの停止（署名付き）。影響する Run をすぐに停止の状態へ進める |
| `stops` | 停止の一覧（`stop_seq` を含む） |
| `resume-scope run <run> <stop_seq>`、`resume-scope tenant\|global <stop_seq>` | 停止の解除（`stop_seq` の指定が必要な、明示的なオーナーの操作） |
| `help`、`quit`、`exit` | 一覧、終了 |

`run` を省くと、このセッションで最後に始めた Run。`last` は Journal の最後の Run。

デモ：

- `report`：書き込み（承認1回）の後、終わりを主張する。検証で完了する。
- `repair`：下書きを書き、終わりを主張し、検証が失敗し、正しい内容で置き換え（承認2回）、修復1回で完了する。
- `falseclaim`：何も書かずに終わりを主張し続ける。修復の上限で `FAILED`（`REPAIR_LIMIT`）。

## 5. ホームと鍵

| パス | 内容 |
|---|---|
| `owner.key`（0600） | オーナーの経路の鍵と Permit の鍵（16進）。この CLI だけが読み、表示せず、コマンドラインや環境変数からは受け取らない |
| `owner.seq`（0600） | オーナーの batch の連番（署名の前に書く。同じ番号の再使用は台帳が拒否する） |
| `config.json`（0600） | テナント、オーナー、作業木の digest |
| `journal.db`、`journal.db.lock` | Journal と、Runtime の OS のロック |
| `area/tree` | 管理された合成の作業木（WP-08） |

- 読むときに、通常のファイル、0600、自分の所有、リンク数1、シンボリックリンクを辿らないことを確かめる（違えば `HOME_UNSAFE`）。
- 本格的な鍵の管理は前倒ししていない（Phase 1 の限界。C2）。

## 6. 実行例

正常な Task（`report`、文の出力を一部省略）：

```text
agent-core> start report
[outcome] run run_devcli1_…
  run_status: WAITING_APPROVAL
  reason: WAITING_APPROVAL
  model_calls: 1
[approval required]
  operation: TOOL_CALL
  tool_id: write_file
  tool_version: 1.0.0
  manifest_hash: sha256:9f171e9b…
  artifact_hash: sha256:950a5eed…
  effect_class: IRREVERSIBLE
  target: WRITE workspace/out/report-1.txt
  change: CREATE workspace/out/report-1.txt before=- after=sha256:0ddd5f09… size=20
  action_hash: sha256:…
  cost_cap_micro: 5000
  expires_at: 2026-11-26T21:30:53.662Z
  cost unit: XTU (synthetic)
  to approve exactly this action: approve sha256:…
agent-core> approve sha256:…
[outcome] run run_devcli1_…
  run_status: COMPLETED
  reason: VERIFIED
  model_calls: 2
  tool_calls: 1
  verification_reads: 2
  ledger_model_calls: 2
  ledger_tool_calls: 3
agent-core> budget
[budget] run run_devcli1_…
  cost unit: XTU (synthetic); real API cost: none
  RUN: cap=100000 spent=320 hold=0
  TASK: cap=1000000 spent=320 hold=0
  unsettled: none
agent-core> quit
bye (exit code 0)
```

修正のループを含む Task（`repair`、主な行だけ）：

```text
agent-core> start repair
  run_status: WAITING_APPROVAL   model_calls: 1
  change: CREATE workspace/out/report-2.txt before=- after=sha256:e554932b… size=23
agent-core> approve sha256:ba7b0a7c…
  run_status: WAITING_APPROVAL   model_calls: 3   tool_calls: 1   verification_reads: 1   repairs: 1
  change: REPLACE workspace/out/report-2.txt before=sha256:e554932b… after=sha256:0ddd5f09… size=20
agent-core> approve sha256:35acfe11…
  run_status: COMPLETED   reason: VERIFIED   model_calls: 4   tool_calls: 2
  verification_reads: 3   repairs: 1   ledger_model_calls: 4   ledger_tool_calls: 5
agent-core> budget
  RUN: cap=100000 spent=640 hold=0
  unsettled: none
```

2回目の承認画面の変更は、1回目に書いた下書きの digest（`before`）を正しい内容の digest（`after`）に置き換えるもので、承認する内容が変わったので新しい承認を求めている。

## 7. 承認、停止、状態確認の経路

承認（`approve <action_hash>`）：

1. 形式の検査（`sha256:` と64桁の16進。違えば `USAGE_INVALID_ARGUMENTS`）。
2. 承認待ちの Attempt がなければ `NO_PENDING_APPROVAL`。表示された action_hash と一致する待ちの Attempt がちょうど1つでなければ `APPROVAL_TARGET_MISMATCH`（別の操作は承認しない）。
3. このセッションのループがその Run を続けられなければ `SESSION_UNAVAILABLE`（古い Run を勝手に再開しない。承認は記録しない）。
4. 停止されていれば承認を記録せず、ループに停止を処理させる（停止が承認より優先）。
5. 操作の期限（記録した記述子の `expires_at_ms`）を過ぎていれば `APPROVAL_EXPIRED`（記録しない）。
6. ExactApproval（その action_hash、オーナー、1回だけ、期限は操作の期限とポリシーの上限の短い方）をオーナーの経路で署名し、台帳に記録し、その承認 ID でループを再開する。台帳の `ConfirmApproval` が、行為、期限、使用回数、停止をもう一度確かめる。
7. モデルの提案から承認を作ることはない。承認は `approve` のコマンドだけ。1つの承認は1回だけ使える（再使用は台帳が拒否し、CLI も待ちがなければ `NO_PENDING_APPROVAL`）。

停止（`stop ...`）：

- オーナーの署名付きの `StopScope` を、実行のゲートの排他の中で台帳に記録する（`gate.stop`）。
- その後、影響する Run（RUN なら1つ、TENANT・GLOBAL なら終わっていないすべて）について、ループを呼んで停止の状態へ進める（承認 ID なしで停止を確かめる。WP15-R02）。待っている操作は実行前に中止される。結果不明や未確定の費用があれば、正常な `CANCELLED` にはならない。
- 別のセッションの Run も停止できる（会話の記録のないループは、報告と停止しかしない）。
- 停止は Journal に残り、再起動で初期化されない。テナントや全体が停止している間は `start` を拒否する（`SCOPE_STOPPED`）。
- 解除は `resume-scope` だけ（`stop_seq` を指定する明示的なオーナーの操作）。

状態確認：

- `status`、`steps`、`budget`、`pending`、`stops` は、Runtime の状態と台帳を読むだけで、Run を進めない。
- 最上位の `status` は、ロックを取って読むだけで終わる。セッションが開いている間はロックを取れない（`RUNTIME_LOCKED`、終了コード 4）。状態確認のために二つ目の書き込み元を作らない。
- 注記：Journal を開くこと自体は、新しい epoch の記録と、Journal の自身の回復（消費済み・実行中の予約を UNKNOWN にする）という書き込みを含む（WP-03、WP-09）。これはロックの中の単一の書き込み元が行う。

## 8. 承認画面

信頼する固定のメタデータだけを、台帳が記録した記述子から表示する。

| 項目 | 内容 |
|---|---|
| 操作の種類 | `operation`（TOOL_CALL）、`effect_class`（IRREVERSIBLE など） |
| 実行物の版 | `tool_id`、`tool_version`、`manifest_hash`、`artifact_hash`（ブローカーのコードの hash） |
| 対象資源と読み書き | `targets`（パスと READ・WRITE） |
| 変更前後の digest | `change`（CREATE・REPLACE、`before`、`after`、大きさ） |
| action_hash | 承認の対象（これだけが承認される） |
| 予算の上限 | `cost_cap_micro`（合成の XTU） |
| 承認の期限 | `expires_at`（UTC）と `expires_at_ms` |
| 再開できるか | `resumable`（このセッションのループが続けられるか） |

表示しないもの：プロンプトの本文、ファイルの本文、認証情報、秘密鍵、自由形式の例外のメッセージ。出力するすべての値は `render.clean` を通す（C0・C1 の制御文字、DEL、双方向の上書き、ゼロ幅の文字、行・段落の区切り、未割り当て・私用の文字を、見える形のエスケープにする。長さも制限する）。

## 9. セッションの制約

- エージェントのループの会話の記録はプロセスの中だけなので、承認待ちと再開は同じプロセスの対話のセッションの中で扱う。
- 別の CLI のプロセスは、途中の Run を再開しない（`SESSION_UNAVAILABLE`）。停止はできる。
- OS のロック（Journal の `RuntimeLock`）で、同じホームの Runtime は1つだけ。二つ目は `RUNTIME_LOCKED`（終了コード 4）。
- EOF：正常な終わり。待っているものは承認しない。終了コードは最後に扱った Run の状態による（待ちなら 3）。
- Ctrl+C（SIGINT）：入力待ちでも処理の途中でも、Journal を閉じて（ロックを解放して）終了コード 130。モデルの呼び出しの途中なら、その Attempt は結果不明として記録され（開始済みの操作を未実行とみなさない）、次のセッションでは `RECONCILIATION_REQUIRED`。
- 異常終了（SIGKILL など）：ロックはカーネルが解放する。次のセッションは古い Run を再開しない。Journal の状態はそのまま WP-12 に引き継げる。
- SIGTERM は特別には扱わない（異常終了と同じ）。

## 10. 出力とエラー

- 人が読む文（既定）と固定の JSON の行（`--json`）。JSON の各行は `schema: "agent-core-cli/1"` と `kind` を持つ。`kind`：`started`、`outcome`、`approval_request`、`pending`、`status`、`steps`、`budget`、`stops`、`stopped`、`error`、`help`、`bye`。
- Task の完了と Run の完了を分ける（`status` の `task status` と `run status`。Phase 1 のループは Task を閉じない）。
- 費用は合成の XTU（マイクロ単位）で、実 API の費用は `none`（Phase 1 は実 API を使わない）。
- エラーは固定の理由コードだけを表示する（例外のクラス名は `INTERNAL_ERROR` の補足としてだけ）。

| 理由コード | 意味 |
|---|---|
| `USAGE_INVALID_COMMAND` | 知らないコマンド |
| `USAGE_INVALID_ARGUMENTS` | 引数の誤り（数、形式） |
| `HOME_EXISTS`、`HOME_MISSING`、`HOME_PARENT_MISSING`、`HOME_UNSAFE`、`HOME_INCOMPLETE` | ホームの誤り |
| `RUNTIME_LOCKED` | 別の Runtime がロックを持つ |
| `UNKNOWN_DEMO` | 知らないデモ |
| `RUN_NOT_FOUND` | Run がない |
| `NO_PENDING_APPROVAL`、`APPROVAL_TARGET_MISMATCH`、`APPROVAL_EXPIRED` | 承認の拒否 |
| `SESSION_UNAVAILABLE` | このセッションでは続けられない Run |
| `SCOPE_STOPPED` | 停止の間の開始 |
| `INTERNAL_ERROR` | 想定外の誤り（シェルを終える） |

Run の結果の理由（`reason`）は WP-15 のもの（`VERIFIED`、`WAITING_APPROVAL`、`STOPPED`、`REPAIR_LIMIT`、`LIMIT_EXCEEDED`、`OUTCOME_UNKNOWN`、`UNSETTLED_COSTS`、`SESSION_UNAVAILABLE` など）。

## 11. 終了コード

| コード | 意味 |
|---|---|
| 0 | 成功（`shell`：最後に扱った Run が COMPLETED か CANCELLED、または Run がない） |
| 1 | 失敗（拒否されたコマンド。`shell`：最後に扱った Run が FAILED か ABORTED_SAFETY） |
| 2 | 入力不正（コマンドラインの誤り） |
| 3 | 待機（`shell`：最後に扱った Run が WAITING_APPROVAL、PAUSED_BUDGET、CANCELLING、RECONCILIATION_REQUIRED、RUNNING） |
| 4 | 別の Runtime がロックを持つ |
| 5 | 想定外の誤り |
| 130 | 中断（Ctrl+C） |

シェルの中の入力の誤りは、その行の `error` を出して続ける（終了コードを変えない）。

## 12. 安全性の保証の範囲

保証する（試験で確かめた）：

- CLI から呼ばれたことは権限にならない。オーナーの署名付きの操作と ExactApproval だけが変更を起こし、台帳が決める。Safety Engine を迂回する経路はない。
- 承認は表示した action_hash の操作だけ。別の操作、期限切れ、再使用、停止の後の承認は、実行されない。
- 停止は承認より優先し、承認 ID がなくても処理される。再起動で初期化されない。解除は明示的な操作だけ。
- 鍵、ファイルの本文、プロンプトは出力されない（子プロセスの試験でも確かめた）。端末の制御文字は出力されない。
- 1つのホームに Runtime は1つ。古い Run を別のプロセスが再開しない。Ctrl+C、EOF、異常終了の後にロックが残らない。開始済みの操作を未実行とみなさない。

保証しない・未検証：

- 鍵はホームのファイル（0600）にある。同じユーザーの別のプロセスや管理者からは守れない。本格的な鍵の管理は後の段階。
- 証拠の検証器は、Phase 1 の合成のもの（すべて受け入れる）で、台帳の対応の検査、ゲートの MAC 付きの報告、ブローカーのチケットに頼る。独立した証拠の検証器は未実装。
- 予算の期間は固定の合成の期間（`synthetic-2026-11`）。
- 端末の制御文字の無害化は、Unicode の分類による。すべての端末の解釈を確かめてはいない。
- Windows、macOS では試していない（Linux だけ）。
- SIGTERM、SIGHUP の特別な扱いはない。

## 13. 試験の結果

- 新しい試験：41件（`test_wp10_cli.py` 31件、`test_wp10_cli_process.py` 7件、`test_import_contracts.py` 3件）。`test_no_process_execution.py` の対象に CLI を加えた（件数は同じ）。
- 先に試験を書き、赤を確かめた（パッケージがなく、収集の時点で失敗）。実装の途中で、合成の期間の形式（`synthetic-YYYY-MM`）の誤りと、子プロセスの試験の出来事の順序の想定の誤り（`outcome` の後に `approval_request`）と、親ディレクトリの用意を直した。
- 全体：1896 件から 1937 件。8種類の検査が、すべて成功した。`uv.lock` は変えていない（新しい依存関係はない）。

必須の試験との対応：

| 必須の試験 | 試験 |
|---|---|
| 正常な Task の開始と完了 | `test_a_normal_task_runs_to_completion_with_one_approval`、`test_a_whole_session_in_a_child_process` |
| 修正のループを含む Task | `test_a_task_with_a_repair_loop` |
| 状態の表示 | `test_the_state_views`、`test_human_readable_output`、`test_status_outside_a_session_is_read_only` |
| 予算と保留の表示 | `test_the_state_views`、`test_an_unknown_outcome_keeps_its_hold_and_is_shown` |
| 承認待ちからの正常な承認 | `test_a_normal_task_runs_to_completion_with_one_approval` |
| 間違った操作への承認 | `test_a_wrong_action_is_not_approved` |
| 期限切れの承認 | `test_an_expired_approval_window_is_refused` |
| 承認待ちの間の停止 | `test_a_stop_while_waiting_cancels_without_an_approval` |
| 承認 ID なしでの停止の確認 | 同上（停止の後、承認 ID なしでループを呼ぶ） |
| 停止と承認の競合 | `test_a_stop_wins_over_a_later_approval`、`test_a_stop_racing_the_approved_action` |
| 未確定の費用の表示 | `test_an_unsettled_cost_is_shown` |
| 不正なコマンドと引数 | `test_invalid_commands_and_arguments`、`test_top_level_usage_errors` |
| 秘密情報の漏えいの防止 | `test_no_secret_reaches_the_output`、`test_no_key_is_printed_by_a_child_process` |
| 端末の制御文字への対策 | `test_terminal_control_characters_are_never_printed`、`test_the_sanitizer` |
| Ctrl+C、EOF、異常終了 | `test_ctrl_c_at_the_prompt_exits_130_and_releases_the_lock`、`test_ctrl_c_during_a_model_call_is_never_taken_as_not_run`、`test_sigint_while_waiting_for_input`、`test_eof_while_waiting_approves_nothing`、`test_a_killed_session_is_not_resumed_by_the_next_one` |
| 二重の起動の拒否 | `test_one_runtime_at_a_time`（同じプロセス、子プロセスの両方） |
| 古い Run の無断の再開の拒否 | `test_a_run_of_an_earlier_session_is_not_resumed`、`test_a_killed_session_is_not_resumed_by_the_next_one` |
| 終了コード | `test_exit_codes`、各試験の終了コードの確認 |
| import-linter の依存の方向 | `test_the_core_never_imports_the_cli`、`test_the_cli_has_no_network_or_process_path` |

## 14. 後続の WP への引き継ぎ

- WP-12：会話の記録の永続化と、別のプロセスからの再開。`SESSION_UNAVAILABLE`、`UNRESOLVED_ATTEMPT`、`RECONCILIATION_REQUIRED`、`CANCELLING`（`UNSETTLED_COSTS`）の Run の再照合と解決。CLI はこれらを表示し、停止だけを行う。
- WP-13 以降：別のプロセスからの操作、永続のセッション、遠隔操作。
- 鍵の管理（C2）、独立した証拠の検証器、本物の期間の管理。
- 変わらず残る課題：AUDIT-02〜04、A-P6、CI（GitHub Actions は未実行）、OS の隔離、実データのための鍵付きのハッシュ。
