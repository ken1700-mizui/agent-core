# WP-08 基本のファイル操作

> 文書の状態：**DRAFT**（第2回の実装のレビュー前）　最終更新：2026-11-26
>
> - 対象：`PHASE_1_IMPLEMENTATION_PLAN.md` の WP-08（C1 の8.3・8.5、C1.S16・S19、E.Z33）
> - 第1回（起点 `bb77074`、コミット `677201d`）：目録、ApplyPlan、実行券、S19 による拒否。1〜8章。94/100 のレビューを受けた。
> - 第2回（起点 `677201d`）：ADR-0008 の `dir_fd` の方式で、管理された合成の作業ツリーの、1ファイルの読み取りと書き込み（CREATE、REPLACE）を実装した。**9章。** 1〜8章のうち、第2回で変わった箇所には注記を付けた。
> - **WP-08 の実装を終え、ChatGPT のレビューを待っている。** 外部の作業領域への反映、複数のファイルの一括の変更は、範囲の外。

## 1. オーナーの判断（2026-11-26）

| 項目 | 判断 | この文書での扱い |
|---|---|---|
| パスの解決 | 今の環境では S19 に従う。安全なパスの解決の手段がなければ、対象のファイルを開く前に、読み書きを拒否する。`dir_fd` の方式の本採用は保留。実ファイルの操作を有効にする案を1つに絞って示す。依存関係の追加と、`ctypes` の禁止の解除は、承認の対象外 | 3.4、6章 |
| 作業ツリーの目録 | 合成のツリーには HMAC-SHA-256 を使う。目録の専用の鍵か、用途ごとに導いた鍵を使う。検証した目録を、対象のツリーと、Policy の信頼済みの digest に結び付ける。正しい目録を別のツリーに写した場合も、受け入れないことを試験する | 3.1 |
| ApplyPlan | 管理された作業ツリーの中の書き込みに必要な、最小の部分を WP-08 に含める。対象、操作の種別、変更の前の期待する状態、書き込む内容の digest を、承認された操作に結び付ける。内容の差し替えと競合を検出できる設計にする。外部の作業領域への反映は、後の範囲 | 3.2 |
| 実行券 | 追加を承認。ゲートが開始を確定した後に発行し、そのコールバックの中で、照合した1つの操作に、1回だけ使えるようにする。対象のパスに加えて、読み書きの種別と、書き込む内容も束縛する。別の操作への流用、再使用、コールバックの終了後の使用を拒否する試験を、先に書く | 3.3 |

## 2. 完了の条件と、今回の結果

| 完了の条件 | 結果 |
|---|---|
| 実装できる部分を、既存のゲートを通して試験する | 済み（4章）。`SyntheticExecutionGate` と SQLite の台帳を、実際の経路のまま使った |
| S19 で拒否したとき、対象のファイルに副作用がない | 確かめた（4章の S19）。開いたのは、ルートのディレクトリと目録のファイルだけ。作成、変更、削除は0件 |
| Attempt と予約が、設計どおりの状態になる | 確かめた。開始の前の拒否：Attempt は `ABORTED_BEFORE_DISPATCH`（理由 `SAFE_FILE_ACCESS_UNAVAILABLE` または `WORKSPACE_REJECTED`）、予約は `RELEASED`（押さえた額 0、費用 0）、Receipt は `NOT_DISPATCHED`、`DISPATCH_AUTHORIZED` と `DISPATCH_STARTED` は記録されない |
| 全試験と、8種類の検査 | すべて成功。試験は 1552 件（1490 件から 62 件を追加） |

## 3. 実装したもの

### 3.1 作業ツリーの目録（`types/files.py`、`safety/workspace.py`）

- **`WorkspaceManifest`（版 1）：** Agent Core が合成のツリーを作るときの記録。
  - `workspace_id`
  - `synthetic: true`（これ以外は受け付けない）
  - `root_path`（正規化した絶対パス）
  - `root_device`、`root_inode`（ルートの実体）
  - `entries`（作ったときの中身：パス、種別、大きさ、SHA-256）
  - `created_at_ms`
- **鍵：** `manifest_key(owner_key)` = `HMAC-SHA-256(owner_key, "agent-core/v1/workspace-manifest-key\0")`。
  - 目録の用途のためだけに導いた鍵である。
  - オーナーの鍵そのもの、Permit の鍵、別のオーナーの鍵では、検証に失敗する（試験した）。
- **MAC：** `HMAC-SHA-256(manifest_key, ドメイン + 目録の正規の JSON)`。比べるときは `hmac.compare_digest` を使う。
- **digest：** `manifest_digest`（ドメイン付きの SHA-256）が、操作の `workspace_ref` になる。オーナーは、この値を Policy の `trusted_synthetic_workspaces` に固定する。
- **置き場所：** 目録は、ツリーのルートの直下の `.agent-core-workspace.json` に置く（計画の2章「作ったときの目録（署名付き）を置き、それがないディレクトリへの操作は拒否する」）。
- **Broker の検査（`preflight`）：** 対象のファイルを開く前に、次の順で確かめる。
  1. 操作が `workspace_ref` を持つ。今の Policy が、決定と同じ版で、その digest を信頼している（`WORKSPACE_NOT_TRUSTED`）。
  2. ルートを、`O_DIRECTORY | O_NOFOLLOW` で開ける（`ROOT_UNUSABLE`）。所有者がこの利用者で、権限が 0700 である（グループと他人に権限がない。`ROOT_PERMISSIONS`）。
  3. 目録が、リンクではなく、ハードリンクが1つだけの普通のファイルで、1 MiB 以下である（`MANIFEST_UNREADABLE`）。厳密な JSON として読める（`MANIFEST_INVALID`）。MAC が正しい（`MANIFEST_MAC_INVALID`）。
  4. 目録の `root_path` が、Broker のルートと同じ（`ROOT_MISMATCH`）。目録の device と inode が、開いたルートの `fstat` と同じ（`ROOT_IDENTITY_MISMATCH`）。目録の digest が、操作の `workspace_ref` と同じ（`WORKSPACE_MISMATCH`）。
- **写しの拒否：**
  - 正しい目録を別のツリーに写した場合は、`ROOT_MISMATCH` で拒否する。
  - 元のツリーを移して、写したツリーを元のパスに置いた場合は、`ROOT_IDENTITY_MISMATCH` で拒否する（パスと中身は同じで、inode が違う）。
- **目録の読み方：** 目録は、パスで開く（`O_NOFOLLOW`。`dir_fd` は使っていない。本採用が保留のため）。
  - それでも、別の場所の目録を読まされる心配はない。中身は MAC で守られている。ツリーの同一性は、先に開いたルートの fd の `fstat` で確かめる。
  - 読んだ目録と、開いたルートが違うツリーのものなら、inode が一致しないので拒否する。
- **`entries` の扱い：** 作ったときの記録である。今の中身との照合は、安全な読み取りが必要なので、**していない**（5章）。

### 3.2 ApplyPlan の最小の部分（`WriteChange`。`types/safety.py`、`safety/descriptor.py`）

- **`FieldSpec.apply`：** `CONTENT` か `EXPECTED_STATE`。どちらも必須の STRING で、資源の引数とは兼ねられない。
- **書き込みのツールの条件：** 書き込みの対象が1つ、内容の引数が1つ、期待する状態の引数が1つであること（Manifest の検証）。書き込まないツールは、`apply` の引数を持てない。
- **`WriteChange` の中身：**
  - `op`：期待する状態が `absent` なら `CREATE`（対象がないこと）、`sha256:<64桁>` なら `REPLACE`（対象がその digest を持つこと）。それ以外は `EXPECTED_STATE_INVALID` で拒否する。
  - `path`、`expected_pre`、`content_digest`（内容の UTF-8 のバイトの SHA-256）、`content_size`
- **作るのは、信頼する Descriptor の組み立てだけ：** 引数から導く。呼び出し側は指定できない。
- **`ActionDescriptor` は版 3 になった。** `apply_change` を加えた。
  - 書き込みがあるときは、その1つの書き込みの対象の `WriteChange` を必ず持つ。書き込みがないときは持たない（型の検証）。
  - `apply_change` は `action_hash` に含まれる。だから、内容、期待する状態、対象を変えると `action_hash` が変わり、承認、予約、Permit がすべて合わなくなる。
  - 版 2 は受け付けない。
- **Journal のスキーマは、版 6 になった。** 記録する Descriptor の形が変わったため。版 5 のファイルは、開くときに拒否する（古いデータは消さない。読み換えもしない）。
- **内容の差し替え：** 承認した後に内容だけを変えて実行しようとすると、claim のときに Descriptor が作り直せず（`DESCRIPTOR_NOT_FROM_CONTENT`）、Receipt は `ISSUED` のまま残る。実行券の照合でも、内容の digest か大きさが違えば拒否する（3.3）。
- **競合（設計）：** `expected_pre` が承認に結び付いている。実際の書き込みでは、Broker が、対象を安全に開いた後で、今の中身の digest と `expected_pre`（CREATE なら、対象がないこと）を比べる。**今の環境では書き込まないため、この比較は実装も検証もしていない**（6章、5章）。

### 3.3 実行券（`runtime/execution_gate.py`、`types/files.py`）

- **発行：** `execute` の中で、`_start`（`DISPATCH_STARTED` の確定。Receipt は `USED`）の**後**に発行し、コールバックに渡す。コールバックの形は `run(authorization, ticket)` になった。
  - 対象の資源が1つでない操作には、実行券を渡さない（`None`）。そのため、ファイルの操作はできない。
- **束縛（`TicketBinding`）：** Receipt、Attempt、`action_hash`、操作（読み書きの種別、パス、書き込みなら `WriteChange` の全体）。
- **使用：** `gate.redeem(ticket, operation)` で使う。Broker は、自分が実際に行う操作を `FileOperation` として示す。書き込みの内容の digest は、実際のバイトから計算する。
- **拒否：** いずれも `PermitRejectedError` で、決まった理由を返す。
  - `TICKET_UNKNOWN`：このゲートが発行していない（別のゲートのもの、作っただけのもの）
  - `TICKET_CLOSED`：コールバックが終わった後（正常に終わっても、例外で終わっても）。前の実行の券を、後の実行で使った場合
  - `TICKET_USED`：2回目の使用
  - `TICKET_MISMATCH`：パス、読み書きの種別、内容、大きさ、期待する状態、対象のどれかが違う。**合わなかった券は、その時点で使い切りにする**（後から正しい操作を示しても、`TICKET_USED`）
- **実行券そのものは、何も持たない。** 状態は、発行したゲートだけが持つ。開いている券は、同時に1つだけ。
- `start` を直接呼んでも、実行券は得られない。

### 3.4 S19 の拒否と Broker（`adapters/file_broker.py`、`ports/files.py`）

> **第2回で変わった（9章）：** `SAFE_RESOLUTION_ADOPTED` は `True` になり（ADR-0008）、`safe_resolution_available()` は ADR-0008 の条件をこの環境で確かめる。`preflight` は `prepare` になった。S19 の拒否は、条件を満たせない場合に残る。

- `SAFE_RESOLUTION_ADOPTED = False`、`safe_resolution_available()` は `False` を返す。
  - 理由：Python 3.12.3 に `os.openat2` がない。`ctypes` は禁止。`dir_fd` の方式は保留。
- `preflight` は、目録の検査（3.1）に合格した後でも、`FileOperationRefusedError("SAFE_FILE_ACCESS_UNAVAILABLE")` で拒否する。
- `read` と `write` も、コールバックの中で、対象を開く前に同じ理由で拒否する（二重の防御）。
- Broker を作れるのは、合成の根（`agent_core` の外）だけ。`agent_core` のどのモジュールも、`agent_core.adapters.file_broker` を import できない（import-linter の契約 `file-broker-composition-root`）。Runtime は `FileOperationPort` を通して使う。
  - **見つかったこと：** 契約の対象を `agent_core` と書くと、import-linter は、Broker の祖先のパッケージの中の import を、何も報告せずに飛ばした（植え込んだ違反の試験で発見）。`agent_core.**`（すべてのモジュールを1つずつ）に直し、`adapters` の中と、最上位のモジュールからの import を検出することを試験した。

### 3.5 Runtime の順序（`runtime/file_execution.py`）

1. 記録した Descriptor を読む。書き込みかどうかと、渡された内容の有無が合わなければ、何も変えずに拒否する。
2. **開始の前（claim の前）**に `preflight(descriptor, 今の Policy)` を呼ぶ。
   - 目録が拒否されたら、`ABORTED_BEFORE_DISPATCH`（`WORKSPACE_REJECTED`）にする。
   - S19 で拒否されたら、`ABORTED_BEFORE_DISPATCH`（`SAFE_FILE_ACCESS_UNAVAILABLE`）にする。
   - どちらも、生きているゲートの MAC 付きの未実行の報告で行い、押さえた額を同じトランザクションで解放する（WP09-R04 と同じ経路）。
   - ほかの理由の拒否は、そのまま例外にする（握りつぶさない）。
3. 合格した場合だけ、ゲートが claim と開始を行う。コールバックの中で Broker が操作する。
   - Broker の拒否（対象を開く前）は `FAILED_DEFINITE`。
   - それ以外の例外は、結果不明（ゲートが押さえた額を保つ）。
- Journal と台帳のポートに、`policy(tenant_id)`（読み取りだけ）を加えた。

## 4. 試験

### 4.1 先に書いた試験と、失敗の確認（red）

- 先に、`tests/unit/test_wp08_files.py`、`tests/support/files_world.py`、import の契約の試験、補助の変更を書いた。
- 実装の前の実行：
  - `test_wp08_files.py` は、収集のときに `ImportError`（`agent_core.adapters.file_broker` などがない）で失敗した。
  - 全体では、補助が新しい型を使うため、10 個の試験のファイルが収集の段階で失敗した。
  - Broker の import の契約の試験は、3件のうち2件が失敗した（`adapters` と最上位。`runtime` の1件は、既存の契約が先に検出した）。
- **限界：** 新しい試験の失敗は、ふるまいの失敗ではなく、import の失敗だった。試験ごとに、理由まで含めた失敗を確かめたわけではない。

### 4.2 追加した試験（59件 + 契約の3件）

| 区分 | 試験 |
|---|---|
| 目録 | `test_the_manifest_key_is_derived_for_its_purpose`、`test_a_changed_manifest_is_refused`（5通り）、`test_the_manifest_is_synthetic_and_names_an_absolute_root`、`test_the_digest_of_a_manifest_is_the_workspace_ref`、`test_a_valid_tree_passes_until_the_s19_refusal` |
| 目録とツリー | `test_a_manifest_copied_to_another_tree_is_refused`、`test_a_copy_moved_into_the_original_path_is_refused`、`test_a_tree_without_a_manifest_is_refused`、`test_a_manifest_behind_a_link_is_refused`（シンボリックリンクと、2つ目のハードリンク）、`test_a_root_behind_a_link_is_refused`、`test_the_root_must_be_private` |
| 目録と Policy | `test_the_descriptor_must_name_this_manifest`、`test_the_policy_must_trust_the_manifest_now`（信頼していない、Policy がない、別の版）、`test_the_pdp_denies_an_untrusted_manifest_digest` |
| ApplyPlan | `test_a_write_tool_declares_one_target_content_and_expected_state`（4通り）、`test_apply_fields_belong_to_write_tools_only`、`test_the_write_change_is_derived_from_the_arguments`、`test_the_write_change_is_part_of_the_action_hash`（内容、期待する状態、対象）、`test_an_invalid_expected_state_is_refused`（4通り）、`test_write_change_needs_the_registration_of_a_write_tool`、`test_a_descriptor_cannot_carry_a_change_for_another_target`、`test_an_operation_is_consistent_in_itself`、`test_substituted_content_is_refused_before_the_start` |
| 実行券 | `test_the_ticket_is_issued_after_the_start_is_committed`、`test_there_is_no_ticket_outside_the_callback`、`test_a_ticket_is_used_once`、`test_a_read_ticket_cannot_serve_another_operation`（パス、種別）、`test_a_write_ticket_binds_the_content_and_the_expected_state`（内容、大きさ、期待する状態、対象）、`test_a_write_ticket_serves_its_own_change`、`test_a_ticket_cannot_be_used_after_the_callback`、`test_a_ticket_is_closed_when_the_callback_raises`、`test_an_earlier_ticket_does_not_work_in_a_later_execution`、`test_a_ticket_from_another_gate_is_refused` |
| S19 | `test_s19_refuses_before_any_target_is_opened`（読み取り、作成、置き換え）、`test_a_rejected_manifest_aborts_before_dispatch`、`test_s19_inside_the_callback_fails_definitely_without_side_effects`（読み取り、書き込み）、`test_a_file_operation_without_safe_resolution_is_never_started`、`test_safe_resolution_is_not_available_here` |
| import の契約 | `test_no_agent_core_module_imports_the_file_broker`（`adapters`、`runtime`、最上位） |

- **副作用の確かめ方：** `os.open`、`builtins.open`、`os.rename`、`os.replace`、`os.link`、`os.symlink`、`os.unlink`、`os.remove`、`os.rmdir`、`os.mkdir`、`os.makedirs`、`os.truncate`、`os.chmod`、`os.chown`、`os.utime`、`os.mkfifo`、`os.mknod` の呼び出しを記録した。
  - ツリーの中で記録されたのは、ルートと目録を開いた2件だけで、ちょうどその2件であることを確かめた。
  - 前後で、ツリーのすべての項目（種別、中身、inode、mtime、ctime、mode、リンクの数）と、各ディレクトリの一覧が同じであることを比べた。
- **既存の試験の変更：**
  - 書き込みのツールの入力が、内容と期待する状態を必須にしたため、補助と、書き込みの入力を直接書いていた試験（`test_safety`、`test_permits`、`test_tool_registry`）の入力を、`tool_world.write_input` に替えた。検査の条件は変えていない。
  - `test_wp09_execution` のコールバックを、2つの引数の形にした。
  - 試験の削除、省略、条件を緩めることは、していない。

## 5. 未検証の項目

> **第2回で変わった（9章）：** 実ファイルの正常な読み書き、書き込みの競合の検出、ファイルの操作の途中でのプロセスの停止は、第2回で試験した。残っている未検証の項目は 9.7 にある。

| 項目 | 理由 |
|---|---|
| 実ファイルの正常な読み取りと書き込み | 安全なパスの解決がないため、実装していない（S19） |
| 書き込みの競合の検出（`expected_pre` と実際の中身の比較、CREATE で対象がないこと） | 同じ理由。束縛（承認と実行券）だけを試験した |
| ファイルの操作の途中で、プロセスを止める試験 | 実際の操作がないため、行っていない。DB の記録と、実際の操作の原子性は、主張しない（WP-09 と同じ） |
| 目録の `entries` と、今の中身の照合 | 安全な読み取りが必要 |
| 合成のツリーを作る機能（本番のコード） | 作っていない。試験では、補助（`files_world`）が作る |
| 実行物のハッシュ（`artifact_hash`）と、実際に動くコードの照合 | WP-08 の範囲で、まだ扱っていない |
| 別の OS の利用者、別のプロセスによるツリーの移動や変更への耐性 | 6章の前提の外。試験していない |

## 6. 実ファイルの操作を有効にするための案（1つ）

> この節の状態：第1回の提案。**オーナーが、Phase 1 の管理された合成の環境に限って承認した**（ADR-0008）。実装と、実際に保証できる範囲は 9章（特に 9.6）にある。この節と違う点：一時ファイルには `O_TMPFILE` を使わず、予測できない名前と排他的な作成を使った。

### 6.1 案：標準ライブラリの `dir_fd` で、1つずつの部分をたどる

- **依存関係の追加：不要。** `ctypes` の解除も不要。Python 3.12 の `os` の、次の機能だけを使う（この環境で、使えることを確かめた）。
  - `os.open`、`os.stat`、`os.rename`、`os.link`、`os.unlink`、`os.mkdir` の `dir_fd`
  - `O_NOFOLLOW`、`O_DIRECTORY`、`O_PATH`、`O_TMPFILE`、`os.fsync`
- **設計の変更：必要。** C1 の8.3 は `openat2`（`RESOLVE_BENEATH`、`RESOLVE_NO_SYMLINKS`）を前提にしている。これを、次の手順に置き換える。ADR を新しく書き、C1 の8.3 の書き換えの文案を出す。
- **解決の手順：**
  1. ルートを `O_DIRECTORY | O_NOFOLLOW` で開き、目録の device と inode と比べる（今の実装と同じ）。
  2. パスを `/` で分け、途中のディレクトリを1つずつ `os.open(名前, O_DIRECTORY | O_NOFOLLOW | O_PATH, dir_fd=親)` で開く。`..`、`.`、空の部分、目録の名前は、分ける前に拒否する。リンクに当たると `ELOOP` になり、拒否する。
  3. 最後の部分を `O_NOFOLLOW` で開く。`fstat` で、普通のファイルであること、リンクの数が1であること、所有者と device がルートと同じであることを確かめる（ハードリンク、デバイス、FIFO を拒否する。C1.S16）。

### 6.2 前提：誰がツリーを移動・変更できるか

- ツリーを変えられるのは、Agent Core の P1（同じ OS の利用者、1つのプロセス）だけ。
- ルートは 0700 で、この利用者が所有する（今の実装で確かめている）。
- 次の主体は、ツリーに書き込まない。
  - 同じ利用者の、ほかのプロセス
  - 読み書きのできるマウントで同時に動く Sandbox
- 実行券と、ゲートの排他（開いている券は1つ）で、P1 の中でも、同時に操作するのは1つだけにする。
- **この前提が成り立たないとき（ほかの書き手がいる場合）の安全は、主張しない。** その場合は、OS の隔離（Phase 3A）が終わるまで、拒否を続ける。

### 6.3 何を保証できるか

| 場面 | 保証できること | 保証できないこと |
|---|---|---|
| 読み取り | 開いた後の fd は、ルートの下の、リンクを通らない、普通の1つのファイルを指す。途中で親のディレクトリが入れ替えられても、すでに開いた fd は変わらない。中身は fd から読み、digest を計算する | 読んでいる最中に、同じファイルを別の書き手が書き換えること（6.2 の前提で除く） |
| 作成（CREATE） | 親のディレクトリの fd の中に、`O_TMPFILE` か、`O_CREAT \| O_EXCL \| O_NOFOLLOW` の一時ファイルを作り、書いて `fsync` し、`os.link(..., dst_dir_fd=親)` で対象の名前を付ける。対象がすでにあれば `EEXIST` で失敗する（作成の競合を、OS が検出する）。最後に親を `fsync` する | 電源の断の後に、一時ファイルが残ること（回収の手順が必要） |
| 置き換え（REPLACE） | 一時ファイルを同じように作り、`os.rename(..., src_dir_fd=親, dst_dir_fd=親)` で置き換える。同じディレクトリの中なので、原子的（古い中身か新しい中身のどちらか）。置き換えの直前に、対象を `O_NOFOLLOW` で開き、digest が `expected_pre` と同じことを確かめる | 比べた後、名前を変える前の間に、別の書き手が書き換えること。標準ライブラリには、比べてから置き換える原子的な手段（`renameat2` の `RENAME_EXCHANGE` など）がない。**競合がないことは、6.2 の前提（書き手は1つ）の下でだけ成り立つ** |
| 書き込みの後 | `fstat` と digest で、書いた中身が `content_digest` と大きさに一致することを確かめてから、結果を報告する（E.Z33 の反映の後の確認） | DB の記録と、ファイルの操作の原子性（主張しない。途中で止まれば、結果不明として扱う） |

### 6.4 採用した場合に必要な作業

- ADR（パスの解決の方式）と、C1 の8.3 の書き換えの文案
- 実ファイルの正常な読み書き、競合、リンクや親の入れ替えでの拒否、途中でのプロセスの停止の試験
- 一時ファイルの回収の手順
- `SAFE_RESOLUTION_ADOPTED` を変えるのは、オーナーの承認の後だけ

## 7. 残っている課題

- パスの解決の方式（6章）のレビューと、オーナーの判断。
- 5章の未検証の項目。
- WP-09 から引き継いだ課題（`WP-09_SAFETY_COST.md` の13章、14.11）は、変わらない。
- WP-01 の残っている課題（信頼しない作業ツリーを扱う前の、親のディレクトリを入れ替える競合への対策を含む）は、変わらない。6章の案は、この課題への答えの候補である。ただし、採用していない。

## 8. 確かめていない外部の仕様

- Linux の `openat2` の振る舞い（使っていない）。
- `O_TMPFILE` と `linkat` の組み合わせが、使うファイルシステムで動くこと（6章の案。確かめていない）。
- import-linter の `**` の書き方の正式な仕様（版 2.17 で、すべてのモジュールに当てはまることを、植え込んだ違反の試験で確かめただけ）。

## 9. 第2回：限定的なファイル操作（WP08-R01〜R05）

> この章の状態：**DRAFT**（レビュー前）

### 9.1 進め方と結果

| 段階 | 内容 | コミット | 試験の数 | 8種類の検査 |
|---|---|---|---|---|
| 開始 | 起点 `677201d`。リモートと一致、作業ツリーは空、1552 件が成功、8種類の検査が成功 | — | 1552 | 成功 |
| 1 設計の変更 | ADR-0008、WP08-R01（目録とルートの固定）、WP08-R02（安全な解決、作業領域の生成、単一の書き込み元） | `264655b` | 1617（+65） | 成功 |
| 2 読み取り | WP08-R03（安全な読み取り）、WP08-R04（実行券と実行の結び付け、実行物の固定） | `4b67629` | 1644（+27） | 成功 |
| 3 書き込み・障害試験 | WP08-R05（CREATE、REPLACE）、例外の注入、実際のプロセスの停止、一時ファイルの回収 | この章を含むコミット | 1685（+41） | 成功 |

- 合計で 133 件の試験を加えた（1552 → 1685）。
- 新しい依存関係はない（`uv.lock` は変えていない）。`ctypes` の禁止は維持した。
- **この環境は uid 0（root）で動く。** 権限の検査は、所有者と mode のビットを見るので、root でも成り立つ。ただし、root はビットを無視して書けるので、root のプロセスは、ロックや 0700 で除ける書き込み元ではない（ADR-0008 の限界）。一般の利用者の権限で動かした試験は、していない。

### 9.2 実装したパスの解決（ADR-0008）

1. **管理領域：** 合成の根が与える絶対パス。`/` から1つずつ `O_DIRECTORY | O_NOFOLLOW` で開く。
   - 途中の階層：ディレクトリ。所有者が root かこの利用者。グループか他人が書けるなら、sticky bit が必要。
   - 管理領域：この利用者のもので、0700。
   - どれかが合わなければ `AREA_UNSAFE`。
2. **ルート：** 管理領域の fd からの相対で開く。この利用者のもの、0700（`ROOT_PERMISSIONS`）、管理領域と同じ device（`MOUNT_BOUNDARY`）。リンクなら `ROOT_UNUSABLE`。
3. **目録：** ルートの fd からの相対で開く。絶対パスで開き直すことはない。
   - まず `O_PATH | O_NOFOLLOW` で開き、`fstat` で確かめる：普通のファイル、リンクが1つ、この利用者のもの、グループと他人が書けない、同じ device、1 MiB 以下。
   - 次に、読むために開き、同じ inode であることを確かめる。
   - HMAC、目録のルートのパスと device と inode、Policy の信頼済みの digest を照合する。
4. **作業ツリーの中：** ルートの fd から1つずつ開く。各階層は、この利用者のもの、グループと他人が書けない、ルートと同じ device（合わなければ `PATH_UNSAFE`）。
   - `""`、`.`、`..`、NUL、バックスラッシュ、絶対パス、予約した名前（目録、一時ファイル）は、開く前に拒否する（`PATH_INVALID`）。
   - 予約した名前は、Descriptor を作る段階でも、ツールの対象にできない（`RESOURCE_PATH_RESERVED`）。
5. **検証したルートの fd を、実際の操作まで持ち続ける。** `prepare`（開始の前）で開き、コールバックの中の操作でも同じ fd を使い、`close` で閉じる。ツリーが移動されても、検証したツリーの中で操作する（試験した）。
6. **条件を満たせない場合（S19）：** プラットフォーム、フラグ（`O_PATH` など）、`dir_fd` の対応、`flock` のどれかがなければ、何も開かずに拒否する。Attempt は `ABORTED_BEFORE_DISPATCH`（`SAFE_FILE_ACCESS_UNAVAILABLE`）。`prepare` の後で条件がなくなった場合は、コールバックの中で、何もせずに確定の失敗にする。

### 9.3 単一の書き込み元をどう確保したか

- **協調する書き込み元（Agent Core の Broker）：** ルートの fd に `flock(LOCK_EX | LOCK_NB)` をかけ、`prepare` から `close` まで持つ。2つ目の Broker は、同じプロセスでも別のプロセスでも `WORKSPACE_BUSY` で拒否される。
  - 拒否された Attempt は、開始の前に `ABORTED_BEFORE_DISPATCH`（`WORKSPACE_BUSY`）になり、押さえた額は解放される。
  - 一時ファイルの回収も、同じロックを取る。
- **OS による隔離との違い：** `flock` は、協調する書き込み元どうしの排他にすぎない。ルートと管理領域が 0700 であることで、ほかの OS の利用者を除く。
- **防げないもの：** 同じ OS の利用者で協調しないプロセス、root のプロセスによる変更。OS による隔離は Phase 3A で扱う。
- 管理された作業領域は、`create_synthetic_workspace` で作る：0700 のルート、0600 のファイル、0700 のディレクトリ。目録は最後に書く。すべてのパスを、何かを作る前に検査する。

### 9.4 指摘ごとの結果

| 指摘 | 結果 |
|---|---|
| WP08-R01 目録とルートの固定 | 済み（9.2 の 1〜5）。目録の署名は、作ったときの記録であり、今の中身が正しいことは証明しない。**作ったときの目録と、その後の管理された変更の区別：** 目録は書き換えない。その後の変更は、Journal の中の、`WriteChange` を持つ Descriptor の Attempt のうち COMPLETED のもの（書いた後の中身と大きさを確かめたもの）である。結果不明（`OUTCOME_UNKNOWN`）の変更は、照合（WP-12）まで、管理された変更として扱わない |
| WP08-R02 単一の書き込み元と安全な解決 | 済み（9.2、9.3）。マウントの境界は device の比較だけで、同じファイルシステムのバインドマウントは検出できない（主張しない） |
| WP08-R03 安全な読み取り | 済み。実行券を先に使い、`O_PATH` で見てから読むために開く。FIFO、デバイス、ソケット、シンボリックリンク、ハードリンク、ディレクトリは、読むために開かれない（試験で、開いた回数を数えた）。大きさの上限は、読む前（`fstat`）と読んでいる間の両方で守る。読む前と後で状態（device、inode、大きさ、mtime、ctime）を比べる。結果は read_file の出力にして、出力のスキーマと `max_output_bytes` で確かめる |
| WP08-R04 実行券と実行の結び付け | 済み。実行券は、作業ツリー（`workspace_ref`）、パス、読み書きの種別、書き込みの `WriteChange`（実際のバイトから計算した digest と大きさ、`expected_pre`）、Receipt、Attempt、`action_hash` に結び付く。Broker は、対象のどの部分を開くよりも前に、実行券を使う。COMPLETED を報告するのに実行券を使っていなければ、結果不明にする（`TICKET_NOT_USED`）。**実行物：** builtin のファイルのツール（`read_file`、`write_file`）の信頼する登録は、Broker のコードの `artifact_hash`（読み込んだときのソースの、ドメイン付きの SHA-256）を固定する。実行する前に、登録の値、動いているコード、今ディスクにあるコードが一致することを確かめ、合わなければ `ABORTED_BEFORE_DISPATCH`（`ARTIFACT_MISMATCH`）。Broker のクラスは継承できない。**失敗の区別：** コールバックの中の失敗は、Broker が、自分がどこまで進んだかの記録から `effect`（NONE か UNKNOWN）を付ける。例外の型だけで、実行がなかったとは判定しない |
| WP08-R05 安全な書き込み | 済み。1ファイルの CREATE と REPLACE だけ。次の段落を参照 |

**WP08-R05 の書き込みの手順：**
1. 実行券を使う。
2. 親のディレクトリまで1つずつ開く。
3. 期待する状態を確かめる。
   - CREATE：対象がない（リンクもない）こと。
   - REPLACE：対象を安全に読み、digest が `expected_pre` と一致すること。一致しなければ書かない。
4. 一時ファイルを、同じディレクトリに、予測できない名前（接頭辞と、32桁の16進数の乱数）と `O_CREAT | O_EXCL | O_NOFOLLOW` で作る。
5. 一時ファイルに、部分的な書き込みを繰り返して全部を書き、`fsync` する。
6. 確定する。
   - CREATE：`link`。対象がすでにあれば `EEXIST` で失敗し、上書きしない。
   - REPLACE：同じディレクトリの中での `rename`（原子的）。
7. CREATE なら、一時ファイルの名前を消す。
8. 親のディレクトリを `fsync` し、確定した後の中身と大きさを確かめる。
9. 結果は、ゲートが Journal と費用の台帳に記録する。

**失敗の扱い：**
- 確定の前の失敗：対象は変わらない（NONE）。一時ファイルは消す。消せなければ回収に任せる。
- 確定のときの失敗と、その後の失敗：UNKNOWN。古い状態を推測で戻すことも、自動で再試行することもしない。

**一時ファイルの識別と回収の契約：**
- **識別：** 一時ファイルは、ちょうど「`.agent-core-tmp-` と32桁の16進数」の名前の、この利用者の普通のファイルだけ。この接頭辞は、ツールの対象にできない。
- **回収：** `recover_temporaries` が行う。
  - 書き込み元のロックを取り（操作の最中は `WORKSPACE_BUSY`）、ルートと目録を `prepare` と同じように確かめてから、ツリーを1つずつ巡る。
  - 消すのは、識別に合うものだけ。対象、リンク、よく似た名前は、そのまま残す。
  - 書き込みも、再試行もしない。

### 9.5 試験

- **red の確認：**
  - 段階1：新しい名前を、`NotImplementedError` を返す仮の実装だけにして実行した。新しい 84 件がすべて失敗した。import の失敗ではない。
  - 段階2：同じように実行し、42 件が失敗した。WP-09 のコールバックで、実行券を使うように直した2件を含む。
  - 段階3：同じように実行し、22 件が失敗した。19 件は、実装の前から成功した。拒否されて副作用がないことの試験で、そのときは書き込みそのものを拒否していたためである。これらは、書き込みを実装した後も成功することに意味がある。
- **正常な読み書き：**
  - 読み取り：中身、出力（スキーマ）、Receipt の `USED`、押さえた額 0、ツリーが変わらないこと。
  - CREATE：新しいファイル、0600、リンク1つ、一時ファイルが残らないこと。
  - REPLACE：中身、新しい inode、リンク1つ。
  - 1バイトずつの部分的な書き込みでも、正しく完了する。
  - 移動されたツリーの中で操作すること。
- **拒否（副作用なし）：**
  - CREATE で上書きしない。対象の名前にリンクがある場合も拒否する。
  - `expected_pre` の不一致。対象がない場合。
  - 特殊なファイル（シンボリックリンク、ハードリンク、FIFO、キャラクタデバイス、ソケット、ディレクトリ）。
  - 内容の差し替え：実行券で拒否し、ファイルの操作は0件。
  - 読み書きの大きさの上限の超過。
  - 各階層のシンボリックリンク：管理領域の上、ルート、途中の2階層、最後の部分、目録。
  - 目録の差し替え：別のツリーの正しい目録、偽の MAC、壊れた JSON。
  - 親のディレクトリの入れ替え：`prepare` の後に、リンクに替えた場合。
  - 実行物の改変：別の `artifact_hash` の登録、ディスクのコードの変更（注入）、builtin の一覧にないツール。
- **例外の注入（同じプロセス）：**
  - 確定の前の失敗（`write` の `ENOSPC`、ファイルの `fsync` の `EIO`、`write` が 0 を返す）：確定の失敗（NONE）。一時ファイルは消える。
  - `link` の直前に対象ができる：`EEXIST`、上書きしない、NONE。
  - `rename` と `link` の失敗：UNKNOWN。押さえた額は保たれ、一時ファイルは回収に残る。
  - 確定の後の失敗（ディレクトリの `fsync`、一時ファイルの名前の削除、確定の後の中身の変更）：UNKNOWN。戻さない。
  - 読み取りの I/O エラーと、読んでいる間の変化と増加。
  - マウントの境界（device を変えて見せる注入）。
- **実際のプロセスの停止（SIGKILL）：** 子のプロセスで、CREATE と REPLACE のそれぞれについて、4つの時点で止めた（8件）。
  - 時点：一時ファイルを作った直後、書いた後で `fsync` の前、確定の直前、確定の直後。
  - 止めた後に、親のプロセスで確かめたこと：
    - Attempt は `DISPATCH_STARTED` で、復旧で予約は `UNKNOWN`（押さえた額は保つ）になる。
    - 確定の前に止めた場合：対象は変わらない。確定の後に止めた場合：対象は新しい中身。
    - 一時ファイルは、REPLACE で確定した後を除いて1つ残る。回収はそれだけを消し、CREATE で確定した後のリンクの数は1に戻る。
    - 同じ Attempt を、もう一度実行することは拒否される（ファイルの変更は0件）。
- **同時の操作：** 同じプロセスの2つ目の Broker、別のプロセスが持つロック、書き込みの最中の2つ目の `prepare`。どれも `WORKSPACE_BUSY`。
- **停止と失効：** `prepare` の後の RUN の停止と、アクセスの失効で、開始が拒否されて書かれない。ロックは解放される。生きているゲートの未実行の報告で、押さえた額は解放される。
  - コールバックの最中の停止は、ゲートのロックで、そもそも適用できない（`GATE_BUSY`。WP-09 で試験した）。
- **結果不明のときの自動の再実行の禁止：** 結果不明のときも、プロセスを止めた後も、同じ Attempt はもう実行されない。
- **S19 の維持：** 6つの条件のそれぞれがない場合について、読み取りと書き込みの両方で、何も開かずに拒否する（12件）。作業領域の生成も拒否する。

### 9.6 保証できる範囲と、保証できない範囲

| 保証できる（試験した） | 保証できない、または主張しない |
|---|---|
| 管理領域から下は、文字列の絶対パスを使わず、検証した fd からの相対で開く | `openat2` と同等の安全性（ADR-0008） |
| どの階層でも、シンボリックリンクをたどらない | 同じファイルシステムのバインドマウントの検出 |
| 読むのは、リンクが1つの普通のファイルだけ。特殊なファイルは、読むために開かない | `O_PATH` で見てから読むために開くまでの間の、協調しない書き込み元による入れ替え（開いた後の inode の比較で検出はする） |
| 協調する書き込み元は1つ（`flock`）。ほかの利用者は 0700 で除く | 同じ利用者で協調しないプロセス、root のプロセスからの変更 |
| CREATE は上書きしない（`link` の `EEXIST`） | REPLACE の照合と置き換えの間の、ほかの書き込み元との競合（単一の書き込み元の前提の下でだけ、競合しない） |
| REPLACE は、同じディレクトリの中で原子的（古いか新しいか） | 電源の断の後の永続性（`fsync` は呼ぶが、試験していない） |
| 確定の前の失敗は、対象を変えない。確定の後の失敗は結果不明にし、戻さない | DB の記録とファイルの操作の原子性 |
| 実行物：動いているコードと、ディスクのコードが、登録の `artifact_hash` と一致すること | 同じプロセスの中で、コードを書き換えられること（信頼するプロセスの中の問題） |

### 9.7 未検証の項目と限界

- 一般の利用者（uid 0 でない）で動かした試験（9.1）。
- 実際のマウントの境界（注入だけで試験した）。
- 電源の断と、ファイルシステムの障害の後の永続性。
- ゲートの `start` と `finish` を別々に呼ぶ、WP-09 の合成の経路では、実行券は渡されない。そのため、Broker のファイルの操作は、その経路では行えない。ただし、その経路で、ファイルを対象とする操作の COMPLETED を報告することは、今もできる（合成の結果の報告。実際のファイルの操作ではない）。
- 作業領域の生成が途中で失敗した場合、目録のないツリーが残る（`prepare` は拒否する。推測では消さない）。
- 一時ファイルの回収は、合成の根が明示的に呼ぶ。起動のときに自動で呼ぶ仕組みは、まだない。
- 結果不明の操作の照合は WP-12。
- 目録の `entries` と、今の中身の照合はしていない（9.4 の R01 の区別による）。

### 9.8 変更したファイル

- 文書：`docs/adr/0008-dir-fd-path-resolution.md`（新規）、`docs/adr/README.md`、この文書、`docs/STATUS.md`
- 本体：`src/agent_core/adapters/file_broker.py`、`ports/files.py`、`runtime/execution_gate.py`、`runtime/file_execution.py`、`safety/descriptor.py`、`types/errors.py`、`types/files.py`、`types/safety.py`、`types/transitions.py`
- 試験：`tests/unit/test_wp08_workspace.py`（新規）、`test_wp08_read.py`（新規）、`test_wp08_write.py`（新規）、`test_wp08_files.py`、`test_wp09_execution.py`、`tests/support/file_child.py`（新規）、`files_world.py`、`ledger_world.py`、`safety_world.py`
- C1_SAFETY.md の8.3 は書き換えていない。限定的な変更は ADR-0008 に記録した（Phase 0 の設計資料は、設計のブランチの文書のため）。

