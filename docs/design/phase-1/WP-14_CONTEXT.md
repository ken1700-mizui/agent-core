# WP-14 最小の Context Engine

> 文書の状態：**DRAFT**（92/100 の条件付き合格の後、限定修正 WP14-R01〜R05 を15章に記録した）　最終更新：2026-11-26
>
> - 起点：`2a6656f`（WP-08 の限定修正 R06〜R09 のコミット）。その前の `e493df0` で、リモートとの一致、作業ツリーが空であること、`main`（`aa089ae`、空の初期コミット）、設計のブランチ（`e6b70c0`）、1685 件の試験と8種類の検査の成功を確かめた。
> - 対象：`PHASE_1_IMPLEMENTATION_PLAN.md` の WP-14。B1 の2〜4章・6章、B1.C1〜C3・C6・C7・C10・C16・C18、C2 の6.2。
> - 統合の規則：B1 の15章・16章の注記（E を優先する）に従う。
> - **WP-15（エージェントのループ）には進んでいない。**

## 1. 範囲

| 実装した | 実装していない（範囲の外） |
|---|---|
| Context IR、Manifest、情報の階層、ProtectedFact、決まった組み立ての順序、合成のトークン推定器による予算、予算の超過での停止、3つのハッシュ、最終の入力の検証、Replay との往復 | 圧縮と AI による要約、Memory、文脈の取り直し、実モデルのトークン計測、自動のモデルの変更、再試行、計画の立て直し、実 API、実データ |

コード：
- 型：`src/agent_core/types/context.py`
- 推定器：`src/agent_core/context/estimator.py`
- 事実の導出と変更：`src/agent_core/context/facts.py`
- 組み立て：`src/agent_core/context/engine.py`
- ポート：`src/agent_core/ports/context.py`
- エラー：`types/errors.py` の `ContextError` 系

## 2. Context IR と Manifest

| 型 | 内容 |
|---|---|
| `ContextInput` | Runtime が渡す候補の項目。**階層の欄を持たない。** 経路（`Channel`）、出どころ（`ProvenanceClass`）、`source_ref`、派生元、本文、優先度、`seq`、ツール呼び出しの組（`pair_id`） |
| `Provenance` | 経路、出どころ、`source_ref`（閉じた文字の集合。区切り記号を含められない）、派生元 |
| `ContextItem` | IR に置いた1項目。**本文は持たず**、本文の SHA-256 と推定トークン数（`TokenEstimate`）、区画、階層、経路、出どころ、ProtectedFact の ID と版、組の ID |
| `ContextIR` | `context_hash` の対象（`ctxhash-v1`）。並べた項目、ツール定義、エンジン・プロファイル・推定器・Policy の版、生成の設定、出力の予約。時刻、組み立ての ID、変換後の本文は含めない |
| `ContextManifest` | 含めた項目（位置、階層、トークン数）、除外した項目と理由（`BUDGET`）、保護した事実、`TokenCheck`、3つのハッシュ、アルゴリズム、`hash_scheme=SYNTHETIC_SHA256`、`key_version=None` |
| `ContextBuildResult` | IR、Manifest、`ModelRequest`、3つのハッシュの元になる3つの blob |
| ID と型付きのエラー | `ContextItemId`、`FactId`、`PairId`、`SourceRef`。`ContextInputError`、`ContextProfileError`、`ProtectedFactError`、`ContextVerificationError`、`ContextBudgetExceededError`（`ContextError` の下。理由は決まったコードだけで、本文を含めない） |

- Pydantic の strict、変更できない型、未知の項目の拒否は、既存の `FrozenModel` のとおり。
- 直列化は `dump_json`（キーの順序の固定、空白なし、UTF-8）。RFC 8785 への準拠は確かめていない（WP-06 と同じ）。

## 3. 情報の信頼階層（B1 3、C1、C17）

- **階層は、経路、出どころ、派生元だけで決める。** そのうち最も権限の低いものを採る。本文の自己申告は見ない。

| 経路 | 階層 | 注記 |
|---|---|---|
| （エンジン自身） | I0 | `SYSTEM_RULES`。呼び出し側からは渡せない |
| （Policy） | I1 | 信頼する `Policy` から、エンジンが文面を作る。`SAFETY_POLICY` の経路は予約で、拒否する |
| `PROJECT_RULES` | I2 | `approved_rules` に、パスと内容の digest が一致する場合だけ。それ以外は D2 |
| `TASK_SPEC`、`USER_MESSAGE` | I3 | I0〜I2 を広げられない（I0 に明記。実際の権限は Safety Engine） |
| `RUNTIME_OBSERVATION` | I4 | 出どころが `RUNTIME_OBSERVED` の場合。モデルの計画（`MODEL_PLAN`）が Journal の経路で来ても D4 |
| `TOOL_OUTPUT` / `EXTERNAL_DOCUMENT` / `MEMORY` / `MODEL_OUTPUT` | D1 / D2 / D3 / D4 | 命令の位置に置かない |

- **格上げ：** 人間の承認による格上げ（`HUMAN_APPROVAL`、`HUMAN_APPROVED`）は、信頼できる承認の記録とその範囲を、今の WP で検証できないため、**拒否する**（`PROMOTION_UNSUPPORTED`）。
- **出どころ `SYSTEM_STATIC`：** 呼び出し側からは渡せない（`CHANNEL_RESERVED`）。
- **派生した項目：** 派生元の最も低い権限を超えない。派生元は、先に（小さい `seq` で）渡された既知の項目に限る（`DERIVED_FROM_UNKNOWN`）。
- **最新の入力：** ちょうど1つで、ユーザーの経路（I3）から来たものに限る（`LATEST_INPUT_INVALID`）。
- **メッセージの役割：** 経路から決める。モデルの出力は ASSISTANT、ユーザーの経路（I3）は USER。それ以外の履歴（ツールの出力など）は、USER のメッセージの中の DATA の区画に入れる。本文に「system:」とあっても、役割は変わらない。

## 4. ProtectedFact（B1 2.2、3.4、C3、C18）

- **導出（Phase 1）：** `facts_from_journal` は、`RuntimeView` を通して、Journal から導いた状態だけを読む。
  - **目的（`GOAL`）と完了条件（`DONE_CRITERIA`）：** Task の `goal_ref` と `done_criteria` の digest に一致する、信頼する合成の文面（`SyntheticTexts`。各文面は、自分の digest と一致しなければ作れない）から作る。一致する文面がなければ拒否する（`FACT_TEXT_UNAVAILABLE`）。
  - **未解決のエラー（`OPEN_ERROR`）：** Run の Attempt のうち、`FAILED_DEFINITE` か `OUTCOME_UNKNOWN` のもの。
  - **必要な承認（`APPROVAL_STATE`）：** `APPROVAL_PENDING` の Attempt。
  - 出どころとして、Task または Attempt と、その digest（証拠）を持つ。
- **必須：** 導出した事実は、すべて `OPEN` で、`required` として、すべての文脈に入る。
  - `required` の事実が欠ける、または `OPEN` でない場合は、組み立てない（`PROTECTED_FACT_MISSING`）。
  - 入らなければ `CONTEXT_BUDGET_EXCEEDED`。省略はしない。
- **通常の会話との分離：** 事実は、PROTECTED の区画に、版と状態とともに置く。項目（`ContextInput`）からは、事実を作ることも、上書きすることもできない。AI の要約は D4 の項目にすぎない。
- **状態の変更（`apply_fact_change`）：**
  - モデルによる変更は、どれも拒否する（`FACT_CHANGE_BY_MODEL`）。
  - Runtime が、証拠の参照を付けて未解決のエラーを解決することだけを認める。結果は新しい版（`version+1`、`previous_version`）で、古い版はそのまま残る。
  - 証拠がなければ拒否する（`FACT_EVIDENCE_REQUIRED`）。
  - 更新、失効、置換、オーナーによる解決は、本格的な仕組みがまだないため、拒否する（`FACT_CHANGE_UNSUPPORTED`）。
  - `RESOLVED` の事実は、型の検証で証拠を必須にした。

## 5. 組み立ての順序（B1 3.3、C2、C7）

```
[固定]   I0 → ツール定義（名前の順）→ I1 → I2
[半固定] I3 → ProtectedFact（分類の順、ID の順）
[変動]   I4 → D4 → D3 → D2/D1 → 履歴（seq の順）→ 最新の入力
```

- 区画の中は、`seq` と ID の順に並べる。入力の順序は結果に影響しない（試験した）。空の区画は、そのまま空にする。
- **変換（合成の Adapter `synthetic-adapter@1`）：**
  - 1つ目の USER のメッセージ：I0、I1、I2、I3、ProtectedFact（命令の層）
  - 2つ目の USER のメッセージ：I4 と D の区画
  - その後：履歴、最新の入力
  - Phase 1 のメッセージの型（WP-05）には system の役割がないため、命令の層を、データより前の、先頭の USER のメッセージに置いた。プロバイダーの system の欄への変換は 3B の Adapter で行う。
- **データの区画：** `<<<DATA section=… tier=… item=… source=… tag=…>>>` と `<<<END tag>>>` で囲む。
  - `tag` は本文の SHA-256 の先頭16桁で、決定的に決まる。
  - エンジン以外の本文は、命令の層も含めて、**すべての `\` と `<` をエスケープする。** そのため、本文から区切り記号は作れない。
  - 試験で確かめたこと：エスケープされていない `<<<` は、エンジンの区切り記号だけになる。偽の終わりの記号と、偽の I0 の区画は無害になる。
- **主張しないこと：** 文脈の構造だけで、プロンプトインジェクションを完全に防げるとは主張しない。実行の権限は、引き続き Safety Engine が強制する。

## 6. トークン予算（B1 4.1、4.4、C6）

```
limit   = context_window − reserved_output   （INPUT_PLUS_OUTPUT のとき reserved_output = max_output_tokens）
input   = Σ 各ブロックの推定 + per_message_overhead × メッセージ数 + Σ (tool_def_overhead + ツール定義の推定)
margin  = max(safety_margin_min, ceil(input × safety_margin_percent / 100))
合格    ⇔ input + margin ≤ limit
```

- すべて整数の演算で、浮動小数点の比較はない。
- **出力の予約を削って入力に回さない。** 予約が profile の `max_output` を超える要求は拒否する（`OUTPUT_RESERVE_EXCEEDS_PROFILE`）。
- **推定器：** `synthetic_utf8_bytes@1.0.0`。UTF-8 のバイト数 ÷ 3 の切り上げ＋1 で、決定的。Manifest に、推定であること（`exact=false`）、推定器、方法、各数値を記録する。
  - 実際のトークナイザとの誤差は分からない。
  - `safety_margin_percent` の 15 は、B1 の暫定の候補で、測った誤差率ではない。
- **対応していないプロファイルは拒否する**（`PROFILE_UNSUPPORTED`）：FAKE でないプロバイダー、`EXACT_LOCAL`・`EXACT_REMOTE`（外部への送信が必要になりうる）、推論トークンを別枠にするもの、別の推定器。
- **処理量の上限：** 項目は 1000 件まで、1項目は 200,000 文字まで、合計は 2,000,000 文字まで。超えたら、ほかの処理の前に拒否する（`CONTEXT_INPUT_TOO_LARGE`）。
- **不正な文字：** NUL と、正しくない Unicode（対になっていないサロゲート）は拒否する（`TEXT_INVALID`）。
- **改行：** LF にそろえる。前後の空白の削除や Unicode の正規化はしない。

## 7. 予算の超過（B1 4.2、4.3、C3、C4）

- **省略しないもの：** I0〜I3（呼び出し側が MAY と付けても MUST として扱う）、`OPEN` の ProtectedFact、最新の入力、呼び出し側が MUST と付けた項目、MUST を含むツール呼び出しの組。
- **任意の項目を外す順序（決定的）：**
  1. 優先度：MAY を先、SHOULD を後。
  2. 区画：D2/D1、D3、D4、履歴、I4、解決済みの事実の順。
  3. 同じ区画の中：履歴は古いものから、ほかは後ろのものから。
  - 外した項目は、Manifest の `excluded` に記録する。ツール呼び出しの組は、一緒に外す。
- **止める場合：** 必須の項目が入らなければ、`ContextBudgetExceededError`（`CONTEXT_BUDGET_EXCEEDED`）で止める。持つのは、必要な量と上限の数値だけ。
- AI による圧縮、要約、モデルの変更、再試行、計画の立て直しは行わない。
- **変換した後の最終の検証（B1 4.4）：**
  - IR の段階の推定とは別に、変換した `ModelRequest` で数え直す。
  - 超えていれば、任意の項目をさらに外して、組み立て直す（3回まで）。それでも入らなければ止める。
  - 返す前に、`verify_build` で結果の全体を検証する。**検証していない ModelRequest は返さない。**
  - IR の段階の推定を 0 にする注入の試験で、最終の検証が超過を止めることを確かめた。

## 8. 3つのハッシュ（B1 6.2、C10）

| ハッシュ | 計算の対象（実際のデータ） | 形式 |
|---|---|---|
| `context_hash` | `dump_json(ContextIR)` の bytes | `ctxhash-v1:sha256:…`。IR の版 1 |
| `rendering_hash` | `dump_json(ReplayRenderedInput v1{messages, tools, params})` の bytes | `renderhash-v1:sha256:…`。WP-06 の形式をそのまま使う |
| `request_hash` | `dump_json(ReplayRequestRecord v1{destination, profile, params, tools, rendering_hash})` の bytes | `reqhash-v1:sha256:…`。WP-06 の形式をそのまま使う |

- **`verify_build` で確かめること：**
  - それぞれのハッシュを、それぞれの blob から計算し直す。
  - IR、変換後の入力、リクエストの記録、`ModelRequest`、Manifest が互いに一致すること。
  - トークンの検査を、リクエストから数え直して確かめる。
- **改変の検出（試験）：** メッセージの本文、ツール定義、プロファイル、生成の設定、各 blob の書き換え、別の文脈のデータへのハッシュの文字列の写しを、それぞれ検出する。メッセージやツール定義が変われば、3つのハッシュがすべて変わる。
- **鍵の区別：** 今のハッシュは、合成データ専用の、鍵のない SHA-256 である（`SYNTHETIC_SHA256`、`key_version=None`）。実データでは、C2 の6.2 の、用途ごとに分けた鍵による HMAC（Context、rendering、request の別々の鍵）が必要になる。本格的な鍵の管理は、実装していない。
- **古い記録：** WP-06 の rendered input と request record の形式（どちらも版 1）は変えていない。WP-06 で中身が決まっていなかった IR の blob に、版 1 の `ContextIR` を定めた。Replay は IR の blob を解釈せず、ハッシュで照合するだけなので、既存の記録の意味は変わらない。

## 9. Replay との互換性

- 組み立てた結果の3つの blob で、WP-06 の Replay の記録（`ReplayStep`、`ReplayRecord`、信頼する Manifest）を作った。WP-06 の `ReplayModelProvider` に、組み立てた `ModelRequest` を渡すと、記録した応答が返ることを確かめた（往復）。
- 別の入力で組み立てた文脈は、Replay で拒否される。

## 10. WP-09 との接続の契約

- `ContextBuildResult` の `request_record_blob` と `rendered_blob` は、WP-09 の `ModelContent`（`request_record`、`rendered_input`）にそのまま使える。
- WP-09 の `model_action` は、上限を自分で導く。呼び出し側が見積もりを過小に申告する入力の欄はない。
  - 入力の上限：`max_input_tokens` は、変換後の入力のバイト数。
  - 出力の上限：`max_output_tokens` は、リクエストの生成の設定。
- **試験で確かめた関係：** WP-09 の入力の上限（バイト数）は、Context のトークン推定以上である。WP-09 の出力の上限は、Context が予約した出力と同じ。`rebuild` で、同じ Descriptor が作り直せる。
- したがって、費用の予約（WP-09）は、Context の推定より控えめ（多め）になる。Context の推定は、窓に収まるかの判定だけに使う。

## 11. アーキテクチャ

- `agent_core.context` は、純粋な層（`safety`、`cost` と同じ段で、互いに独立）に置いた。import-linter で次を検査する。
  - `types` と `ports` だけに依存し、SQLite、adapters（Journal、モデル）、runtime、tool の登録簿、Safety Engine を import しない。
  - 植え込んだ違反の試験で、6通りを検出した。
- 最小のポート：`RuntimeView`（Task、Run、Step、Attempt の読み取りだけ）。`RuntimeState` がそのまま満たす。
- Memory の検索と書き込み、モデルの呼び出し、ツールの操作、Safety の権限の変更は、どれも行わない。組み立てで、ファイルの操作が 0 件であることを試験した。

## 12. 試験

- **TDD：**
  - 試験と補助（`tests/unit/test_wp14_context.py`、`tests/support/context_world.py`、import の契約の試験）を先に書いた。
  - 型（`types/context.py`）とエラーだけを作り、エンジン、事実、推定器を `NotImplementedError` の仮の実装にして実行し、81 件が失敗した（ふるまいの失敗）。
  - 2件は、型だけで成功した（目録の文面が digest と一致しないと作れないこと、解決済みの事実に証拠が必要なこと）。
  - 実装の後、試験の補助の不具合を2つ直した。1つは、毎回新しい Task を作っていたため、同じ入力にならなかったこと。もう1つは、試験の窓が profile の `max_output` より小さかったこと。
- **結果：** WP-14 で 89 件を追加した（文脈の試験 83 件、import の契約 6 件）。全体は 1789 件が成功し、8種類の検査も成功した。

| 区分 | 主な試験 |
|---|---|
| 正常と決定性 | 決まった順序、同じ入力から同じ blob、入力の順序に依存しないこと、版と設定がハッシュに入ること |
| 階層 | 経路ごとの階層（10通り）、自己申告の拒否、Journal にあるモデルの計画の格上げの拒否、承認されていない規則、派生元、予約の経路と格上げの拒否、最新の入力、役割 |
| ProtectedFact | Journal からの導出、文面の不一致、欠落、失敗と承認待ちの Attempt、モデルによる変更の拒否、Runtime の証拠による解決と古い版、ほかの変更の拒否、要約による置き換えの拒否 |
| 区切り | 偽の終わりと偽の I0、命令の層からの偽の区画、CRLF |
| 予算 | 出力の予約、INPUT_ONLY、profile を超える出力、境界（ちょうど収まる、1 足りない）、任意の項目の決定的な除外、組の除外、必須の項目、変換後の再検証、対応していないプロファイル、推定器 |
| ハッシュと Replay | 各ハッシュの再計算、8通りの改変と写しの検出、メッセージとツールの変更、WP-06 の Provider との往復、異なる文脈の拒否 |
| 入力と漏えい | NUL、サロゲート、巨大な入力（3通り）。エラーに本文が出ないこと |
| WP-09 | 費用の上限が変換後のバイトから導かれること |
| 純粋さ | ファイルの操作 0 件、import の契約 |

## 13. 保証できない事項

- **OS をまたいだ決定性（C7）：** この Linux の環境でだけ確かめた。**Windows での決定性は検証していない。**
- **トークン数：** 合成の推定にすぎない。実際のモデルのトークン数、15% の余裕の妥当性、絶対に窓を超えないことは主張しない。
- **プロンプトインジェクション：** 文脈の構造とエスケープは、多層防御の1層である。モデルがデータに従わないことは保証しない。
- **system の役割：** Phase 1 の合成の Adapter には system の役割がない。命令の層は、先頭の USER のメッセージに置く。
- **ハッシュ：** 鍵のない SHA-256 で、合成データ専用である。推測攻撃への耐性（B1-P02）、鍵の分離（C2 の6.2）は、実データの前に必要。
- **呼び出し側のエラー：** 呼び出し側が `ContextInput` を作るときの Pydantic の検証エラーは、エンジンの外で起きる。エンジンの型付きのエラーは本文を含めないが、Pydantic の既定のエラーの文には、入力の一部が含まれうる。
- **事実の導出：** Phase 1 では、Journal から導ける事実と、信頼する合成の文面に限る。変更禁止事項、安全性の制約、重要な判断、応答待ちのツール呼び出しの導出、Runtime の証拠からの自動の解決、オーナーによる変更の経路は、まだない。
- **ツール呼び出しの組：** 組は一緒に外す。ただし、`ToolUseBlock` と `ToolResultBlock` としての変換は、まだしていない（テキストとして変換する）。
- **WP-15：** エージェントのループの中で文脈を組み立てて使う接続は、WP-15 の範囲で、実装していない。

## 14. 残っている課題（変わらないもの）

AUDIT-02〜04、A-P6、CI、OS の隔離、WP-08 の残っている課題（`WP-08_FILE_OPS.md` の 9.6、9.7、10.6）、WP-09 の残っている課題は、そのまま残っている。

## 15. 限定修正（WP14-R01〜R05。92/100 のレビューの後）

> この章の状態：**DRAFT**（レビュー前）。起点 `47c4643`（リモートと一致、作業ツリーは空、`main` は `aa089ae`、設計のブランチは `e6b70c0`、1789 件と8種類の検査が成功）。1〜14章と食い違う箇所は、この章が正本。

| 指摘 | 対応 |
|---|---|
| R01 信頼された入力経路 | `runtime/context_assembly.py` の `assemble_context_inputs` を、エージェントのループが使う唯一の組み立ての経路にした。<br>- Policy は Journal に導入されたテナントのもの。<br>- 承認済みのプロジェクトルールは、信頼する `RuleCatalog`（パスと本文）から作る。<br>- ツールは、RunGrant と Policy の両方が固定する登録済みのツールだけ。<br>- ProtectedFact は、その時点の Journal の状態から導き直す。<br>- 記録（`Turn`）は種類だけを持ち、経路と出どころは種類から固定で決まる（モデルの応答とツールの呼び出しは D4、ツールの出力は D1、文書は D2、Runtime の観測は I4、ユーザーの入力は I3）。`Turn` には経路の欄がない。<br>- Task、Run、テナント、RunGrant の対応と、Run が RUNNING であることを確かめる。<br>- エンジンの型は認可の証明ではない。組み立てた結果は、R03 の再生成で、信頼する入力と照合する。 |
| R02 ProtectedFact の解決 | 証拠の参照だけでは解決しない。Journal の上の証拠との対応を検証する仕組みがまだないため、**解決の機能を無効にした**（`FACT_CHANGE_UNSUPPORTED`）。事実は OPEN のまま残る。事実は毎回 Journal から導き直すので、版は再構築できる。 |
| R03 最終検証 | `verify_build(result, inputs=, profile=, params=)` にした。<br>- 信頼する入力、プロファイル、生成の設定から決定的に組み立て直し、結果のすべての部分と比べる（`REBUILD_MISMATCH`）。<br>- 構造の検査を加えた：Manifest の項目、位置、順序、ハッシュ、保護の対象が IR と一致すること（`MANIFEST_MISMATCH`）。IR の各項目が、ちょうど1つのテキストのブロックとして、同じ順序でレンダリングされていること（`IR_RENDER_MISMATCH`）。<br>- 3つのハッシュを計算し直しても整合している偽物（IR の階層の書き換え、ブロックの削除）を拒否することを試験した。 |
| R04 トークン予算の再検証 | Manifest の `TokenCheck` は信頼しない。<br>- 信頼するプロファイルと生成の設定から、出力の予約（`window_counts` に従う）、窓、上乗せ、安全余裕、推定器の版を計算し直し、記録と完全に一致することを求める（`TOKEN_CHECK_FAILED`）。<br>- テキストでないブロックは、数えずに飛ばすのではなく拒否する（`UNSUPPORTED_BLOCK`）。<br>- `bind_model_descriptor` で、WP-09 のモデルの Descriptor が、この `request_hash`、プロファイル、変換後のバイト数、要求した出力に結び付くことを確かめる（`MODEL_DESCRIPTOR_MISMATCH`）。 |
| R05 ツール呼び出しの組 | `ContextInput` に `pair_role`（CALL・RESULT）を加えた。<br>- 組には、モデルの呼び出しがちょうど1つ、ツールの結果が多くても1つ、結果は呼び出しの後、どちらも履歴、という条件を課す。孤立した結果、重複、順序の逆転、別の経路からの呼び出しや結果は拒否する（`PAIR_INVALID`）。<br>- 今のツール呼び出し（最後の呼び出し）とその結果、結果を待っている呼び出しは MUST にする。<br>- 予算で外すときは、組ごと外す。<br>- モデルの履歴は D4 の ASSISTANT のままで、命令の階層を変えられない。<br>- プロバイダーの system の欄や tool のメッセージへの変換は、後の Phase に残す。 |

- **TDD：** 回帰の試験（`tests/unit/test_wp14_fixes.py` と、既存の試験の更新）を先に書き、仮の実装で 42 件が失敗することを確かめた。
- **既存の試験の更新：**
  - 解決の試験：解決が拒否され、事実が OPEN のままであることを確かめる試験に変えた（R02 による意図した変更）。
  - 組の試験：役割と、今の組を加えた。
  - `verify_build` の呼び出し：信頼する入力を渡す形にした。
- **結果：** 1824 件が成功した（35 件を追加）。8種類の検査も成功した。
- **保証できない事項の追加：** 信頼する入力から組み立て直して比べるため、検証の費用は組み立てとほぼ同じになる。解決の機能は無効のままで、未解決のエラーは Run の終わりまで文脈に残る。

