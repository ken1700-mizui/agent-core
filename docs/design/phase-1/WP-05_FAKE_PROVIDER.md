# WP-05：Fake Provider、モデルの共通の型、ModelPort（実装の記録）

> 文書の状態：**DRAFT**（ChatGPT によるコードレビューを待っている）　作成日：2026-11-26
>
> - この文書は、WP-05 で実装した内容と、**設計資料に書かれていなかったために、WP-05 で決めたこと**を記録する。
> - 正本の規則は `E_INTEGRATION.md` の28章に従う。この文書は、設計資料を上書きしない。
> - ADR-0003（独自の ModelPort）は PROPOSED のまま変えていない。実モデルへの接続は、承認されていない。
> - 実装：`src/agent_core/types/model.py`、`src/agent_core/types/model_errors.py`、`src/agent_core/ports/model.py`、`src/agent_core/adapters/fake_model.py`。

## 1. 構成と依存の方向

```
adapters.fake_model  →  ports.model  →  types.model, types.model_errors
runtime（将来、ModelPort を使う）→ ports.model（adapters は import できない）
```

- import-linter の契約で強制する。`runtime` と `ports` から `adapters.fake_model` への import は、検出されることを試験で確かめた。
- 依存関係は加えていない。Anthropic SDK と OpenAI SDK は、入れていない。

## 2. 共通の型（E の7.4・8章、B1 の2.4・6.2）

| 型 | 主な項目 | 制約 |
|---|---|---|
| `TextBlock` | `type="text"`、`text` | 空でない。NUL を含まない。100万文字まで |
| `ToolUseBlock` | `id`、`name`、`arguments` | `arguments` は、正規化した JSON のオブジェクトの文字列（鍵を並べ、空白なし）。重複した鍵、NaN、オブジェクト以外を拒否する |
| `ToolResultBlock` | `tool_use_id`、`content`（テキスト）、`is_error` | 前にある `tool_use` に答えるものだけ |
| `ContentBlock` | 上の3種類 | `type` で区別する。画像などの未知の種類は拒否する |
| `Message` | `role`（`USER`、`ASSISTANT`）、`content` | ツールの要求は `ASSISTANT` だけ、ツールの結果は `USER` だけ |
| `ModelProfile` | `provider`、`model_id`、`deployment`、`context_window`、`max_output`、`token_accounting`、`tokenizer_method`、`cache_capability`、`billing_scheme_ref`、`profile_version` | B1 の2.4 の一部。`max_output` は `context_window` 以下。Phase 1 の値は合成 |
| `ProfileRef` | `model_id`、`profile_version` | E の8章の `profile@version` |
| `GenerationParams` | `max_output_tokens`、`temperature_milli`、`timeout_ms` | 整数だけ（温度は 1000 分の 1 の単位） |
| `ModelRequest` | `model_request_id`、`attempt_id`、`profile`、`messages`、`tools`、`params`、`context_hash`、`rendering_hash`、`request_hash` | 1つの Attempt に属する。ツールの名前と `tool_use` の ID は重複しない |
| `ModelResponse` | `model_request_id`、`content`（テキストとツールの要求）、`stop_reason`、`usage`、`provider_request_id`、`final_usage` | `stop_reason` が `TOOL_USE` のときだけ、ツールの要求がある。`final_usage` は Usage の状態と一致する |
| `Usage` | 7章 | 7章 |
| `ModelRequestId` | 接頭辞 `mrq` | WP-02 の ID の形式 |

- どれも WP-02 の基本の型（strict、不変、未知の欄の拒否、正規化した書き出し）に従う。
- **3つのハッシュは、別の型にした。** 接頭辞が違う（`ctxhash-v1:sha256:`、`renderhash-v1:sha256:`、`reqhash-v1:sha256:`）ので、取り違えると検証で拒否される。
  - B1 の6.2 の対応：`context_hash` は正規化した IR、`rendering_hash` はモデル向けに変換した文脈、`request_hash` はリクエストの記録。
  - 計算する対象の正規化は WP-14（Context）で決める。WP-05 では、型と対応だけを定めた。
- **モデルの出力と、実行の権限を分けた。** `ModelResponse` はデータにすぎず、実行や認可の方法を持たない。ツールの要求を実行するには、新しい Attempt、認可、Permit が必要である（C1、E の7.4）。

## 3. ModelPort（`ports/model.py`）

- `complete(request: ModelRequest) -> ModelResponse` だけ。プロバイダーに依存しない。
- 失敗は、すべて `ModelError` の型付きのエラーにする。各エラーは、結果（`outcome`）と、その時点で分かっている使用量（`usage`）を持つ。

| エラー | `outcome` | 使用量 |
|---|---|---|
| `ModelInvalidRequestError`、`ModelUnsupportedError`、`FakeScriptMissError` | `NOT_SENT`（何も送っていない） | 確定した 0 |
| `ModelDefiniteFailureError` | `FAILED_DEFINITE`（プロバイダーが処理していないことが確か） | 確定した 0 |
| `ModelOutcomeUnknownError`、`ModelTimeoutError`、`ModelResponseInvalidError` | `UNKNOWN`（処理され、課金された可能性がある） | 不明、または一部だけ（確定した値にはしない） |

- Adapter は、プロバイダー固有のエラーを、この型に変える（Fake では、合成のプロバイダーの例外を `ModelDefiniteFailureError` に変える試験をした）。ほかの例外が出たら、契約の違反であり、呼び出し側は `UNKNOWN` として扱う。
- ModelPort は、**認可、費用の予約、再試行、ほかのモデルへの切り替えをしない。** それは Runtime の役目で、新しい呼び出しは新しい Attempt になる（E の7.4）。
- エラーのメッセージに、リクエストや応答の本文を入れない。

## 4. Fake Provider（`adapters/fake_model.py`）

- **台本（`FakeScript`）：**
  - リクエストの指紋（`request_fingerprint`）ごとに、結果の列（`FakeReply` か `FakeFault`）を持つ。
  - `complete` のたびに、その指紋の次の結果を1つ使う。
- **指紋：** `profile`、`messages`、`tools`、`params` を正規化した JSON の SHA-256（`fakefp-v1:sha256:`）。ID と3つのハッシュは含めない。そのため、同じ入力なら、どの Attempt でも同じ結果になる。
- **登録していない入力**や、使い切った列は、`FakeScriptMissError` にする。**推測で応答を作らない。**
- **受け付ける条件：**
  - `FAKE` のプロファイルだけ（ほかは `ModelUnsupportedError`）。
  - リクエストが、自分のプロファイルを指していること。
  - `max_output_tokens` が、プロファイルの上限以下であること。
- **応答：** 台本の内容から、共通の型の `ModelResponse` を作る。
  - プロバイダーから受け取ったデータと同じように、厳密な検証を通す。
  - 応答が、リクエストの `max_output_tokens` を超えていれば、不正な応答とする。
- **ツールの要求：** データとして返すだけで、実行しない。ツールの登録簿や実行の経路とは、つながっていない。
- **ネットワーク：** 使わない。試験の中のネットワークの遮断の下で動くことを確かめた。
- **安全の扱い：** 「Fake」という名前や `synthetic` の印だけで、安全とはみなさない。安全は Runtime の認可（WP-09）による。この Adapter を、実行の経路に自動でつなぐものはない。

## 5. 決定性

- 同じ台本、同じ設定、同じ入力の列からは、同じ結果（応答の全体と `provider_request_id`）が得られる（試験で確かめた）。
- `provider_request_id` は `fake-<呼び出しの番号>`。時刻や乱数を使わない。

## 6. 障害の注入

| 障害 | 結果 |
|---|---|
| `FAILED_BEFORE_SEND` | `ModelDefiniteFailureError`（`FAILED_DEFINITE`） |
| `PROVIDER_REJECTED` | 合成のプロバイダーの例外を、`ModelDefiniteFailureError` に変える |
| `STREAM_INTERRUPTED` | `ModelOutcomeUnknownError`（`UNKNOWN`）。使用量は一部だけ（入力だけが分かる） |
| `OUTCOME_UNKNOWN` | `ModelOutcomeUnknownError`（`UNKNOWN`）。使用量は不明 |
| `INVALID_RESPONSE` | 不正なデータを、本物の検証に通す。`ModelResponseInvalidError`（`UNKNOWN`） |
| タイムアウト | 台本の合成の経過時間（`elapsed_ms`）が、リクエストの `timeout_ms` を超えたら、`ModelTimeoutError`（`UNKNOWN`）。**実際には待たない** |

- **Provider の中で再試行しない。** 失敗の後も、台本の次の結果は残る。次の呼び出しは、別の呼び出し（将来の新しい Attempt）である（試験で確かめた）。
- **結果不明と、確定した失敗を区別する。** 結果不明に、確定した 0 の使用量を付けない。

## 7. Usage

| 項目 | 内容 |
|---|---|
| `status` | `FINAL`（完全で最終）、`PARTIAL`（一部だけ分かる）、`UNKNOWN`（何も分からない） |
| `input_tokens`、`output_tokens`、`total_tokens` | 0 以上の整数。`FINAL` ではすべて必要で、合計は入力と出力の和。`UNKNOWN` ではどれも持たない（0 ではない）。`PARTIAL` では合計を持たない |
| `cost` | XTU の最小の単位（整数）。`UNKNOWN` では持たない |
| `synthetic` | Fake の値は真。**合成の試験の値であり、本物のトークンの計測や、実際の費用ではない** |

- Fake の費用：`切り上げ（(入力 × 入力の価格 ＋ 出力 × 出力の価格) ÷ 1,000,000）` を、整数だけで計算する（価格は 100万トークンあたりの micro-XTU。D の10章の `test-virtual-v1` と同じ形）。浮動小数点は使わない。
- 本格的な Cost Engine（予約、照合、台帳）は WP-09 で実装する。

## 8. 設計資料との対応

| 設計 | 実装 |
|---|---|
| E の7.4（共通の型、初期は Fake と Replay、再試行とフォールバックは新しい Attempt） | 2〜4章。Replay は WP-06 |
| E の8章（ModelProfile、ModelRequest、ModelResponse の項目） | 2章 |
| B1 の2.4（ModelProfile の項目） | `ModelProfile`（Phase 1 に要る項目だけ。`tool_def_overhead`、`per_message_overhead`、`estimate_error_rate` は WP-14 で加える） |
| B1 の6.2（3つのハッシュ） | 2章 |
| B1.C16（Engine にはモデルの呼び出しの権限がない） | ModelPort は認可も予約もしない。モデルの呼び出しは、Runtime の Attempt として扱う（WP-15） |
| D の10章（XTU の試験用の価格表） | 7章 |
| ADR-0003 | ModelPort と Fake。状態は PROPOSED のまま |

## 9. 設計資料にない判断（WP-05 で決めたこと）

| 判断 | 理由 |
|---|---|
| 3つのハッシュの接頭辞（`ctxhash-v1:sha256:` など） | 取り違えを、型で防ぐため |
| 温度を 1000 分の 1 の整数で持つ | 浮動小数点を使わないため |
| `ToolUseBlock.arguments` を正規化した JSON の文字列にした | 不変で、書き方が1通りになるため |
| 失敗の `outcome` の3分類と、使用量の `PARTIAL` | 結果不明と確定した失敗を区別し、不明を 0 にしないため |
| `ProviderKind` に `UNSUPPORTED` を置いた | `FAKE` 以外を拒否することを試験するため。実際のプロバイダーは、承認の後に加える |
| Fake の指紋に、ID とハッシュを含めない | 同じ入力の再現のため |
| `ModelRequestId` の接頭辞 `mrq` | WP-02 の ID の形式に合わせた |

## 10. 検証の結果

- **TDD：** 試験を先に書き、モジュールがない状態で失敗することを確かめてから、実装した。
- 試験は 1076 件（WP04-R01〜R04 で 23 件、WP-05 で 58 件を追加）。8種類の品質の検査が、すべて成功した。
- 実 API、ネットワーク、認証情報は、どれも使っていない。

## 11. 確かめていないこと

- 実際のプロバイダーの仕様（メッセージの形、エラーの種類、使用量の数え方）。Phase 3B まで確かめない。
- 本物のトークンの数え方（Fake の値は合成）。
- 3つのハッシュの計算の対象の正規化（WP-14）。
- GitHub Actions の上での実行（CI は未実行）。
