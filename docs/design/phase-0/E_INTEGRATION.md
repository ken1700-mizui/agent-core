# 設計E：全体の統合（E1・E2）

> 文書の状態：**DRAFT**（E2 の版。独立審査を待っている）　作成日：2026-11-26
>
> 改訂の記録
> - E1（`76c5427`）は 92/100 点で、統合の準備として条件付き合格となった。
> - E2 で、この文書を書き直した。対象は、実行の経路、Safety、Cost、共通の接続口、12 の論理 Engine。
>
> 元にしたもの
> - 設計A〜D（`76c5427` の時点）
> - オーナーの E2 の指示
> - **独立審査者が、2026-11-25 作成の初回 Phase 0 報告書を取り直して、直接確かめた内容。** 具体的には、第7章の Engine の一覧、Planning・Model Gateway・Improvement・Orchestration の方針、Phase 2 の後の実API の使い方についてのオーナーの決定。
> - Claude Code の側は、初回報告書の全文を**受け取っていない**。この文書が使っているのは、審査者が伝えた範囲だけである。
>
> 作業の分け方（採用された）
>
> | 段階 | 範囲 |
> |---|---|
> | E1 | 課題の棚卸しと、共通のアーキテクチャ |
> | **E2** | **実行の経路、Safety、Cost、共通の接続口（この版）** |
> | E3 | 保存、削除、バックアップ、鍵、復元 |
> | E4 | ロードマップ、Phase、ゲート、過剰な設計の整理、最終的な統合 |
>
> 不変条件の呼び方：資料の接頭辞を付ける（`A.I1`、`B1.C1`、`B2.M1`、`C1.S1`、`C2.X1`、`D.V1`、この文書は `E.Z1`）。B1 の圧縮の Strategy は、`B1.ST1`〜`B1.ST6` と読み替える。

## 1. 12 の論理 Engine と、内部のモジュール

### 1.1 Engine の一覧（初回報告書の第7章。審査者が確かめたもの）

| # | Engine | 責務 | 主な出典 | 内部のモジュール |
|---|---|---|---|---|
| 1 | Runtime | Run・Task・Step・Attempt の状態機械、Journal への唯一の書き込み、実行の経路、完了の検証 | A | **Execution**（Runtime と Tool の実行の経路。PEP による強制を受ける）、**Verification**（実行中の完了の検証。7.3） |
| 2 | Context | 文脈の組み立て、圧縮、`context_hash` | B1 | — |
| 3 | Memory | 記憶の候補、検証、検索、系譜 | B2 | — |
| 4 | Tool | ツールの登録、検索、選択、スキーマの検証、出力のハンドル化（7.2） | C1 の5章 | Tool Adapter（`builtin` のものだけ。実行は Execution で行う） |
| 5 | Planning | 難易度の判定、計画、計画の立て直し（7.1） | 初回報告書の方針 | — |
| 6 | Evaluation | 実験の登録、ベンチマーク、独立した採点、統計、判定 | D、C2 の10章 | 評価の側の Controller・Verifier・Signer・Aggregator |
| 7 | Improvement | 失敗の分類、改善の候補、採用、取り消し（7.5。本格的な実装は Phase 6） | D の9.5 | — |
| 8 | Model Gateway | 共通のメッセージの形、ModelRequest と ModelResponse、Usage、ModelProfile、Provider Adapter、再試行とフォールバックの方針（7.4） | B1 の2.4、初回報告書の方針 | Fake、Replay（初期）。Anthropic、OpenAI、OpenAI 互換、ローカル（将来） |
| 9 | Cost | 見積もり、予約、費用の台帳、照合、承認 | A の4章、D の10〜12章 | — |
| 10 | Safety | PDP、Policy Store、承認、AuthorizedScope、停止の状態、PEP での強制の規則 | C1 | **Data Security**（データの保護と、共通の保存の機能。C2） |
| 11 | Orchestration | サブエージェントの契約（7.6。初期は単一のエージェントだけ。本格的な実装は Phase 7） | 初回報告書の方針 | — |
| 12 | Observability & Recovery | 構造化ログ、追跡、障害の分類、区切りの点、結果不明の照合、残ったものの回収（7.7） | A、C1 の8.7、C2 の9章 | **Recovery**（論理的な責務。実行の状態を更新するのは Runtime） |

- **12 の Engine を、12 の独立したプロセスやサービスとして作る必要はない。**
- E1 の暫定の一覧にあった Verification・Execution・Data Security・Recovery は、削除せず、上の表の内部のモジュールとして位置付けた。

### 1.2 Model Gateway と Egress Gateway の区別

| | Model Gateway（論理 Engine） | Egress Gateway（PEP。C1 の P3） |
|---|---|---|
| 役割 | モデルとのやり取りの意味を扱う。共通の形への変換、ModelProfile、Usage の正規化、再試行の方針 | 外部への通信を強制する。封をしたリクエスト、宛先の検査、認証情報の注入、送信の関門（3章） |
| 認証情報 | 持たない | 持つ（唯一） |
| ネットワーク | 持たない | 持つ（唯一） |
| 置き場所 | P1 の中のライブラリ（変換） | 別のプロセス、別の OS ユーザー |

### 1.3 プロセスの配置（C1 の2.1 との対応）

| プロセス | 中で動く Engine とモジュール |
|---|---|
| P1 Runtime Core | Runtime、Context、Memory、Planning、Tool（選択と登録簿）、Cost、Safety（PDP と Data Security）、Model Gateway（変換）、Observability & Recovery、Orchestration（将来） |
| P2 Broker | Runtime の Execution（Sandbox、ファイルのコピーと取り出し、`builtin` の Adapter、stdio の MCP） |
| P3 Egress Gateway | 外部への通信だけ |
| P4 Sandbox | 信頼しないコード |
| 評価の側（別のホストか VM） | Evaluation（holdout の部分） |

- **名前の衝突への注意：** C1 では P1 を「Orchestrator」と呼んでいた。しかし、Engine の Orchestration（サブエージェント）とは別のものである。今後は P1 を **Runtime Core** と呼ぶ（`E.Z1`）。

---

## 2. 状態の正本と、ID・seq

### 2.1 状態の正本

| 状態 | 正本 | 写しや投影 | 書き込む主体 |
|---|---|---|---|
| Task・Run・Step・Attempt、ProtectedFact、承認、Permit、Receipt の確定した状態、`access_epoch`、`revocation_gen`、拒否リスト | Journal（`journal.db`） | Materialized State（キャッシュ） | Runtime（P1） |
| 予算の `hold` と、費用のエントリー | Journal と同じ DB の台帳 | 集計の表 | Cost → Runtime（P1） |
| 停止の状態 | Journal | 停止の状態のファイル（署名付き） | Safety → Runtime |
| 削除の要求 | Journal（受付）と、削除台帳（独立した写し） | 外部の証人（E3 で見直す） | Safety（Data Security）→ Runtime |
| 記憶の状態 | Journal のコマンド | `memory/<tenant>.db`、区画の索引 | Memory → Runtime（ProjectionRepair） |
| 本文（記憶、成果物、リクエスト、応答） | 暗号化した blob | — | Runtime（SAG を通る） |
| 鍵 | 鍵の DB | 鍵のバックアップ | Safety（Data Security） |
| 認証情報 | Egress Gateway の保管庫 | なし | オーナー |
| ポリシー、マニフェスト、実行物の登録 | Policy Store | Journal にはハッシュだけ | オーナー → Safety |
| PEP の中の、まだ確定していない送信の状態（`DISPATCH_AUTHORIZED` から、結果の通知まで） | PEP のメモリ（失われうる） | P1 への通知 | PEP |
| 監査ログ | 監査ログ | 署名付きのチェックポイントの写し | Observability & Recovery |
| 実験と結果 | 開発の側の Registry と Results Store、評価の側の保存先 | 制限した報告 | Evaluation |
| 進捗 | `docs/STATUS.md` | `AGENTS.md` には参照だけ | — |

### 2.2 ID の生成と、重複の検査

- **形式：** `<種類の接頭辞>_<サイトの ID>_<128 ビット>`。
  - 128 ビットの中身は、上位 48 ビットがミリ秒の時刻、下位 80 ビットが OS の暗号用の乱数。
  - サイトの ID は、開発の側と評価の側を分けるためのもの。
  - 種類の接頭辞の例：`tsk`、`run`、`stp`、`att`、`evt`、`prm`、`rcp`、`rsv`、`mem`、`del`、`exp`。
- **時刻の順に並ぶ ID を使うだけでは、一意であることを保証できない。** そのため、次の方法で確かめる。
  1. Journal の `event_id` などに、UNIQUE の制約をかける。書き込むのは P1 だけなので、制約の違反は必ず見つかる。
  2. 違反したら、乱数の部分を作り直して、書き込み直す（上限 3 回。超えたら `FATAL` にし、乱数の生成器の異常として扱う）。
  3. 時計が戻った場合も、ID の順番は信用しない。順序は seq で決める。
- 削除したものの ID も、再利用しない（tombstone が ID を持ち続ける）。

### 2.3 `event_id`・`run_seq`・`tenant_seq` の役割

| 項目 | 役割 | 性質 |
|---|---|---|
| `event_id` | イベントの**識別**。参照、重複の除去、冪等な適用に使う | 一意。順序の意味は持たない |
| `run_seq` | 1つの Run の中の**順序** | 隙間なく増える（`A.I1`） |
| `tenant_seq` | 1つのテナントの中の、すべての Run をまたいだ**全体の順序** | 隙間なく増える。P1 が、`run_seq` と同じトランザクションで振る |

**投影（Memory Store、索引、削除台帳の写しなど）の更新の決まり**

- **順序：** 投影は、`tenant_seq` の順に当てはめる。どこまで当てはめたかを `last_applied_tenant_seq` として記録する。
- **欠番：** 次のイベントの `tenant_seq` が `last + 1` でなければ、その投影を止める（`UNAVAILABLE`）。そのうえで、ProjectionRepair で直す（安全側に倒す）。
- **重複：** `event_id` で重複を取り除く。各行は `source_seq` を持ち、それより大きい seq のイベントだけを当てはめる（`B2.M26`）。
- **障害の後の再開：** `last_applied_tenant_seq + 1` から、続きを当てはめる。
- 評価の側は、別の Journal と、別のサイトの ID を持つ。開発の側との間で、全体の順序は作らない。

### 2.4 Rebuild、ProjectionRepair、Replay

| モード | すること | しないこと |
|---|---|---|
| Rebuild | Journal を**メモリの中で** fold する | 永続的な保存先への書き込み、モデルの呼び出し、外部への通信 |
| ProjectionRepair | Materialized State と、Memory の投影を、Journal から作り直す（永続化した投影の修復）。設計A の A11 の排他のロックを持って行う | モデルの呼び出し、外部への通信、内容の作り直し |
| Replay | 記録したデータだけを使う。今の記憶は照会しない | ただし、**今の削除の状態と、アクセスの権限による安全の検査は省かない**。blob を読む前に、拒否リストと権限を確かめる。当たれば `REPLAY_UNAVAILABLE` |

- 設計A の2.2 の「Rebuild（Journal から状態を作り直す）」は、上の意味に直した（A の15章）。

---

## 3. 実行の状態遷移（E2 で最も重要な課題。C2-P01、C2-P02、D-P01、D-P02）

### 3.1 Attempt の状態

```
PROPOSED → VALIDATED → CONTENT_FIXED → POLICY_ALLOWED ─┬→（承認が必要）APPROVAL_PENDING → APPROVED ─┐
                                                       └──────────────────────────────────────────────┴→ BUDGET_RESERVED
→ INTENT_RECORDED → PERMIT_ISSUED → PERMIT_CONSUMED（Receipt を取得した）
→ DISPATCH_AUTHORIZED（PEP の送信の関門の中で、実行を始めることを認めた）
→ DISPATCH_STARTED（実際に、外への書き込み、またはプロセスの起動を始めた）
→ COMPLETED | FAILED_DEFINITE | OUTCOME_UNKNOWN
DISPATCH_STARTED より前のどこからでも → ABORTED_BEFORE_DISPATCH
（取り消し、失効、期限切れ、停止を、PEP が送信の前に見つけて、送っていないことが確かな場合）
```

| 状態 | 意味 | 確定させる場所 |
|---|---|---|
| `PERMIT_CONSUMED` | P1 の消費のトランザクションを通った。Receipt が `ISSUED` になった | Journal |
| `DISPATCH_AUTHORIZED` | PEP が送信の関門（3.2）を取り、すべての条件を確かめ終えた | PEP のメモリ |
| `DISPATCH_STARTED` | 外部への通信では、**リクエストの中身の最初のバイトの書き込みの呼び出し**を始めた。Sandbox では、コンテナのプロセスの起動を始めた | PEP のメモリ → P1 へ通知 |
| `COMPLETED` | 応答を最後まで受け取った。またはプロセスが終了した | Journal |
| `FAILED_DEFINITE` | 相手に届いていないことが確かな失敗（中身を書く前の接続の失敗など） | Journal |
| `OUTCOME_UNKNOWN` | 相手に届いたかどうか分からない。中身を書き始めた後に、接続が切れた、時間切れになった、ストリーミングが途中で止まった、PEP が止まった、のどれか | Journal（`A.A04`、`A.A10` の保守的な復旧に従う） |

- **ネットワークへの書き込みと、Journal を、完全に原子的にすることはできない。** `DISPATCH_STARTED` を P1 に通知する前に、PEP が止まることがある。その場合、P1 は `PERMIT_CONSUMED` のまま、その先が分からない。この状態は **`OUTCOME_UNKNOWN`** として扱う（D-P01）。
- 接続と TLS の握手は、送信の関門の外で、あらかじめ済ませておく。握手では、リクエストの中身は送られない。

### 3.2 PEP の送信の関門（失効と、実際の送信開始の順序を強制する）

各 PEP（Broker、Egress Gateway）は、1つの読み書きのロックを持つ。これを「送信の関門」と呼ぶ。

**送信の側**（読みのロックを持つ。持つ時間は、確認と、書き込みの呼び出しを始めるまでの、短い間だけ）

1. Receipt を、比較して交換する操作（CAS）で `ISSUED` から `CLAIMED` にする。成功するのは1回だけ。
2. 次をすべて確かめる。
   - Receipt の `pep_instance_id` が、自分のものである。
   - Receipt の `revocation_gen` が、PEP の今の値と同じである。
   - 承認と Receipt の期限：「単調に増える時計での、今の時刻 ＋ 余裕」が、期限より前である。
   - 停止の状態が `RUNNING` である。
   - P1 から受け取った「送信の Lease」が、有効である。
3. `DISPATCH_AUTHORIZED` にする。そのまま書き込みの呼び出し（またはプロセスの起動）を始め、`DISPATCH_STARTED` を記録してから、ロックを放す。
   - 書き込みの呼び出しは、ブロックしないものを使い、時間の上限を設ける。
4. どれか1つでも満たさなければ、`ABORTED_BEFORE_DISPATCH` にする。

**失効の側**（書きのロックを持つ）

1. P1 から失効の通知（新しい `revocation_gen`）を受け取る。
2. 書きのロックを取る。
3. `revocation_gen` を更新し、`ISSUED` と `CLAIMED` の Receipt を、すべて `REVOKED` にする。
4. ロックを放す。
5. P1 に ack を返す。

**この仕組みで守れること**

- 書きのロックを取るときには、まだ送信を始めていない操作は、関門の外で待つしかない。
- 書きのロックを放した後に、関門に入った操作は、新しい `revocation_gen` に当たって止まる。
- したがって、**ack を返した後に、まだ始まっていなかった古い操作が、送信を始めることはない。**
- ack より前に `DISPATCH_STARTED` になった操作は、「失効より前の送信」として、照合の対象にする。

**CAS が保証するのは、Receipt を1回だけ使えることだけである。** 予算、失効、承認の期限、停止の状態との整合は、次の3つを組み合わせて保つ。

| 条件 | 保つ仕組み |
|---|---|
| 予算 | P1 の消費のトランザクション（3.3） |
| 失効と停止 | `revocation_gen` と、送信の関門 |
| 期限 | 関門の中での、時刻の確認 |

### 3.3 P1 の側：消費のトランザクションと、失効の確定

**消費のトランザクション**（C1 の7.5 を引き継ぐ）

1つのトランザクションで、次をすべて確かめてから記録する。

- Permit の MAC、期限、`access_epoch`、`consumer`
- 承認の使用の回数
- 予約（`RESERVED` で、同じ Attempt のもの）
- Intent
- `owner_epoch`
- 停止の状態
- 拒否リスト
- ハッシュの一致
- SpendApproval（実API の場合）

→ Receipt を `ISSUED` にする。中身は `{receipt_id, permit_id, attempt_id, pep_instance_id, revocation_gen, expires_at}`。

**失効の確定**

- **失効の原因を、1つの仕組みに集める：** 次のどれが起きても、P1 は `revocation_gen` を1つ増やす。
  - 権限の失効
  - 削除の要求
  - 承認の取り消し
  - 停止
  - SpendApproval の取り消し
  - 予算の上限の引き下げ
- 失効の状態は、2段階で記録する。
  - `REVOCATION_RECORDED`：失効を記録した。
  - `REVOCATION_ENFORCED`：すべての PEP について、次のどちらかを満たした。
    - (a) ack が届いた。
    - (b) その PEP の送信の Lease が切れ、さらに猶予の時間が過ぎた（3.4）。
- `ENFORCED` になるまで、P1 は、対象の範囲に新しい Permit も Receipt も出さない。

### 3.4 さまざまな場合の扱い

| 場合 | 扱い |
|---|---|
| 同じ Receipt の二重の消費 | PEP の中の CAS で、2回目は失敗する。別の PEP のインスタンスでは、`pep_instance_id` が合わないので、拒否される |
| PEP の再起動 | 新しい `pep_instance_id` になり、前の Receipt はすべて無効になる。前の PEP の中で `DISPATCH_STARTED` の通知が届いていなかったものは、`OUTCOME_UNKNOWN` にする |
| 古い Worker の復活 | Worker-Exec はネットワークを持たない。送信できるのは PEP だけ。PEP は OS の排他ロックで1つだけ動かす（`A.A11` と同じ）。古い PEP は、送信の Lease が切れると送信できない |
| IPC の切断 | P1 は、送信の Lease（暫定で TTL 3 秒、1 秒ごとに更新）を PEP に与える。PEP は、Lease が切れたら関門を閉じ、新しい送信を認めない。P1 の側では、(b) の条件で失効が確定する |
| 承認の期限切れ | 関門の中で確かめる。消費の後でも、期限が切れていれば、送信しない |
| 緊急停止 | 停止の状態を記録し、`revocation_gen` を増やす（3.3）。PEP は、停止の状態のファイルも、関門の中で確かめる（C1 の11.4） |
| 権限の失効 | `revocation_gen` の仕組み（3.3） |
| 予算の予約 | 予約がなければ、消費に失敗する（3.3）。予約は、`attempt_id`、`permit_id`、`receipt_id` に結び付く（4章） |
| 消費の直後の障害（D-P01） | `PERMIT_CONSUMED` で止まっていて、PEP が「送っていない」と確かに報告していない場合は、`OUTCOME_UNKNOWN` にする。**予約は解放しない** |
| ストリーミングの途中の切断 | `OUTCOME_UNKNOWN`。途中までの使用量は、下限として記録する。費用の `hold` は上限のまま（4章） |
| 結果が分からない場合の復旧 | 設計A の A04・A08・A10 の保守的な方針を保つ。自動で再実行するのは、`READ_ONLY` の操作だけ（新しい Attempt、新しい予約、新しい Permit を使う） |

---

## 4. Cost の統一（D-P01〜P03、CP-14・CP-15・CP-17）

### 4.1 実行の状態と、費用の台帳の対応

| Attempt の状態 | 費用の台帳の状態 | `hold` |
|---|---|---|
| `BUDGET_RESERVED` | `RESERVED` | 予約の額 |
| `PERMIT_CONSUMED` | `CONSUMED`（**新しく加える**。D-P01） | 予約の額（解放しない） |
| `DISPATCH_STARTED` | `ACTIVE`（CP-17） | 予約の額 |
| `ABORTED_BEFORE_DISPATCH`（生きている PEP が、送っていないことを報告した） | `RELEASED` | 0 |
| `FAILED_DEFINITE` | `RELEASED`。ただし、BillingScheme が「この種類の失敗も課金されうる」としている場合は `UNKNOWN` | 0 か上限 |
| `OUTCOME_UNKNOWN` | `UNKNOWN` | 上限の見積もり |
| `COMPLETED` で、最終の使用量がある | `USAGE_REPORTED` | 4.2 のとおり |
| 照合した | `RECONCILED` | 照合した実額（予約を超えていても、実額を記録する） |

**訂正と、説明できない差額**

- `CORRECTION`（訂正）と `UNEXPLAINED`（説明できない差額）は、通常の状態遷移とは別の、**調整のエントリー**として追記する（D-P02）。元のエントリーを指す。

**実額が見積もりを超えた場合**

- `OVERRUN` のエントリーを記録する。
- その範囲（Run、Task、実験、月）の新しい予約を止め、オーナーに知らせる。
- 実額は、上限にかかわらず、正しく記録する。

### 4.2 予約の残りを解放する条件（D-P02）

`USAGE_REPORTED` で予約の残りを解放してよいのは、次の**すべて**を満たす場合だけ。

1. BillingScheme で、その使用量の報告が**最終のもの**とされている（ストリーミングの終わりの最終の使用量など）。
2. 追加で課金されうる項目（ツールの料金、最小の料金など）がない、または、すべて分かっている。
3. 価格表が `verified` である（試験の段階では `test`）。

- 満たせば：`hold` = 計算した額 ×（1 ＋ 価格の不確かさの上乗せ）とし、残りを解放する。
- 満たさなければ：`hold` は予約の額のまま、照合を待つ。

### 4.3 整数の計算

- 金額は、最小の単位の 64 ビットの整数で持つ。
- **トークンの数 × 単価の掛け算は、128 ビットの中間の値で計算する**（またはオーバーフローを検査する演算を使う）。その後、決めた規則で丸めて、64 ビットに戻す。
- 合計も、オーバーフローを検査しながら足す。
- オーバーフローが起きたら `FATAL`。予約は失敗させる（安全側）。

### 4.4 予算の階層（D の11.1 に Task を加える。長時間の作業。9章）

```
1回の呼び出し ⊆ Attempt ⊆ Step ⊆ Run ⊆ Task ⊆ ユーザー ⊆ テナント ⊆ 月間
実験 ⊆ テナント ∩ 月間
```

### 4.5 実API の承認：Run ごとの承認と、月額の上限

**審査者が確かめた、初回報告書のオーナーの決定**

- Phase 2 の後に実API を使う場合は、「見積額を添えた、実行ごとの承認」と「月額の上限」が必要。

**これを満たすための SpendApproval の形**

`{approval_id, experiment_id?, approver, valid_from, expires_at, monthly_cap, model_ids, environment, run_plans: [{run_plan_id, task_id, config_id, estimate, max_cost}]}`

- **承認するのは、見積額を添えた個々の Run の計画（`run_plans`）である。**
- Cost Engine は、予約するときに、その Run が `run_plans` に載っていることを確かめる。載っていない Run（範囲の外の Run、作り直した Run、再試行で増えた Run）は、**新しい承認がなければ予約できない**。実験の全体を承認しただけでは、範囲の外の Run を自動では承認しない。
- この決定の文言は、`OWNER_DECISIONS.md` には載っていない。オーナーの承認を得て、D5 として加えることを提案する（E4。**無断では変えない**）。

### 4.6 環境の間での、予算の移し替え（D-P03）

```
① 古い環境の封筒を CLOSING にする → 新しい予約を止める → 失効を ENFORCED にする
② 古い環境の CONSUMED と ACTIVE は、照合が済むまで UNKNOWN として押さえ続ける
③ 古い環境の明細を作る：確定した費用 ＋ USAGE_REPORTED の hold ＋ UNKNOWN の hold ＋（残っていれば）予約
   明細には署名を付ける
④ オーナーが、新しい環境の封筒を承認する
   新しい環境に割り当てるのは「月の承認額 − 明細の合計」。同じ月の予算の全額を、割り当て直すことはしない
⑤ 古い環境を止めたことを確かめてから、新しい環境の封筒を OPEN にする
⑥ 後で照合して、押さえていた額が減った場合は、オーナーが割り当てを増やせる（自動では増やさない）
```

---

## 5. 操作の種類ごとの経路

| 操作 | PDP の判定 | 承認 | 予算（金額） | 予算（回数） | Intent | Permit と消費 | 送信の関門 | SAG | 記録 |
|---|---|---|---|---|---|---|---|---|---|
| 純粋な判断の処理 | 不要 | 不要 | 不要 | 不要 | 不要 | 不要 | 不要 | 不要 | 判断の結果を Step のイベントに含める |
| ファイルの読み取り（ワークスペースの写し） | 要（`files.read`、範囲） | 不要 | 不要 | 要 | 要（軽いもの） | 要（Broker が消費） | 不要（外部に出ない） | 結果の保存の前に要 | 要 |
| ファイルの書き込み（ホストへの反映） | 要 | 方針による | 不要 | 要 | 要 | 要（Broker） | 反映の手順（6章） | 要（反映するファイルごと） | 要 |
| Sandbox の中のコマンドの実行 | 要 | 方針による | Sandbox の費用（XTU） | 要 | 要 | 要（Broker） | 要（プロセスの起動が `DISPATCH_STARTED`） | 出力の保存の前に要 | 要 |
| Model Call（Fake・Replay） | 要 | 不要 | XTU の台帳 | 要 | 要 | 要 | 不要（外部に出ない） | 要 | 要 |
| Model Call（実API。Phase 3B 以降） | 要（EgressCheck を含む） | 方針による | 要（Run ごとの SpendApproval） | 要 | 要 | 要（Egress Gateway が消費） | 要 | 要（リクエストの記録を保存する前） | 要 |
| そのほかの外部通信 | 要（EgressCheck） | 方針による | 要 | 要 | 要 | 要（Egress Gateway） | 要 | 要 | 要 |
| 記憶の検索 | 要（AuthorizedScope、拒否リスト、epoch） | 不要 | 不要 | 要 | 要（照会の記録） | **不要**（P1 の中の処理で、プロセスの境界を越えない） | 不要 | 結果の保存の前に要 | 要 |
| 記憶の保存 | 要 | 重要な記憶だけ（B2 の3.3） | 不要（検証のモデル呼び出しは、別の Model Call） | 要 | Journal のコマンド | 不要 | 不要 | 要 | 要 |
| Rebuild | 不要 | 不要 | 不要 | 不要 | 不要 | 不要 | 不要 | 不要（書き込まない） | 不要 |
| Replay | **要**（今の削除の状態と権限） | 不要 | 不要 | 不要 | 不要 | 不要 | 不要（外部に出ない） | 不要 | Replay の実行の記録だけ |
| ProjectionRepair | 要（保守の権限。排他のロック） | オーナーか Runtime の保守の手順 | 不要 | 不要 | 修復の開始と終了 | 不要 | 不要 | 要（書き戻す内容は、もともと SAG を通ったものだけ） | 要 |

**Context を組み立てるときのモデル呼び出し**

- 要約や関連度の判定のためのモデル呼び出しは、それぞれ**独立した Model Call の Attempt** として、上の経路を通す（`B1.C16`）。
- **隠れたモデル呼び出しは禁止する：** Context、Memory、Planning、Tool、Verification には、モデルを呼ぶ手段を持たせない。
- **Context の組み立てを再帰させない：** 補助のための Model Call の文脈は、決まった最小の型で組み立てる。その中では、圧縮も補助の呼び出しもしない（深さは 0）。
- **際限なく再試行させない：** Step ごとの補助の呼び出しの上限と、D の11.2 の上限を守る。

---

## 6. 成果物を複数のファイルに反映するときの原子性（C1-P03）

**ApplyPlan（反映の計画）**

`{apply_id, run_id, attempt_id, base_tree_hash（写しを作ったときの状態）, changes[{path, op: add|modify|delete, pre_hash, post_hash, backup_ref}], order}`

**手順**

```
① 計画：コンテナを止めた後、差分を計算し、C1 の8.5 の検査を行う。ApplyPlan を Journal に記録する
② 同時の変更の検出：反映を始める直前に、対象のすべてのパスについて、
   今のハッシュ = pre_hash（写しを作ったときの状態）であることを確かめる
   違っていれば CONFLICT → 反映しない → RECONCILIATION_REQUIRED（上書きしない）
③ 退避：変更と削除の対象の、元の内容を、暗号化した blob に退避する（backup_ref）
④ 反映：決めた順番で行う（追加と変更が先、削除は最後）。ファイルごとに、一時的な置き場から原子的に置き換える
   ファイルごとに ApplyStep を Journal に記録する
⑤ 確認：すべての post_hash を確かめる → ApplyPlan を COMMITTED にする
```

**途中で失敗した場合と、障害の場合**

| 場合 | 扱い |
|---|---|
| 途中で失敗した | 退避した内容で元に戻し、`ROLLED_BACK` にする |
| 障害の後（ApplyStep の途中で止まっていた） | 対象のファイルごとに、今のハッシュを確かめる。<br>- `pre_hash` か `post_hash` のどちらかに一致するなら、元に戻すことができる。<br>- どちらでもない（外から変更された）なら、`CONFLICT` として、人間に回す |

**完了の扱い**

- 途中までの反映を、正常な完了とは扱わない。
- Run の `VERIFYING`（`A` の6章）は、ApplyPlan が `COMMITTED` であることを条件にする。

**選んでよい別の方式**

- ワークスペースが git の場合は、別の作業ツリーに反映してから、参照を切り替える方式を選んでもよい。

---

## 7. まだ設計のない Engine の、最小限の契約

### 7.1 Planning

**難易度の判定（初回報告書の方針）**

| 判定 | 扱い |
|---|---|
| `simple` | 計画を作らずに、直接実行する |
| `complex` | 完了条件付きの、構造化された計画を作る |

- 判定は、決まった規則で行う（変更するファイルの見込みの数、タスクの種類、あいまいさの印）。規則で判定できないときだけ、Model Call で判定する。

**Plan の型**

`Plan{plan_id, task_id, version, goal_ref（ProtectedFact）, steps[{step_key, description, depends_on[], done_criteria, budget_share}], done_criteria, created_by: model_plan}`

- Plan は D4（権威を持たない。`B1.C17`）。

**計画の立て直し**

- 立て直すのは、次の場合だけ。
  - Step が失敗した。
  - 完了条件を満たせないと分かった。
  - `CONTEXT_BUDGET_EXCEEDED` になった。
- 回数の上限は、D の11.2（暫定で 10 回／Run）。

**予算と、不要な計画の抑制**

- 計画を作ることも、立て直すことも、Model Call として、回数と費用の予算を使う。
- 不要な計画を作らせないための決まり：
  - `simple` では、計画を作らない。
  - 計画の手順の数に上限を設ける。
  - 評価では、不要な計画の立て直しの回数を測る（D の9.6）。
- **計画を作ること自体を、目的にしない。**

### 7.2 Tool

```
登録（オーナー。マニフェストと実行物の固定。C1 の5章）
→ 検索（RunGrant の tools の中だけ）
→ 候補の選択（規則による。必要なら Model Call）
→ スキーマの検証 → 権限の確認（PDP）
→ 実行（Runtime の Execution と PEP）
→ 出力の検証（スキーマと大きさ）
→ ハンドル化（大きな出力は、成果物として保存し、参照だけを返す。B1 の5.3）
```

- **Tool Engine の選択の結果は、提案にすぎず、実行の権限を持たない。** 実行の権限は、PDP と Permit だけが与える。

### 7.3 Verification（Runtime の、完了の検証のモジュール）

- **モデルが「完了した」と言っただけでは、タスクを完了にしない。**
- 完了する前に `VERIFYING` を通る（`A` の6章）。そこで、次を確かめる。
  - 完了条件
  - テストの実行
  - 成果物の検査
  - ApplyPlan が `COMMITTED` であること
- 満たさなければ、結果をフィードバックとして返し、修正のループに戻す。修正の回数には上限を設ける（暫定で 3 回）。上限を超えたら `FAILED` にする。
- **実行中の Verification と、Evaluation による独立した採点は、別のものとして扱う。** Verification の結果を、評価の採点には使わない（`D.V4`）。

### 7.4 Model Gateway

**共通の型**

`Message{role, content: ContentBlock[]}`、`ContentBlock{type: text|image|tool_use|tool_result|…, …}`、`ModelRequest`、`ModelResponse`、`Usage`、`ModelProfile`（8章）。

**Provider Adapter**

| 段階 | Adapter |
|---|---|
| 初期 | Fake、Replay だけ |
| 将来 | Anthropic、OpenAI、OpenAI 互換、ローカル |

- Adapter は、共通の型を、各プロバイダーの形式に変換するだけ。各プロバイダーの仕様は確かめていない。

**再試行とフォールバック**

- 実モデルでの実行、再試行、フォールバックは、それぞれが新しい Attempt である。個別の認可と、費用の予約を通す。
- **初めて実モデルで比較している間は、無断で別のモデルにフォールバックしない。** モデルの ID は、実験の仕様で固定する（`D.V1`）。

### 7.5 Improvement（本格的な実装は Phase 6）

```
失敗の分類（Observability の障害の分類を使う）→ 改善の候補（CandidateChange）
→ dev で試す → validation で比べる（事前登録）→ 独立審査 → オーナーの承認 → 採用（Decision）
→ 監視 → 取り消し（ROLLBACK）
```

- **Improvement からは、評価の基準、採点のプログラム、holdout を変えられない**（書き込みの権限を持たない。`D.V8`）。

### 7.6 Orchestration（初期は単一のエージェントだけ。本格的な実装は Phase 7）

**契約**

`SubTask{subtask_id, parent_run_id, goal, done_criteria, grant（親の RunGrant の範囲の中に限る）, budget（親の残りから切り出す）, context: 独立したもの, result_contract}`

**権限と予算**

- 権限は、親より狭くなるだけ（`C1.S7`）。
- 費用は、親の予算の階層から引く。

**上限**

- 深さの上限は 1、並べて動かせる数の上限は 2（どちらも暫定）。
- 初期の段階では、どちらも 0（サブエージェントを使わない）。

**結果の受け渡し**

- 結果は、データ（D4）として、親に渡す。

### 7.7 Observability & Recovery

| 責務 | 内容 |
|---|---|
| 構造化ログ | ID、状態、時間、ラベル、理由の区分だけを記録する。本文は入れない（`C2.X4`、`C2.X25`） |
| 追跡 | Task → Run → Step → Attempt を、ID でつなぐ |
| 障害の分類 | Agent の側の障害か、環境の側の障害か（D の7.2） |
| 区切りの点 | Run の区切りで、引き継ぎの記録を作る（9章） |
| 結果不明の照合 | `OUTCOME_UNKNOWN` を一覧にして、照合する（`A.A10`） |
| 残ったものの回収 | 障害の後に残ったコンテナや一時データを、回収する（C1 の8.7） |

- 実行の状態を更新するのは Runtime である。Recovery は、照合の結果を、Runtime への要求として返す。
- **監視やログを通じて、機密が漏れないようにする：** 監視のデータも、SAG と、ラベルによる送信の規則に従う。

---

## 8. 共通の契約

| 型 | 主な項目 | 正本 | 責任を持つ Engine |
|---|---|---|---|
| Task | `task_id, goal_ref, done_criteria, budget_total, caps_total, status, runs[]` | Journal | Runtime |
| Run | `run_id, task_id, run_grant_ref, status, owner_epoch, parent_run_id?` | Journal | Runtime |
| Step | `step_id, run_id, run_seq, kind, status` | Journal | Runtime |
| Attempt | `attempt_id, step_id, state（3.1）, action_hash, permit_id?, receipt_id?, reservation_id?` | Journal | Runtime |
| Event | `event_id, type, schema_version, tenant_id, run_id?, run_seq?, tenant_seq, engine@version, policy_version, access_epoch, owner_epoch, revocation_gen, labels, refs, keyed_hashes, prev_hash` | Journal | Runtime |
| EngineRequest | `request_id, kind, engine@version, run_id?, step_id?, attempt_id?, tenant_id, payload_ref, labels, estimated_cost?, policy_version, access_epoch` | Journal | 要求を出した Engine |
| ModelProfile | `provider, model_id, deployment, context_window, max_output, token_accounting, billing_scheme_ref, cache_capability, profile_version` | Policy Store | Model Gateway |
| ModelRequest | `model_request_id, attempt_id, profile@version, messages[], tools[], params, context_hash, rendering_hash, request_hash` | 暗号化した blob と Journal | Model Gateway |
| ModelResponse | `model_request_id, content[], stop_reason, usage, provider_request_id, final_usage: bool` | 暗号化した blob と Journal | Model Gateway |
| ToolManifest | C1 の5.1 と `artifact_hash` | Policy Store | Tool・Safety |
| ContextManifest | B1 の2.5 | Journal | Context |
| MemoryRecord | B2 の2.2 | Memory の投影（Journal のコマンドが正本） | Memory |
| AuthorizedScope | `tenant, user, project, agent, eval_namespace, allowed_types, allowed_labels, policy_version, access_epoch, hash` | Journal（計算した結果） | Safety |
| RunGrant | C1 の4.2、上限（D の11章）、`approval_id?`（CP-15） | Journal | Safety |
| ActionDescriptor | C1 の7.1 | Journal | Runtime |
| Approval | C1 の7.2・7.6（Exact と Scoped） | Journal | Safety |
| Permit | `permit_id, action_hash, access_epoch, consumer, artifact_hash, reservation_id, expires_at, mac`（CP-4・CP-8） | Journal | Runtime・Safety |
| Receipt | `receipt_id, permit_id, attempt_id, pep_instance_id, revocation_gen, expires_at, state` | Journal（`ISSUED` と最終の状態）、PEP のメモリ（途中の状態） | Runtime・PEP |
| BudgetReservation | `reservation_id, attempt_id, levels[], amount, state（4.1）, hold` | 台帳 | Cost |
| SpendApproval | 4.5 | Journal | Cost・Safety |
| ExperimentSpec | D の3.3 | Experiment Registry | Evaluation |
| Decision | D の3.4 | Decision Recorder | Evaluation・Improvement |

---

## 9. 長時間の稼働

| 概念 | 意味 | 期限と上限 |
|---|---|---|
| サービス（Runtime の常時の稼働） | Runtime Core が、ずっと動き続けること | 期限はない。Run ではない |
| Task（長時間の作業） | 何時間も、何日もかかる作業の全体 | Task の全体の予算と上限（4.4）、総経過時間の上限 |
| Run（有限） | Task の中の1区間 | D の11.2 の上限（暫定で 2 時間）を保つ |
| 区切りの点 | Run の終わりに作る、引き継ぎの記録（B1 の S5。権威のある部分は Journal から作る） | — |
| 引き継ぎ | 次の Run は、同じ `task_id` と、区切りの点を引き継いで始まる | — |

**上限を迂回させない**

- **Run を作り直しても、上限は元に戻らない。** 予算と回数の上限は、Task の単位でも数える。新しい Run には、Task の残りだけを渡す。
- 実API では、Run ごとの承認（4.5）が必要になるので、作り直した Run も承認なしでは動かない。

**範囲**

- スマートフォン向けの管理画面と、遠隔操作の本格的な設計は、E4 で行う。

---

## 10. Evaluation の統一（D-P04〜P06）

### 10.1 規則 A・B の誤判定の制御（D-P04）

- **規則を1つだけ登録した場合：**
  - 段階1：維持すべき指標の検定。交わりと和の検定なので、補正は要らない。
  - 段階2：改善の判定。改善の対象の指標が複数あれば、Holm で補正する。
- **規則 A と B の両方を登録した場合：**
  - 「A か B のどちらかで採用する」は、仮説の和集合になる。そのため、2つの規則の段階の検定に、**Holm の補正**をかける。
  - 各規則の中は、上の段階1と段階2のとおり。
- **固定した順番の検定は、使わない：** 「A に失敗したら B に進む」という形は、固定した順番の検定（前が成功したときだけ次に進むもの）の条件を満たさないため。
- **偽陽性の率は、採用してはならない境界の全体で評価する：**
  - 例：主指標が 0（E3 なら `δ_imp`）、維持すべき指標が `−δ_ni` の境界、その組み合わせ。
  - 最悪の場合でも、α ＋ モンテカルロの誤差の範囲に収まることを、D の16.1 のシミュレーションで確かめる。

### 10.2 費用と、品質の達成率（D-P05）

- **基本にするのは、次の2つ：**
  - 固定の予算での、実際に測った成功率
  - 累積の実費
- **Kaplan–Meier の方法を、無条件には使わない：** 途中の失敗や、自分から作業をやめたことは、他と関係なく起きた打ち切りとは限らない。
  - 使うのは、打ち切りが予算の上限だけで起きている場合の、感度の分析に限る。
- **全実費で数えた費用：** 無効な試行を含めた費用も、別に報告する。

### 10.3 機能ごとの採用の判定（D-P06）と、比較の構成の統一

- **全体の評価：** Baseline A・B・C と比べる（D の4.1）。
- **機能の評価：** FeatureComparator を使う。これは、Agent Core の中で、その機能だけを変えたもの（CP-13）。
  - B1 の11章の「全履歴方式」「基本的な圧縮」「最適化」と、B2 の11章の4つの構成は、FeatureComparator である。
- **機能ごとの採用の条件：** 機能ごとの指標（D の9.6）を主指標にする。全体の維持すべき指標は、そのまま当てはめる。
- **0 の扱い：**

| 場合 | 扱い |
|---|---|
| 費用が 0 | 絶対の差で判定する |
| 両方の構成で成功が 0 件 | 成功率では `INCONCLUSIVE`。成功率を根拠にして採用はしない |
| 品質の指標がない | その指標は `NOT_APPLICABLE` と記録する。当てはまる維持すべき指標の数が、事前に決めた最低の数より少なければ、`INCONCLUSIVE` |

- **安全のために必須の機能は、性能を改善する証拠がないことだけを理由に、外さない。** それらは、Safety Gate で扱う。

### 10.4 holdout の使用の回数と、集計の制限を、評価の側で強制する

- Eval Controller は、評価セットの版ごとの使用の回数を、署名付きの台帳で数える。上限を超える実行は拒否する（`D.V7`、CP-16）。
- Aggregator は、公開した集計の量を、版ごとに累積で数え、上限を超える報告を出さない。

---

## 11. 安全のために必須の機能（共通の土台）

次は、どの構成でも、どの Phase でも外さない。

- PDP と PEP、Permit とその原子的な消費、送信の関門と `revocation_gen`
- Sandbox（自己診断に失敗したら、実行を拒否する）、Egress Gateway
- SAG（認証情報を保存しない）、予算の強制（Task を含む階層、Run ごとの承認）、緊急停止
- B1 の権限の階層、ProtectedFact の安全上の制約

## 12. この文書の不変条件（`E.Z`）

| ID | 不変条件 |
|---|---|
| E.Z1 | P1 を Runtime Core と呼ぶ。Engine の Orchestration（サブエージェント）とは別のもの |
| E.Z2 | すべての ID は、2.2 の方式で作り、Journal の UNIQUE の制約で、重複を検査する |
| E.Z3 | `run_seq` と `tenant_seq` は、隙間なく増える。投影は `tenant_seq` の順に当てはめ、欠番があれば止まる |
| E.Z4 | 外部への送信とプロセスの起動は、送信の関門の中で、`DISPATCH_AUTHORIZED` を経てから始まる |
| E.Z5 | ack を返した後、または送信の Lease が切れた後は、まだ始まっていない操作が、古い `revocation_gen` で始まることはない |
| E.Z6 | `PERMIT_CONSUMED` の後、送っていないことが確かでなければ、予約を解放しない |
| E.Z7 | 予約の残りは、4.2 の条件をすべて満たしたときだけ解放する |
| E.Z8 | 実額は、見積もりを超えていても、正しく記録する。訂正と、説明できない差額は、調整のエントリーとして追記する |
| E.Z9 | 金額の掛け算と足し算は、オーバーフローを検査する |
| E.Z10 | 実API の予約には、その Run の計画を含む SpendApproval が必要 |
| E.Z11 | Run を作り直しても、Task の単位の上限は元に戻らない |
| E.Z12 | 隠れたモデル呼び出しと、再帰的な Context の組み立てを禁止する |
| E.Z13 | 途中までの反映を、正常な完了とは扱わない。同時の変更があれば、上書きしない |
| E.Z14 | Replay でも、今の削除の状態と権限の検査を省かない |
| E.Z15 | 選択、計画、Verification の結果は、実行の権限を持たない |
| E.Z16 | 初めて実モデルで比較している間は、無断でフォールバックしない |

## 13. E3・E4 へ残すもの

- **E3：** 次の事項。
  - 削除台帳の耐久性と、削除の記録の複製（C2-P03）
  - 確定していない削除（C2-P04）
  - 派生データの削除
  - バックアップと復元（A-P1）
  - 鍵の更新（C2-P05）
  - CP-11
  - B2-P01・P03
- **E4：** 次の事項。
  - 正式なロードマップと、Phase のゲート
  - 対応する OS の範囲（A-P2）
  - LiveProvider を有効にすることについての、オーナーの承認の案（E-OWN-1）
  - D5（Run ごとの承認と月額の上限）を `OWNER_DECISIONS.md` に加えることの承認の案
  - `AGENTS.md` の整理（CP-6）
  - 個人での利用に向けた、機能の優先順位
  - スマートフォンからの遠隔操作
  - Service Harness（本体が完成した後の、独立したサブ機能。今回は設計しない）
- 商用化は、必須の目標ではない。

## 14. 残存リスク

1. **ネットワークへの書き込みと Journal：** 原子的にはできない。`DISPATCH_STARTED` を P1 に通知する前に障害が起きると、結果は `OUTCOME_UNKNOWN` になり、予約は押さえられたままになる。
2. **送信の関門のロック：** 書き込みの呼び出しが時間の上限まで止まると、失効の処理がその間待たされる。
3. **送信の Lease の時間：** 時計のずれと、Lease の時間の決め方によって、失効が確定するまでの時間が延びる。
4. **新しく設計した部分：** Planning、Tool の選択、Verification、Orchestration、Improvement は、最小限の契約だけである。性能に直接効く部分なので、実装の前に、細かいところを詰める必要がある。
5. **初回報告書との照合：** 初回報告書の全文と照合したのは、審査者が伝えた範囲だけである。
