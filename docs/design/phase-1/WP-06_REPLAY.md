# WP-06：Replay Provider（実装の記録）

> 文書の状態：**DRAFT**（ChatGPT のレビューを待っている）　作成日：2026-11-26
>
> - この文書は、WP-06 で実装した内容と、**設計資料に書かれていなかったために、WP-06 で決めたこと**を記録する。
> - 正本の規則は `E_INTEGRATION.md` の28章に従う。この文書は、設計資料を上書きしない。
> - 実装：`src/agent_core/types/replay.py`（型）、`src/agent_core/ports/replay.py`（接続口）、`src/agent_core/adapters/replay_model.py`（`ReplayModelProvider`、`DenyAllReplayAccess`）。
> - **Phase 1 で扱うのは、合成の記録（`origin=SYNTHETIC`）だけ。** 実 API の記録も呼び出しも行わない。実 API の費用は 0 ドル。

## 1. 構成

```
agent_core.runtime
        ↓
agent_core.adapters（replay_model） | identity | tool | observability
        ↓
agent_core.ports（ModelPort、ReplayBlobStore、ReplayAccessPort）→ agent_core.types
```

- `ReplayModelProvider` は、既存の `ModelPort`（`complete(request) -> ModelResponse`）を満たす。Runtime からは、Fake Provider と同じ形で使える。
- import-linter の契約で、`runtime` が `adapters.replay_model` を直接 import できないこと、`adapters` が `runtime` を import できないことを、試験で確かめた。
- 依存関係は加えていない（標準ライブラリの `hashlib` と Pydantic だけ）。

## 2. 型と記録の形式

| 型 | 内容 |
|---|---|
| `ReplayRecordId` | 新しい ID の種類 `rpl`（`rpl_<site>_<32hex>`） |
| `ReplayOrigin` | `SYNTHETIC`、`LIVE_APPROVED`。**Phase 1 は `SYNTHETIC` だけを受け付ける**（`LIVE_APPROVED` は Phase 3B 以降） |
| `ReplayRecord` | 記録の形式の版（`format_version`、今は 1 だけ）、`record_id`、`origin`、`tenant_id`、元の Run（`source_run_id`）、呼び出しの並び（`steps`）。並びは 1 から n の連番で、元の要求の ID は重複しない |
| `ReplayStep` | 順番、元の `model_request_id` と `attempt_id`、IR・描画した文脈・要求の記録への参照（内容のアドレス）、B1 の6.2 の3つのハッシュ、鍵の版、結果 |
| 結果 | `RecordedResponse`（応答の本文への参照）か、`RecordedFailure`（失敗の種類と、元の呼び出しの Usage）。識別の欄（`kind`）で区別する閉じた型 |
| `ReplayRequestRecord` | 送った内容の記録（B1 の6.2 の `request_record_ref`）。プロファイル、生成の設定、ツールの定義、描画のハッシュ。認証情報、要求の ID、時刻は入れない |
| `ReplayManifest`、`ReplayManifestEntry` | 信頼する固定の一覧（3章） |
| `ReplayAccessDecision` | 許すかどうかと理由。拒否には必ず理由があり、許可には理由がない |
| `ReplayUnavailableError` | `REPLAY_UNAVAILABLE`。決まった理由のコード（`ReplayReason`）だけを持つ。送信していない（`NOT_SENT`）。トークンは 0、費用は確定した 0 |

- **本文は記録に入れない。** 記録は、内容のアドレス（保存したバイト列の `sha256:`）で本文を参照する。
- **知らない形式の版は拒否する**（`UNKNOWN_FORMAT`）。版を確かめるのは、記録を型として読む前である。

## 3. 信頼の起点（固定した一覧）

- 記録の中のハッシュを、すべて矛盾なく計算し直した記録でも、それだけでは受け入れない。**信頼の起点は、固定した `ReplayManifest` である。**
- 一覧の項目は、記録の ID、テナント、記録の本文のアドレス、記録のハッシュ（`replay_record_hash`）を固定する。
  - `replay_record_hash` は、ドメインのタグ（`agent-core/replay-record/v1\0`）を付けた、正規化した記録の SHA-256。
- 照合の順序（Provider を作るとき）：
  1. 一覧に記録がある（`RECORD_MISSING`）。テナントが一致する（`TENANT_MISMATCH`）。
  2. 記録の本文を読む**前に**、アクセスを確かめる（5章）。
  3. 本文を読む（ない：`DATA_MISSING`）。厳密な JSON として読み、形式の版を確かめる（`UNKNOWN_FORMAT`）。
  4. 本文のアドレスが一覧と一致する。型として正しい。`replay_record_hash` と記録の ID が一覧と一致する（どれかが外れたら `MANIFEST_MISMATCH`）。
  5. 記録のテナント（`TENANT_MISMATCH`）、由来が `SYNTHETIC`（`ORIGIN_NOT_ALLOWED`）、Replay の Run が元の Run と違う（`SOURCE_RUN_REUSED`）。
- **Phase 1 の一覧は、試験の中の固定の合成のデータである。** 署名と、独立した証人による保証は、後の段階で加える（9章）。

## 4. ハッシュの計算と照合

呼び出しのたびに、次の段の記録と照合する。**同じ文字列であることだけでは、通さない。**

1. 要求の3つのハッシュ（`context_hash`、`rendering_hash`、`request_hash`）が、次の段を指す。ほかの段を指すなら `OUT_OF_ORDER`、どの段でもなければ `REQUEST_MISMATCH`。推測で近い段を選ぶことはない。
2. その段のすべての本文について、読む**前に**アクセスを確かめる。
3. 3つのハッシュを、**それぞれが表す保存したデータから、別々に計算し直す。**
   - `context_hash`：IR の本文から（`CONTEXT_HASH_MISMATCH`）。
   - `rendering_hash`：描画した文脈の本文から（`RENDERING_HASH_MISMATCH`）。
   - `request_hash`：要求の記録の本文から（`REQUEST_HASH_MISMATCH`）。
4. 要求の記録を厳密に読み、その中の `rendering_hash` が、3で確かめた描画のハッシュと一致する（`REQUEST_RECORD_INCONSISTENT`）。
5. 今の要求のプロファイル、生成の設定、ツールの定義が、要求の記録と一致する（`PROFILE_MISMATCH`）。
6. 記録した応答について：
   - 本文のアドレスが一致する。厳密な JSON として読め、`ModelResponse` として正しい。応答の `model_request_id` が、その段の元の要求の ID と一致する（**応答と要求の結び付き**）。どれかが外れたら `RESPONSE_MISMATCH`。
   - 応答の本文や Usage を書き換えると、アドレスが変わるので見つかる。
7. 結果を返す前に、もう一度アクセスを確かめる（`ACCESS_REVOKED`。5章）。

- 記録した失敗と、その Usage は、記録の本文に入っている。そのため、記録のハッシュ（3章）で改変を見つける。
- 試験では、記録の中のハッシュの文字列を変えずに本文だけを変えると拒否されること（`CONTEXT_HASH_MISMATCH`）、記録の中のすべてのハッシュを計算し直しても、固定した一覧と合わなければ拒否されること（`MANIFEST_MISMATCH`）を確かめた。

## 5. アクセス、削除、鍵の版の確認（E.Z14）

- `ReplayAccessPort.check(tenant_id, refs, key_version)` が、今のアクセスの判定を返す。削除（`DELETED`）、鍵の版が使えない（`KEY_UNAVAILABLE`）、拒否（`ACCESS_DENIED`）を区別する。
- **確かめる時点：**
  - 記録の本文を読む前（Provider を作るとき）。
  - 段の本文（IR、描画、要求の記録、応答）を読む前。拒否されたら、どの本文も読まない（試験で、読み出しが起きないことを確かめた）。
  - 結果を返す前。照合の途中で取り消されたら `ACCESS_REVOKED` で止める。
- **他のテナントの記録は使えない：** 一覧の項目と記録の両方のテナントを照合する。本文の保存も、テナントごとに分かれる。
- **確かめる仕組みがなければ拒否する：** 既定の `DenyAllReplayAccess` は、いつも `ACCESS_DENIED` を返す。判定が正しい型でない場合も拒否とする。
- 本格的な鍵の管理、削除の伝播、Memory Engine は実装していない（WP-14、AUDIT-03）。

## 6. Replay を続けられない条件

- `ReplayUnavailableError` を出したら、その Provider は止まる。それ以降の呼び出しも、同じ理由で拒否する。
- 理由は決まったコード（`ReplayReason`）だけ。エラーに本文、要求の内容、保存した値を入れない（試験で、目印の値がトレースバックに出ないことを確かめた）。
- **代わりのもので埋めない：** 今のファイル、今の Memory、別の写し、別の保存場所、Fake Provider、実際のモデルのどれも使わない。Provider は、代わりの経路を一切持たない（試験で確かめた）。
- **記録を変えない：** 記録と本文の保存に書き込まない（試験で、Replay の前後で保存の内容が同じことを確かめた）。
- **順番どおりに進め、尽きたら止める**（`EXHAUSTED`）。
- **ツールの要求を実行しない：** 記録した応答の中の `ToolUseBlock` は、データとして返すだけである。実行の経路はない（WP-07 の提案と同じ扱い）。

## 7. 決定性と Usage

- **決定性：** 同じ記録と同じ要求の並びからは、いつも同じ結果になる（試験で、2回の Replay の結果が等しいことを確かめた）。時刻や乱数を使わない。
- **要求の ID の対応：** 返す応答の `model_request_id` は、今の要求の ID にする。元の要求の ID との対応は、`source_of(今の ID)` で明示的に得られる。
- **記録した失敗と、結果が分からない呼び出しを再現する：**

| 記録した失敗 | 再現するエラー |
|---|---|
| `NOT_SENT` | `ModelNotSentError` |
| `FAILED_DEFINITE` | `ModelDefiniteFailureError` |
| `OUTCOME_UNKNOWN` | `ModelOutcomeUnknownError` |
| `TIMEOUT` | `ModelTimeoutError` |
| `RESPONSE_INVALID` | `ModelResponseInvalidError` |

  再現したエラーは `ReplayUnavailableError` ではない（Replay は正しく続いている）。

- **Usage：**
  - Replay の呼び出しそのものの Usage は、トークン 0、費用は確定した 0（XTU）、`synthetic`。**費用は 0 ドル。**
  - 元の呼び出しの Usage は、`source_usage(今の ID)` で、記録のまま、変えずに得られる。Replay の Usage とは別に持つ。
  - そのため、元の費用を二重に数えない。元の Usage が「不明」なら、不明のまま返す（0 とみなさない）。

## 8. Rebuild との区別

| | Replay（WP-06） | Rebuild |
|---|---|---|
| 目的 | 記録した呼び出しを、記録どおりに再現する | 今の資料から、文脈を作り直す |
| 本文の出どころ | 固定した記録の本文だけ | 今の Memory やファイル |
| 合わないとき | `REPLAY_UNAVAILABLE` で止まる | 作り直した結果として扱う |

- **Replay は Rebuild に切り替わらない。** 記録が欠けていても、今のデータで埋めて Replay と呼ぶことはない。Rebuild は WP-06 では実装していない。

## 9. 保証できないこと（主張しないこと）

- **ハッシュは、鍵のない SHA-256 で、合成のデータだけに使う。** 実データでは、鍵を使う HMAC が必要である（C2 の6.2、WP-14）。鍵のないハッシュは、推測できる内容の確認に使われうるため。
- **正規化した JSON（`dump_json`）が RFC 8785 に適合することは、確かめていない。** Phase 1 の内部の形式である。
- **固定した一覧の署名と、独立した証人は、まだない。** 一覧を差し替えられる者からは守れない。一覧の信頼は、Phase 1 では、試験の固定のデータであることに頼る。
- `ReplayAccessPort` の判定が正しいことは、Port の実装に頼る。本格的な実装（鍵の管理、削除の伝播）は後の段階。
- 実際のプロバイダーの記録の形式との互換は、確かめていない（実 API を使わないため）。

## 10. 設計資料との対応

| 設計 | 実装 |
|---|---|
| B1 の6.2（3つのハッシュと、要求の記録の参照） | `ReplayStep`、`ReplayRequestRecord`、4章 |
| B1 の6.3、B1.C10（記録どおりの再現。合わなければ止まる） | 4・6章 |
| B1.C24（`REPLAY_UNAVAILABLE` と決まった理由） | `ReplayUnavailableError`、`ReplayReason` |
| A の1.1（合成と、承認された実データの区別） | `ReplayOrigin`。Phase 1 は `SYNTHETIC` だけ |
| E.Z14（本文を読む前のアクセス、削除、鍵の版の確認） | 5章 |
| PHASE_1 の WP-06（合成の記録の Replay） | 全体 |

## 11. 設計資料にない判断（WP-06 で決めたこと）

| 判断 | 理由 |
|---|---|
| 本文は記録に入れず、内容のアドレスで参照する | 本文の削除やアクセスの確認を、記録と分けて行うため |
| 記録のハッシュに、ドメインのタグ `agent-core/replay-record/v1\0` を付ける | 他のハッシュと混同しないため |
| 固定した一覧を、信頼の起点にする | 記録の中で矛盾なく計算し直した偽の記録を拒否するため |
| 一度 `REPLAY_UNAVAILABLE` になった Provider は止まったままにする | 欠けた部分を飛ばして続けないため |
| 返す応答の ID は今の要求の ID にし、元の ID は対応表で持つ | Runtime の照合（WP-05）と整合させ、元の記録を変えないため |
| 記録した失敗の Usage は記録に、応答の Usage は応答の本文に持つ | どちらも改変を見つけられる場所に置くため |

## 12. 検証の結果

- **TDD：** 試験を先に書き、モジュールがない状態で失敗する（収集の失敗）ことを確かめてから、実装した。
- WP-06 で 33 件と、import-linter の契約の試験 2 件を加えた（合計 1237 件）。
- 8種類の品質の検査が、すべて成功した。試験の中では、ネットワークへの接続を遮断している。外部への通信は発生しない。
- 実装の途中で、試験の誤り2件を直した（決定性の試験で、呼び出しのたびに新しい Provider を作っていたこと。一覧の照合の試験で、保存した記録を書き換えていなかったこと）。期待する結果は弱めていない。一覧と合わない記録は、Provider を作る時点で拒否されることにした（より早い時点）。
