# WP-03：SQLite の Journal（実装の記録）

> 文書の状態：**DRAFT**（ChatGPT によるコードレビューを待っている）　作成日：2026-11-26
>
> - この文書は、WP-03 で実装した内容と、**設計資料に書かれていなかったために、WP-03 で決めたこと**を記録する。
> - 正本の規則は `E_INTEGRATION.md` の28章に従う。この文書は、設計資料を上書きしない。
> - 実装：`src/agent_core/adapters/sqlite_journal.py`、`src/agent_core/adapters/runtime_lock.py`、`src/agent_core/types/event_hash.py`。

## 1. 構成

- Journal の具体的な実装は、Adapter の側（`agent_core.adapters`）に置いた。共通の型と Ports は、SQLite と OS のロックに依存しない。
  - 次の2つを、import-linter の契約で強制する。
    - 層の契約（`(adapters | identity) → ports → types`）
    - `core-independent-of-storage`（`types`、`ports`、`identity` から、`sqlite3`、`fcntl`、`agent_core.adapters` への import を禁止する）
- Port：`JournalPort`（`append`、`read_tenant`、`read_run`、`head`）と、`JournalVerifier`（`verify_tenant`）。`read_run` と `JournalVerifier` は、WP-03 で加えた。
- 依存関係は加えていない（標準ライブラリの `sqlite3`、`fcntl` だけを使う）。

## 2. 接続の設定

| 設定 | 値 | 理由 |
|---|---|---|
| `journal_mode` | `WAL` | ADR-0002、A の5.3。有効にできなければ、開かない |
| `synchronous` | `FULL` | Journal は正本である。WAL と `NORMAL` の組み合わせでは、電源が落ちたときに、確定した最後のトランザクションが失われうる。`FULL` は、コミットのたびに WAL を同期する。書き込みは速くなくてよいため、耐久性を優先した |
| `busy_timeout` | 5000 ms | 書き込むのは、OS のロックを持つ1つのプロセスだけ。待ちが生じるのは、外部からの短い読み取り（将来のバックアップなど）だけ。それより長く待つ場合は、異常として扱う |
| `foreign_keys` | `ON` | 表の間の参照を、DB でも強制する |
| `trusted_schema` | `OFF` | スキーマに仕込まれた関数などを信用しない |
| `isolation_level` | `None`（自動のトランザクションを使わない） | `BEGIN IMMEDIATE`、`COMMIT`、`ROLLBACK` を明示的に書く |
| SQLite の版 | 3.37 以上（STRICT の表のため） | 確かめた環境では 3.45.1（Python 3.12） |

## 3. スキーマ（版 1）

- ファイルの識別：`PRAGMA application_id = 0x41434A31`（ASCII の "ACJ1"）。
- スキーマの版：`PRAGMA user_version = 1`。
- 開くときに、次を確かめる。
  - `application_id` と `user_version` が一致する。
  - スキーマのすべてのオブジェクト（表、索引、トリガー）が、版 1 の DDL と完全に一致する。
  - 一致しなければ、`JournalSchemaError` で開かない（未知の版、ほかのアプリケーションの DB、追加の表やトリガーを拒否する）。
- 新しい DB（`application_id` も `user_version` も 0 で、オブジェクトがない）だけを、初期化する。

| 表 | 列 | 制約 |
|---|---|---|
| `runtime_owner` | `singleton`、`epoch`、`instance_id`、`acquired_at_ms` | 行は1つだけ（`singleton = 1`）。`epoch >= 0` |
| `tenant_head` | `tenant_id`、`tenant_seq`、`head_hash` | `tenant_id` が主キー。`tenant_seq >= 1` |
| `run_head` | `run_id`、`tenant_id`、`run_seq` | `run_id` が主キー。`tenant_id` は `tenant_head` を参照する。`run_seq >= 1` |
| `events` | `tenant_id`、`tenant_seq`、`event_id`、`run_id`、`run_seq`、`hash_version`、`event_hash`、`body` | 主キーは `(tenant_id, tenant_seq)`。`event_id` は UNIQUE。`(run_id, run_seq)` は UNIQUE。`run_id` と `run_seq` は、両方あるか、両方ないか |

- すべて STRICT の表（型を強制する）。
- 外部キーは `DEFERRABLE INITIALLY DEFERRED`（コミットのときに確かめる）。
- `body` は、Event の正規化した JSON（`dump_json`）。列の値は、`body` の中身の写しで、読むときに一致を確かめる。
- **本文や秘密情報は入れない。** Event には、本文への参照（`refs`）と、鍵付きのハッシュ（`keyed_hashes`）だけを入れる（E の8章、15.1）。今は合成のデータだけを使う。

## 4. 追記のトランザクション

`append(draft)` は、**1つの `BEGIN IMMEDIATE` のトランザクション**で、次を順に行う。

1. （トランザクションの前）`draft` の型と、`draft.owner_epoch` が今の所有者の epoch と一致することを確かめる。一致しなければ `StaleEventEpochError`。
2. （トランザクションの前）OS のロックのファイルが、取得したものと同じであることを確かめる（`RuntimeLockLostError`）。
3. `BEGIN IMMEDIATE`。
4. `runtime_owner` の `epoch` と `instance_id` が、自分のものと一致することを確かめる（`OwnerEpochMismatchError`）。
5. `tenant_head` から、次の `tenant_seq` と `prev_hash` を決める。
6. `run_id` があれば、`run_head` から、次の `run_seq` を決める。その Run が、ほかのテナントに結び付いていれば拒否する（`RunTenantMismatchError`）。
7. 64 ビットの上限に達していれば拒否する（`SequenceExhaustedError`）。
8. Event を作り、イベントのハッシュを計算する。
9. `events` に挿入する。`event_id` が重複していれば、UNIQUE の制約で検出する（`DuplicateIdError`）。
10. `tenant_head` と `run_head` を更新する。
11. `COMMIT`。

**失敗したとき**

| 失敗 | 扱い | インスタンス |
|---|---|---|
| `event_id` の重複（UNIQUE の制約の違反で、`SQLITE_CONSTRAINT_UNIQUE` かつ `events.event_id`） | `ROLLBACK` して `DuplicateIdError`。何も保存されていないことが確かなので、`IdGenerator.issue_unique` が別の ID で再試行してよい | 使い続けられる |
| Run とテナントの不一致 | `ROLLBACK` して `RunTenantMismatchError` | 使い続けられる |
| 所有者の不一致、seq の上限、保存された head の不正 | `ROLLBACK` して、型付きの致命的なエラー | **止める**（以後は `JournalClosedError`） |
| 重複以外の DB のエラー（ほかの制約の違反、I/O など） | `ROLLBACK` して `JournalStorageError`。**`DuplicateIdError` としては扱わない** | 止める |
| Journal のエラー以外の例外 | `ROLLBACK` して、元の例外をそのまま出す | 使い続けられる |
| `ROLLBACK` の失敗、または `COMMIT` の失敗 | `JournalWriteOutcomeUnknownError`（結果が分からない）。**別の ID で再試行しない** | 止める |

- どの失敗でも、トランザクション全体を取り消すので、途中の行や、seq の欠番は残らない。
- **将来の費用の台帳：** 書き込みのトランザクションは、`_write_transaction` という1つの単位にした。WP-09 で、費用の台帳の更新を、同じ SQLite のトランザクションに加えられる（ADR-0002、E の15.1）。WP-03 では、費用の台帳は実装していない。

## 5. seq とハッシュの連鎖

- `tenant_seq` は、テナントごとに 1 から始まり、欠番なく増える。`run_seq` は、Run ごとに 1 から始まり、欠番なく増える。`run_id` のないイベントには、`run_seq` を付けない。
- 1つの `run_id` は、1つのテナントにだけ結び付く。
- 順序は seq だけで決める。ID の時刻は使わない。

**イベントのハッシュ（版 1。`agent_core.types.event_hash`）**

```
event_hash = "sha256:" + hex(SHA-256(b"agent-core/event-hash/v1\x00" || dump_json(event)))
```

| 項目 | 内容 |
|---|---|
| 入力 | 保存する Event の全体。`tenant_seq`、`run_seq`、`prev_hash` を含む |
| 連鎖の範囲 | テナントごとに1本。`tenant_seq` の順。`tenant_seq` が 1 のイベントだけ、`prev_hash` がない |
| 正規化 | `dump_json`（鍵を並べ、空白のない UTF-8 の JSON）。Phase 1 の内部の形で、RFC 8785 への準拠は確かめていない |
| 版 | ドメインを分けるタグに、版を入れた。各行に `hash_version`（今は 1）を保存する。未知の版は拒否する |

**読み取りのときの検証**

- `read_tenant`、`read_run`、`head`、`verify_tenant` は、返す行をすべて検証する。
  - 保存した `body` を厳密に復元できること（未知の `schema_version` を含め、不正なものを拒否する）。
  - `body` が正規化した形であること。
  - 列の値が、`body` と一致すること。
  - ハッシュを計算し直すと、保存したハッシュと一致すること。
  - `prev_hash` が、直前の行のハッシュと一致すること。
  - seq が連続していること。
  - head が、最後のイベントと一致すること。
- `read_tenant` は、開始の位置の直前の行も読み、最初に返すイベントの `prev_hash` を、検証した行と比べる。返した件数が `limit` より少なければ、最後の行が head と一致することも確かめる。
- `verify_tenant` は、SQLite の `quick_check` と、テナントの全体（連鎖、seq、Run の head、Run とテナントの結び付き、テナントの head）を確かめる。
- どれかが合わなければ `JournalCorruptionError` を出し、正常な Event としては返さない。

**ハッシュの連鎖の限界（記録）**

- 攻撃者が、連鎖の全体を、整合するように書き換えた場合は、検出できない。
- DB の全体を、古いが整合した版に戻した場合も、検出できない。
- これには、独立した証人か、署名したチェックポイントが必要である（E の21章）。**WP-03 では実装していない。**

## 6. 書き込みの排他と epoch（A の5.3、A11。Linux）

**OS のロック（`RuntimeLock`）**

- DB の隣の `<db>.lock` を、シンボリックリンクをたどらずに開く（`O_NOFOLLOW`）。
- `flock(LOCK_EX | LOCK_NB)` で、待たずに取得する。取れなければ `RuntimeLockHeldError` で、2つ目の Runtime を起動しない。
- ロックは、`close` まで持ち続ける。プロセスが異常終了したら、カーネルが解放する（試験で確かめた）。
- ファイルに書く PID は表示のためだけで、判定には使わない。
- 取得した後と、毎回の書き込みの前に、パスが、ロックした inode と同じものを指しているかを確かめる。ロックのファイルが削除されたり、置き換えられたりしていれば、`RuntimeLockLostError` で止める（別のプロセスが、新しいファイルでロックを取れてしまうため）。

**所有者の記録（`runtime_owner`）**

- ロックを取った後、開くときの `BEGIN IMMEDIATE` のトランザクションで、`epoch` を 1 増やし、自分の `instance_id`（OS の乱数による 128 ビット。PID ではない）を記録する。
- 毎回の書き込みのトランザクションの中で、`epoch` と `instance_id` が自分のものと一致することを確かめる。一致しなければ、`OwnerEpochMismatchError`（致命的）で止める。
- Event の `owner_epoch` は、今の所有者の epoch と一致しなければならない。
- epoch が 64 ビットの上限に達していれば、開かない（`SequenceExhaustedError`）。

**限界**

- `flock` は、同じカーネルの中でしか効かない。ネットワークのファイルシステムの上では信頼できない。DB とロックのファイルは、ローカルのファイルシステムに置く（A の5.3）。
- Agent Core のコードを使わずに、SQLite のファイルを直接書き換えることは、防げない。その場合も、epoch とハッシュの検証で、書き換えを見つける作りにした。

## 7. 設計資料との対応

| 設計 | 実装 |
|---|---|
| A の2章（SQLite の WAL、イベントの項目、`prev_hash`） | 2〜5章 |
| A の5.3、A11（3層の排他） | 6章 |
| A.I1（追記だけ、seq の隙間なし、連鎖が途切れない） | 4・5章と、改ざん・欠損・並べ替えの試験 |
| A.I3（有効な epoch を持つ1つの主体だけが書く） | 6章 |
| A.I18（書けるプロセスは常に1つ。異常終了したらロックを OS が解放する） | 6章と、別のプロセスの試験 |
| E の2.2（UNIQUE の制約、重複のときの再試行） | 4章。`DuplicateIdError` は、確実に何も保存していないときだけ出す |
| E の2.3（`event_id`、`run_seq`、`tenant_seq` の役割） | 5章 |
| E の15.1（Journal は本文を持たない。費用の台帳は同じトランザクション） | 3章、4章 |
| ADR-0002 | 全体 |

## 8. 設計資料にない判断（WP-03 で決めたこと）

| 判断 | 理由 |
|---|---|
| `synchronous = FULL`、`busy_timeout = 5000 ms` | 2章 |
| `application_id` と、スキーマのオブジェクトの完全な一致の検査 | ほかの DB と、手を加えられたスキーマを、開く前に拒否するため |
| イベントのハッシュの仕様（5章） | 設計は `prev_hash` を求めているが、計算の方法を決めていない |
| Event の `owner_epoch` を、今の epoch と一致させる | 古い所有者が作ったイベントを、新しい所有者の Journal に入れないため |
| 致命的なエラーの後は、インスタンスを止める | 結果が分からない状態で書き続けないため。再起動して、検証してから使う |
| `read_run` と `JournalVerifier` を加えた | WP-04 の状態機械と、復旧が使う |
| 読み取りの件数の上限は 1000 | 1回の読み取りの量を限るため |

## 9. 検証の結果

手元の環境：Ubuntu 24.04、Python 3.12.3、SQLite 3.45.1。

| 種類 | 内容 |
|---|---|
| **実際のプロセスを使った試験**（3件） | 別のプロセスがロックを持つ間、2つ目の Runtime を拒否する。持ち主のプロセスを SIGKILL で止めると、カーネルがロックを解放し、新しい所有者が epoch 2 で再開する。**トランザクションの途中（イベントの行を書いた後、head を更新する前）で SIGKILL** すると、再び開いたときに、途中の行も欠番も残らず、次の追記は `tenant_seq` 3 になる |
| **例外の注入だけの試験** | `_insert_event` の後や `_update_heads` の後の例外で、すべて取り消されること。`COMMIT` の失敗が、結果不明として扱われること。トリガーによる、重複以外の制約の違反が、`DuplicateIdError` として扱われないこと |
| **DB を直接書き換えた試験** | 本文の改ざん。本文を改ざんして、その行のハッシュも計算し直した場合（連鎖で検出する）。行の削除（途中と末尾）。並べ替え。列と本文の不一致。未知の `schema_version`。未知のハッシュの版。正規化していない本文。未知のスキーマの版。ほかの DB。追加の表。所有者の書き換え（epoch の増加と、`instance_id` の変更）。seq と epoch の 64 ビットの上限 |
| そのほか | 新しい DB の初期化、WAL などの設定、追記と読み取り、テナントと Run の分離、再起動の後の seq と head、閉じた Journal の拒否、ロックのファイルの削除と置き換え、不正な読み取りの引数 |

## 10. 確かめていないこと

- 電源が落ちた場合と、OS が異常終了した場合の耐久性（`synchronous = FULL` の効果）。試験は、プロセスの SIGKILL だけ。
- ディスクが一杯の場合と、実際の I/O のエラー（`COMMIT` の失敗は、例外の注入だけで試した）。
- ネットワークのファイルシステム、Linux 以外の OS。
- 連鎖の全体の書き換えと、DB の全体を古い版に戻すことの検出（独立した証人が必要。E の21章。WP-03 の範囲外）。
- 親のディレクトリを入れ替える競合（ロックのファイルとは別。WP-01 から引き継いだ課題）。
- GitHub Actions の上での実行（CI は未実行）。
