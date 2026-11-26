# 設計E：全体の統合（E1・E2）

> 文書の状態：**DRAFT**（E4 の版。Phase 0 の最終審査を待っている。E1 は 92/100、E2 は 94/100、E3 は 92/100 で、それぞれ条件付き合格）　作成日：2026-11-26
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
> | E2 | 実行の経路、Safety、Cost、共通の接続口（94/100、条件付き合格） |
> | E3 | 保存、削除、バックアップ、鍵、復元（第2部。92/100、条件付き合格） |
> | **E4** | **ロードマップ、Phase、ゲート、過剰な設計の整理、最終的な統合（第3部。この版）** |
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
  - **E2-P05 による見直しの対象：** DNS、接続、TLS の握手も、外部に情報（宛先など）を伝える。E4 で、「認可と送信先の検証を通る前は、DNS、接続、TLS を含めて、外部通信を始めない」という方向で直す（`RESOLUTION_MATRIX.md` の6章）。

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
| 結果が分からない場合の復旧 | 設計A の A04・A08・A10 の保守的な方針を保つ。自動で再実行してよい条件は、30.3（E4）で改めた。`READ_ONLY` という分類だけでは、再実行しない |

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

> **E3 での注記：** 復旧の詳細と、専用の作業ツリーを優先する方式は、22章で置き換えた（E2-P02）。

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

> **E4 での注記：** 反映との順序は、29.5 に置き換えた（E3-P05）。

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

---

# 第2部：E3（保存、削除、バックアップ、鍵、復元）

> この部は E3 の版（DRAFT）。新しい機能を大量に加えることはせず、設計の間の整合と、復元のときの安全を優先した。
> 実装と試験は行っていない。OS や暗号のライブラリの仕様は、確かめていない。

## 15. 保存領域と、状態の正本（E3-01）

### 15.1 SQLite を使う保存領域

| 保存先 | 保存する内容 | 正本か投影か | 書く主体 | 読める主体 | 暗号化 | 整合性の検証 | バックアップ | 削除 | 保存の期間（候補） | 復元のときの扱い |
|---|---|---|---|---|---|---|---|---|---|---|
| `journal.db` の Journal | イベント（本文なし）、ProtectedFact、承認、Permit と Receipt の確定した状態、epoch、`revocation_gen`、拒否リスト、停止と削除の受付、ApplyPlan | **正本** | P1 | P1（ほかは IPC で） | 本文を持たない。ディスクの暗号化を推奨 | `prev_hash` の連鎖、隙間のない `run_seq` と `tenant_seq` | 対象 | しない（追記だけ。tombstone のイベントを追記する） | 無期限（オーナーが決める） | 隔離したうえで、復元の関門（21章）を通す |
| `journal.db` の費用の台帳 | `hold`、費用のエントリー、封筒 | **正本** | P1（Journal と同じトランザクション） | P1 | 同上 | Journal と同じ。月ごとのチェックポイント（21.4） | 対象 | しない | 無期限 | 費用の証人と照合する（21.4） |
| `journal.db` の Materialized State の表 | 今の状態の表 | 投影（キャッシュ） | P1（ProjectionRepair） | P1 | 同上 | `state_hash@seq` | 対象外（作り直せる） | — | — | Journal から作り直す |
| `journal.db` の blob の目録の表 | blob の状態（16.1） | 正本（blob の準備の状態について） | P1 | P1 | 同上 | blob のハッシュ | 対象 | 削除の印を付ける | — | blob の実物と照合する |
| `memory/<tenant>.db` | 記憶のメタデータ、系譜、利用の記録、ブラインドトークン（本文はない） | 投影 | P1（投影の適用） | P1 | ディスクの暗号化を推奨 | `source_seq`、`last_applied_tenant_seq` | **対象外**（Journal と blob から作り直す） | 行を消し、`secure_delete` を有効にする | — | 作り直す（tombstone を先に当てはめる） |
| `index/<tenant>/<区画>.db`（FTS5） | 平文の索引（public と internal だけ） | 投影 | P1 | P1 | なし（平文。だから対象を限っている） | `last_applied_tenant_seq` | 対象外 | 行を消し、最適化する | — | 作り直す |
| `keys/<tenant>.db` | ラップした DEK、鍵の記録（20.1） | **正本**（鍵について） | P1（Key Service） | P1（P3 は request の HMAC の鍵の写しだけ） | KEK でラップ | AEAD によるラップの検証、鍵の記録の連鎖 | **別に**取る（保管の期間は短い） | 破棄（`DESTROYED` または `SHREDDED`） | `R_key`（候補は 7 日） | 20.4 の手順で扱う |
| 開発の側の Experiment Registry と Results Store | 実験の仕様と結果（dev と validation） | 正本 | Evaluation | オーナーと Evaluation | ディスクの暗号化を推奨 | `spec_hash` の固定、ハッシュの連鎖 | 対象 | 方針による | 無期限 | 照合してから使う |
| 評価の側の保存先（別のホスト） | holdout の使用の回数、評価の結果、署名 | 正本 | 評価の側の Controller | 評価の側 | 評価の側の方針 | 署名、連鎖、評価の側の証人（21.4） | 評価の側で取る | — | — | 評価の側の証人と照合する |

### 15.2 ファイルとして保存する領域

| 保存先 | 保存する内容 | 正本か投影か | 書く主体 | 暗号化 | 整合性の検証 | バックアップ | 削除 | 復元のときの扱い |
|---|---|---|---|---|---|---|---|---|
| `blobs/` | 本文（Context IR、変換済みの文脈、リクエストの記録、応答、ツールの出力、記憶の本文、成果物、反映の退避） | **正本**（本文について） | P1（SAG を通る） | AEAD（blob ごとの DEK） | 名前が内容のハッシュ。AEAD の検証 | 対象（`R_blob`） | 物理的な削除と、鍵の破棄 | Journal の参照と照合する。欠けていれば `MISSING` |
| Snapshot（A の2章） | `state@seq` | 投影 | P1 | 本文なし | `state_hash@seq`、`journal_head_hash` | 任意 | — | Journal と照合し、合わなければ捨てる |
| 停止の状態のファイル | 範囲ごとの停止の項目 | 写し | P1 | なし（本文を持たない。改ざんの検知は署名で行う） | 署名、`file_seq` | 対象外（Journal が正本） | — | **復元した後は、STOPPED にする**（21.3） |
| 削除台帳（主）と、その独立した複製 | 削除の記録（本文なし。17章） | 削除については**正本**（Journal より優先する。29.6） | P1 | 複製の置き場所で、暗号化を推奨（メタデータの機密のため。29.6） | 連鎖、署名 | 複製そのものが、耐久性を受け持つ | しない | 17章の手順で使う |
| 証人（freshness witness） | 最新性の証拠（17.1、21.4、29.1） | 証拠 | P1 | なし（ハッシュと seq だけ） | 署名、単調に増える seq | 証人の側の耐久性による | しない | 21章の照合に使う |
| 監査ログ | 本文なしの記録 | 正本（監査について） | Audit Writer | 本文なし | 連鎖、チェックポイント | 対象 | しない | チェックポイントと照合する |
| Policy Store | ポリシー、マニフェスト、実行物の登録 | 正本 | オーナー | なし（機密を入れない。改ざんの検知に、署名を推奨） | 版のハッシュの連鎖。証人に `policy_version` を記録する | 対象 | — | 証人と照合する（21.3） |
| 認証情報の保管庫 | 外部への認証情報 | 正本 | オーナー | OS の保護 | — | **対象外**（オーナーが登録し直す） | — | 登録し直す |
| ワークスペースと一時的な置き場 | 作業ツリー、反映の途中の状態 | 外部（Agent Core の正本ではない） | Broker（反映） | — | ApplyPlan のハッシュ | 対象外 | — | 22章 |
| Sandbox の一時領域 | 信頼しないコードの書き込み | 一時のもの | Sandbox | — | — | 対象外 | Run の終わりに破棄 | 回収する（C1 の8.7） |
| ログとトレース | ID、状態、時間（本文なし） | 記録 | Observability | — | — | 任意 | 保存の期間で消す | — |
| バックアップの集合 | 21.1 の目録と、各保存先の写し | 写し | Backup Manager | バックアップの鍵 | 目録の署名とハッシュ | — | 期限で消す | 21章 |

**決まり**

- Journal と費用の台帳は、同じ `journal.db` の、同じトランザクションで更新する（`A.I5`、`E.Z8`）。
- **別々のデータベースやファイルをまたぐ操作は、原子的にならない。** そのため、16章の順序と復旧の手順で、整合を保つ。

## 16. 複数の保存先をまたぐ書き込みと、復旧（E3-02）

### 16.1 blob の状態

| 状態 | 意味 |
|---|---|
| `PREPARED` | 準備した。ディスクにあるが、Journal からまだ参照されていない |
| `COMMITTED` | 確定した。Journal のイベントから参照されている |
| `UNAPPLIED` | 確定したが、投影（Memory Store や索引）にはまだ当てはめていない |
| `MISSING` | 欠損。Journal から参照されているのに、実物がない、または検証に通らない |
| `ORPHAN` | 孤立。`PREPARED` のまま、猶予の期間を過ぎた |
| `TOMBSTONED` | 削除した |

### 16.2 書き込みの順序

```
S1 鍵の準備：keys DB に、DEK を PENDING で作る（別の DB での、別のトランザクション）
S2 blob の準備：暗号化して一時ファイルに書く → fsync → 内容のハッシュの名前に変える → ディレクトリを fsync
   → blob の目録の表に PREPARED と記録する
S3 Journal の確定：イベントで、blob のハッシュと key_id を参照する。費用の台帳も、同じトランザクションで更新する
   ← この時点で確定する
S4 Memory Store への投影：1つのトランザクションで、行、系譜、last_applied_tenant_seq を更新する
S5 検索の索引の更新：区画の DB に当てはめ、その DB の last_applied_tenant_seq を更新する
S6 watermark：投影ごとの last_applied_tenant_seq（S4 と S5 の中で更新する）
S7 回収：鍵と blob の目録を COMMITTED にする。ORPHAN を消す。削除の対象を物理的に消す（17〜19章）
```

### 16.3 クラッシュした場合の復旧

| 障害が起きた位置 | 状態 | 復旧 |
|---|---|---|
| S1 の前 | 何もない | 何もしない |
| S1 の後、S2 の前 | 参照されていない PENDING の鍵 | 猶予の後に破棄する |
| S2 の途中（一時ファイル） | 名前の変わっていない一時ファイル | 削除する |
| S2 の後、S3 の前 | `PREPARED` の blob と、PENDING の鍵 | 猶予の後に `ORPHAN` として、鍵の破棄と blob の削除を行う。この記録は存在しなかったものとして扱う |
| S3 の途中 | SQLite が巻き戻す | S2 の後と同じ |
| S3 の後、S4 の前 | `UNAPPLIED` | `last_applied_tenant_seq + 1` から当てはめ直す |
| S4 の後、S5 の前 | 索引が古い | その区画の検索を止め（`UNAVAILABLE`）、当てはめ直す |
| S5 の後、S7 の前 | 目録の状態が PENDING のまま | Journal の参照があれば `COMMITTED` にする |
| 参照された blob や鍵がない | `MISSING` | その項目を `UNAVAILABLE` にする。**作り直さない**。調査に回す |
| 投影が、最新の削除や権限の変更に追いついていない | 古い投影 | 検索を止める（`B2.M26`） |

### 16.4 ProjectionRepair での決まり

- tombstone と、鍵の破棄の一覧を、**先に**当てはめる。
- 削除した情報を、作り直さない。blob がない、または鍵が破棄されている項目は、`UNAVAILABLE` のままにする。
- 回収（S7）は、バックアップが参照している間は保留する（21.2 の固定）。

## 17. 削除台帳と、独立した複製（E3-03、C2-P03、B2-P01）

### 17.1 5つのものを区別する

| もの | 役割 | 持つ内容 | それだけでは守れないこと |
|---|---|---|---|
| 削除の記録 | 何を、いつ、なぜ消したか | `deletion_id`、対象の ID と版、範囲、理由の区分、受け付けた `tenant_seq`、系譜の対象の一覧のハッシュ、破棄した `key_id` の一覧。**削除の対象の機密の本文は入れない** | — |
| 独立した複製 | 削除の記録**そのもの**の写し（全件） | 削除の記録の全件。追記だけ。署名付き | 複製を失うと、内容は戻せない |
| 証人（freshness witness） | 最新性の証拠 | `{ledger_seq, head_hash, 署名}` だけ | 失われた記録の**内容を戻すことはできない**。巻き戻りを見つけるだけ |
| 台帳の署名 | 記録が本物であること | Ed25519 の署名 | 最新であることは示さない |
| 台帳の anchor | 手元の最新の位置 | `{seq, head_hash}` | 手元と一緒に巻き戻りうる |

**復元に必要な最小限の情報**

- 削除の記録の全件（`deletion_id`、`seq`、ハッシュの連鎖、署名）。
- 破棄した鍵の一覧（`key_id`）。

この2つがあれば、tombstone と鍵の破棄を当てはめ直せる。

- **限界：** 独立した複製を失った場合は、証人が残っていても、失われた削除の記録の内容は戻せない。そのときは、範囲を隔離し、オーナーに確認する（18章の受付記録を使う）。

### 17.2 個人での利用に向けた構成の比較（どれも、契約や接続は今回は行わない）

| 構成 | 独立性 | 耐久性 | 費用 | 手間 |
|---|---|---|---|---|
| (a) 同じホストの別のディスクや区画 | 低い（ホストの故障に巻き込まれる） | 中 | ほぼなし | 小 |
| (b) 家庭内の別の機器（外付けの記憶装置、NAS） | 中 | 中〜高 | 小 | 小〜中 |
| (c) 暗号化した外部のオブジェクトストレージ（版の管理や、削除の禁止の機能があるもの） | 高い | 高い | 小（記録は小さい） | 中 |
| (d) オーナーのスマートフォンに保存（署名付きの記録） | 高い | 機器による | なし | 中 |

**推奨の候補（PROVISIONAL）**

- **主：** ホストの台帳。
- **独立した複製：** (b) または (c) に、同期して書く。
- **証人：** (c) または (d) に置く。複製とは別の場所にする。

過剰な分散システムは作らない。

## 18. 確定していない削除の要求（E3-04、C2-P04）

### 18.1 段階

| 段階 | 起きたこと | 利用者への応答 |
|---|---|---|
| `RECEIVED` | 受信した | なし |
| `ACCESS_STOPPED` | メモリの中で拒否リストに加え、`revocation_gen` を増やした（永続化はまだ） | なし |
| `LOCALLY_ACCEPTED` | P1 のトランザクションで、Journal、拒否リスト、epoch を記録した | 受付記録（状態は `LOCAL`） |
| `REPLICATED` | 独立した複製に、追記と fsync を行い、確認を受け取った | （内部の状態） |
| `FRESHNESS_CONFIRMED` | 証人を更新し、検証した | （内部の状態） |
| `DURABLY_ACCEPTED` | `REPLICATED` と `FRESHNESS_CONFIRMED` の両方がそろった | 受付記録（状態は `DURABLE`） |
| `PURGING` → `COMPLETED_LIVE` | 削除の処理をしている／管理下の対象をすべて処理した | 進み具合 |
| `BACKUP_EXPIRY_PENDING` → `FULLY_COMPLETED` | バックアップの期限を待っている／すべての期限が過ぎた | `DeletionReport` |

### 18.2 削除の受付記録（利用者に返すもの）

`DeletionReceipt{deletion_id, requested_at, target_summary（本文を含まない。記憶の ID、範囲、種類）, state: LOCAL|DURABLE, ledger_seq?, receipt_signature}`

- 削除の受付記録の署名の鍵（Ed25519）の公開鍵は、オーナーが持つ。
- オーナーは、受付記録を、ホストの外（スマートフォンなど）に保存する。

### 18.3 独立した複製に保存する前に、障害が起きた場合

- `DURABLE` の受付記録が出ていない要求は、失われた可能性がある。利用者は、受付記録が `DURABLE` になるまで、同じ `deletion_id` で要求をし直す（冪等）。
- 復元した後に、オーナーが持つ受付記録の中に、台帳にないものがあれば、それを当てはめる。署名で、本物であることを確かめる。
- **待った時間だけを根拠にして、安全と判定しない。** 受付記録も、削除の対象も確かめられない範囲は、隔離を続ける。隔離を解けるのは、オーナーが確かめた後だけ。

## 19. 派生データの削除の統一（E3-05、B1-P03、B2-P03、CP-11）

### 19.1 統一した状態（CP-11）

- **要求の全体の状態：** 18.1 の段階を使う。B2 の7.5 と C2 の7.1 は、これに置き換える。
- **対象ごとの状態：** C2 の8.1 の6つの状態（`PURGED`、`SHREDDED`、`QUARANTINED`、`PENDING_REVIEW`、`EXTERNAL`、`TRACE_LIMIT`）に、`UNAVAILABLE`（欠損）を加える。

### 19.2 対象ごとの扱い

| 対象 | 扱い |
|---|---|
| Memory | 行の削除、記憶ごとの鍵の破棄、索引の削除 |
| Context（IR、変換済みの文脈、リクエストの記録） | 系譜で特定し、blob ごとの鍵を破棄して、blob を削除する |
| 要約 | 削除する（プライバシーによる削除の場合） |
| 検索のキャッシュ | メモリの中だけにあるので、すぐに捨てる |
| モデルの応答、ツールの出力 | blob ごとの鍵を破棄して、blob を削除する |
| 成果物 | 管理下の blob は削除する。ワークスペースのものは `PENDING_REVIEW` |
| ログとトレース | 本文を持たないので、ID の記録だけが残る |
| 評価の記録 | 開発の側の記録は、系譜で特定して削除する。評価の側は、評価の側で同じ手順を行う |
| バックアップ | 期限を待つ（`BACKUP_EXPIRY_PENDING`） |

### 19.3 共通の決まり

- **系譜の追跡：**
  - 系譜をたどる途中の状態は、`DeletionTraceState` として Journal に記録する。後で再開できる。
  - 上限に達したら、関係する範囲を隔離する（C2 の8.1.1）。
- **実行中の Run：** 削除を受け付けると `revocation_gen` が増える（E の3.3）。そのため、その内容を使った新しい送信、記憶、成果物の生成は止まる。
- **外部に送ったデータ：** 消せたとは言わない。
- **再抽出の防止：** 系譜が削除の対象に触れる候補は、却下する（`B2.M25`）。抑止の一覧は、任意で使う（C2 の8.3）。
- **完了の区別：** `COMPLETED_LIVE`（管理下はすべて処理した）と、`BACKUP_EXPIRY_PENDING`（バックアップの期限待ち）を分けて、利用者に示す。

## 20. 暗号鍵の更新と、障害からの復旧（E3-06、C2-P05）

### 20.1 統一した記録

**鍵の記録**

`KeyRecord{key_id, purpose, scope, version, state, wrapped_material, kek_id, created_at, activated_at?, retired_at?, destroyed_at?, compromise_window?, hash_verifiable_until?}`

| `state` の値 | 意味 |
|---|---|
| `PENDING` | 作ったが、まだ使っていない |
| `ACTIVE` | 新しい書き込みに使っている |
| `RETIRING` | 新しい鍵へ移している途中 |
| `RETIRED` | 移し終えた。読み取りと検証にだけ使う |
| `DESTROYED` | 通常の手順で破棄した |
| `SHREDDED` | 削除のために破棄した |
| `COMPROMISED` | 漏えいした |

**暗号文の封筒**

`{alg_id, key_id, nonce, aad_hash, ciphertext}`

- AAD の中身は、C2 の6.6 のとおり。
- blob の参照は、`{blob_hash, key_id}` を持つ。
- KEK は、`kek_id` と版を持つ。

### 20.2 通常の鍵の更新（データの鍵）

```
① 新しい鍵を PENDING で作る → KeyCreated → ACTIVE（新しい書き込みに使う）。古い鍵は RETIRING
② データごとに：新しい暗号文の blob を準備（PREPARED。16.2）
   → Journal に ObjectRekeyed{old_ref, new_ref, new_key_id} を記録して確定
   → 投影を更新 → 古い blob は、保管の期間の後に回収する
③ 古い鍵を参照する、確定したデータが 0 件になり、
   さらにその鍵を必要とするバックアップの期限が切れたら → RETIRED → 保管の期間の後に DESTROYED
```

- **障害の後の再開：** データごとに `key_id` を持っているので、古い鍵と新しい鍵のデータが混ざっていても、読める。古い `key_id` のデータを探して、②から続ける。
- **新しい暗号文が永続化され、参照が確定したことを確かめる前に、古い鍵を破棄しない。**

### 20.3 KEK の更新

- データの鍵を、新しい KEK でラップし直す（行ごとに `kek_id` を持つので、途中で止まっても再開できる）。
- すべてラップし直したら、古い KEK を `RETIRED` にする。

### 20.4 削除のための鍵の破棄と、復元

- 削除のために破棄した鍵の `key_id` は、削除の記録（17.1）の「破棄した鍵の一覧」に入れる。
- 復元するときは、データを使い始める前に、この一覧の鍵を**もう一度破棄する**（21.3）。古い鍵のバックアップを戻しても、消去を取り消すことにはならない。
- `FULLY_COMPLETED` は、鍵のバックアップの期限が過ぎるまで待つ（C2 の8.5）。

### 20.5 漏えいしたときの失効

- C2 の6.5 のとおり。通常の更新とは別の手順で扱う。
- 漏えいしたかもしれない期間に作った記録は `SUSPECT` として扱い、独立した根拠で検証し直す。
- 失効した鍵の一覧は、証人にも記録する（21.4）。

### 20.6 古い HMAC の鍵を破棄した場合

- その鍵で作ったハッシュは、照合できなくなる。
  - Replay：その記録は `REPLAY_UNAVAILABLE`。
  - `context_hash` の比較：鍵の版が違えば、比較できないと扱う。
- そのため、HMAC の鍵には `hash_verifiable_until` を持たせる。照合が必要な記録の保管の期間が終わるまで、破棄しない。削除のための破棄は例外である。

## 21. バックアップと復元（E3-07、A-P1、E2-P03、E2-P06）

### 21.1 バックアップの目録

`BackupManifest{backup_id, generation, created_at, schema_versions, journal: {tenant_seq, head_hash}, cost_ledger: {同じ位置, 月ごとの合計}, blobs: {一覧のハッシュ（Merkle の根）, 件数}, keys: {keys_db の版, 鍵の記録の連鎖の先頭}, deletion_ledger: {seq, head_hash}, policy: {policy_version, hash}, witness_refs, signature}`

- 複数の保存先を、どの時点で組み合わせたかを、目録で確かめられるようにする。

### 21.2 取り方（単一のホスト。整合した時点で取る）

1. P1 が、新しいトランザクションを短い間止める。
2. `journal.db` を、その時点の `tenant_seq` で写す。SQLite のオンラインでのバックアップの機能を使う予定だが、仕様は確かめていない。
3. 同じ時点の `keys` を写す。
4. blob は、内容のハッシュの名前で変わらないので、目録に載せた一覧を後から写してよい。そのために、バックアップを取っている間は、回収（S7）でそれらを消さないように固定する。
5. 投影（Memory Store、索引、Materialized State）は、写さない。作り直す。

### 21.3 復元の手順（復元の関門）

```
R0 隔離した環境で始める：ネットワークを切り、PEP を無効にし、停止の状態を STOPPED にする
R1 バックアップそのものの整合性：目録の署名、Merkle の根、Journal の連鎖を確かめる
R2 最新の削除台帳：独立した複製と証人を確かめる → tombstone と、鍵の破棄の一覧を当てはめる
   → オーナーが持つ受付記録も当てはめる（18.3）
R3 今のアクセスの権限：Policy Store の今の版と照合する（証人の policy_version と比べる）
   → バックアップの中の古いポリシーや権限は使わない
R4 今の鍵の失効の状態：証人の失効の一覧と照合する → 失効した鍵と破棄した鍵は、使えないようにする
R5 停止の状態：復元したものは使わない。STOPPED のままにし、オーナーが再開するまで待つ
R6 使った費用と、未確定の費用：費用の証人（21.4）と照合する。
   - 復元したものが証人より少なければ、証人の値を使う（多く数える側）
   - 復元の前の hold は、すべて UNKNOWN として押さえる
R7 支出の承認と、環境への予算の割り当て：復元の前の SpendApproval と封筒は、すべて無効にする → 承認を取り直す
R8 holdout の使用の回数：評価の側の証人と照合する（評価の側で行う）
R9 結果不明の外部操作：CONSUMED、ACTIVE、UNKNOWN の Attempt は、すべて OUTCOME_UNKNOWN にして照合する
R10 成果物の反映の途中の状態：22章の手順で、確認と巻き戻しを行う
R11 世代を新しくする：owner_epoch、access_epoch、revocation_gen、PEP のインスタンスの登録、Worker の世代
    → 古い Permit、Receipt、送信の Lease は、すべて無効になる
R12 Run：復元した Run は RECOVERING にする。自動では再実行しない
    → オーナーが判断して、区切りの点から、新しい Run（新しい承認）として始める
```

- **最新であることを確かめられない場合**（R2〜R4、R6、R8 のどれか）は、実行も外部への送信も許さない。

**古い状態が復活しても、使えないようにする（E2-P03）**

| 復活しうるもの | 再利用させない仕組み |
|---|---|
| 失効した権限、古いポリシー | R3（証人の `policy_version`） |
| 使用済みの Permit と Receipt、送信の Lease、Worker の世代 | R11（世代の更新） |
| 使用済みの予算 | R6（費用の証人） |
| 古い SpendApproval | R7（すべて無効にする） |
| 緊急停止の前の状態 | R5 |
| 以前の暗号鍵 | R2・R4（破棄の一覧と、失効の一覧） |
| 既に実行した外部操作 | R9（自動では再実行しない。`READ_ONLY` を除く） |

### 21.4 最新性の証拠と、復元の後の照合（E2-P06）

**署名だけでは、最新であることは示せない。** 次の値は、単調に増える項目として、証人（17.2 の (c) か (d)）に記録する。

| 記録の種類 | 内容 | 書く時期 |
|---|---|---|
| `deletion_ledger_head` | 削除台帳の先頭 | 削除のたびに |
| `cost_checkpoint` | 月ごとの、確定した額と hold の合計 | 一定の間隔、および承認のたびに |
| `envelope_state` | 環境ごとの封筒の状態 | 変更のたびに |
| `policy_version` | ポリシーの版 | 変更のたびに |
| `key_revocation_head` | 鍵の失効と破棄の一覧の先頭 | 変更のたびに |
| `holdout_usage` | holdout の使用の回数と、公開した量 | 評価の側の証人に、使うたびに |

- 復元した後は、証人の値と比べる。
- **上限を使い直す方向に戻っている場合**（使用の回数や費用が少ない、版が古い）は、証人の値を正とする。
- 証人に届かない場合は、費用の支出、holdout の使用、外部への送信を止める。

### 21.5 個人での利用に向けた、間隔と期間の候補（PROVISIONAL。未承認）

| 項目 | 候補 |
|---|---|
| Journal のバックアップ | 1 時間ごと（差分）、1 日ごと（全体） |
| 保管の期間 | `R_data` 30 日、`R_blob` 30 日、`R_key` 7 日、`R_ledger` 無期限 |
| 復旧の目標 | 失ってよいデータの範囲（RPO）1 時間、復旧にかかる時間（RTO）4 時間 |
| 保存の費用 | 必要最小限（本文を持たない記録が中心なので、小さい） |

## 22. 成果物の反映の復旧（E3-08、E2-P02、C1-P03）

### 22.1 優先する方式：専用の作業ツリー

- Agent Core が管理する、別の作業ツリー（git の worktree など）で、成果物を完成させる。
- 検証（`VERIFYING`）を通った後に、管理された参照を切り替える（ブランチの参照の更新など）。
- 切り替えの1回の操作が原子的になるかは、仕組みによる。どの仕組みでそうなるかは、確かめていない。

### 22.2 外部のプログラムが自由に変えられるワークスペースの場合

- **複数のファイル全体の原子性は、保証しない。**
- そのうえで、次を行う。

| 項目 | 内容 |
|---|---|
| 反映の前の状態 | 対象ごとの `pre_hash`、ファイルがあるかどうか、権限。ApplyPlan に記録する |
| 耐久性のある退避 | 変更と削除の対象の元の内容を、暗号化した blob に退避する（16.2 の S2・S3）。**Journal で確定した後に**、最初の変更を始める |
| 反映の進み具合 | ファイルごとに `ApplyStep{STARTED, DONE}` を記録する |
| 反映の直前の検査 | ファイルを置き換える**直前に**、今のハッシュが `pre_hash` と一致することを、ファイルごとに確かめる。検査と置き換えの間の、わずかな時間差は残る（限界） |
| 排他 | 協調のための排他のロックだけ（外部のプログラムは守らない可能性がある） |
| 巻き戻しの進み具合 | `RollbackStep{STARTED, DONE}` を記録する |
| 障害の後の再開 | 既定では巻き戻す。すべてのファイルが `DONE` で、`post_hash` と一致する場合だけ、先へ進めてよい |
| 巻き戻しの途中の外部の変更 | 今のハッシュが `post_hash` でない場合（外から変わった）は、上書きせず、`CONFLICT` にする |
| 削除したファイルの復元 | 退避した blob から戻す |
| 途中の状態の隔離 | ApplyPlan が `COMMITTED` になるまで、その Run の成果物を、書き出しにも、次の作業の入力にも使わない |
| 人間への確認 | `CONFLICT` の場合と、巻き戻しに失敗した場合は、`RECONCILIATION_REQUIRED` にする |
| 正常に完了したと言える条件 | 次をすべて満たすこと。<br>- すべてのファイルが `DONE`<br>- すべての `post_hash` を確かめた<br>- 作業ツリー全体の最終の検証に合格した<br>- ApplyPlan が `COMMITTED`<br>- Verification に合格した |

- 部分的な反映を、成功として扱わない。外部の変更を上書きしない。
- 安全なファイル操作の手段（`openat2` など）の、OS の実際の仕様は確かめていない。

## 23. 個人での利用に向けた構成（E3-09）

**初期に優先すること**

- 単一のホストで運用する。
- 保存は、SQLite と、管理したファイルにする。
- 自動化できるバックアップにする（21.2）。
- 障害の状態を、スマートフォンから確かめられるようにする。示すのは、本文を含まない要約だけ。具体的には、停止の状態、隔離の状態、結果不明の件数、バックアップの新しさ、証人に届くか。画面の設計は E4 で行う。
- 長時間の稼働（9章）。
- 復元と保守を、易しくする。
- 保存の費用を、必要最小限にする。

**前倒しで必須にしないもの**

- 大規模なマルチテナント、分散データベース、企業向けの KMS。

**維持するもの**

- 独立した評価の環境。
- 必要な鍵の分離。
- 削除の記録の耐久性（独立した複製と証人）。

## 24. 実装するときの検証の項目（E3-10。実装も試験も、まだしていない）

| 項目 | 検証の方法 | 合格の条件 |
|---|---|---|
| Journal の確定の前後の障害 | 16.2 の S1〜S7 の各地点で、プロセスを強制的に止める | 16.3 の表のとおりに復旧する割合 100%。二重に当てはめたもの 0 |
| blob の欠損 | 参照された blob を消す、または壊す | `MISSING` から `UNAVAILABLE` になる割合 100%。作り直したもの 0 |
| 孤立した blob | S2 の後で止める | 猶予の後に回収される割合 100% |
| 古い検索の索引 | 索引の `last_applied` を戻す | 追いつくまで、検索を止める割合 100% |
| 削除台帳と anchor の食い違い | 両者を食い違わせる | 7.3（C2）の自動の修復と、止める場合を、正しく区別する割合 100% |
| 削除の記録の複製の欠損 | 複製を消す（証人は残す） | 失われたことを見つけ、範囲を隔離する割合 100%。復元できたと誤って判断した件数 0 |
| 古いバックアップからの復元 | 削除の前のバックアップを戻す | R0〜R12 を通るまで、実行と送信が 0 |
| 削除した記憶の復活 | 上と同じ | 削除した記憶を使えた件数 0 |
| 鍵の更新の途中の障害 | 20.2 の①〜③の各地点で止める | すべてのデータを読める割合 100%。古い鍵を、早く破棄した件数 0 |
| 失効した鍵の復活 | 失効の前の鍵のバックアップを戻す | その鍵を使えた件数 0 |
| 使用済みの予算の復活 | 費用の台帳を古い状態に戻す | 証人の値で、正しく直される割合 100% |
| 古い承認と Permit の復活 | 復元の前の SpendApproval、Permit、Receipt を使おうとする | 使えた件数 0 |
| holdout の使用の回数の復活 | 評価の側の記録を古い状態に戻す | 証人の値で、正しく直される割合 100%。上限を超えた使用 0 |
| 成果物の反映と巻き戻しの途中の障害 | ApplyStep と RollbackStep の途中で止める。外から同時に変える | 外部の変更を上書きした件数 0。部分的な反映を成功とした件数 0 |
| 外部操作の重複した実行 | 復元の後、`OUTCOME_UNKNOWN` の操作がある状態にする | 自動で再実行した件数 0（`READ_ONLY` を除く） |

- **試験に合格しても、完全に安全であることの証明にはならない。**

## 25. E3 の不変条件（`E.Z` の続き）

| ID | 不変条件 |
|---|---|
| E.Z17 | 別々の DB やファイルをまたぐ操作は、原子的であるとは扱わない。順序（16.2）と、復旧の表（16.3）で整合を保つ |
| E.Z18 | 確定（Journal の参照）がない blob と鍵は、存在しなかったものとして回収する。参照されているのに欠けているものは、作り直さない |
| E.Z19 | 削除の記録の独立した複製は、本文を持たない。証人は、最新性を示すだけで、内容を戻す手段ではない |
| E.Z20 | `DURABLE` の受付記録が出るまでは、削除の要求が障害の後も残るとは言わない。待った時間だけを根拠にして、安全と判定しない |
| E.Z21 | 新しい暗号文の参照が確定するまで、古い鍵を破棄しない。削除のために破棄した鍵は、復元しても使えるようにしない |
| E.Z22 | 復元は、隔離した環境で、R0〜R12 を通ってから使う。最新であることを確かめられなければ、実行も送信もしない |
| E.Z23 | 復元すると、世代が新しくなり、古い Permit、Receipt、送信の Lease、SpendApproval は使えなくなる。復元した Run は、自動では再実行しない |
| E.Z24 | 上限に関わる値（費用、封筒、holdout の使用の回数、ポリシーの版、鍵の失効）は、証人と照合し、使い直す方向には戻さない |
| E.Z25 | 外部のプログラムが変えられるワークスペースでは、複数のファイル全体の原子性を保証しない。外部の変更を上書きせず、部分的な反映を成功とはしない |

## 26. E3 の残存リスク

1. **台帳の喪失：** 独立した複製と、オーナーの受付記録の両方を失うと、削除の要求を戻せない。範囲を隔離し続けるしかない。
2. **証人に頼ること：** 証人に届かない間は、費用の支出、holdout の使用、外部への送信が止まる。証人そのものが乗っ取られると、巻き戻りを見つけられない。
3. **同じ時点での巻き戻り：** 台帳、複製、証人を、すべて同じ時点まで巻き戻されると、見つけられない。
4. **反映の直前の時間差：** 検査と置き換えの間の、わずかな時間差（TOCTOU）は残る。
5. **バックアップの整合：** オンラインのバックアップの機能や、ブロックの単位での写しの仕様は、確かめていない。
6. **物理的な消去：** 媒体のレベルでの消去は保証できない（C2 の7.6）。

## 27. E4 へ必ず引き継ぐもの

| ID | 内容 |
|---|---|
| E2-P01 | Lease と、送信開始の競合（OS による強制、プロセスの停止、送信の経路の遮断） |
| E2-P04 | Run ごとの支出の承認の再利用（`run_plan_id` と `run_id` の対応、再開・再試行・新しい Run の違い） |
| E2-P05 | 外部通信の開始の定義（DNS、接続、TLS、部分的な送信、ライブラリの中のバッファ） |

- このほか、E4 で扱うもの：正式なロードマップ、Phase の開始と完了の条件、Phase 1 の実装のタスク、対応する OS、LiveProvider の循環依存の解消の案、実API の使用についてのオーナーの承認の案、スマートフォンからの遠隔操作、残存課題と不変条件の最終的な整理、`AGENTS.md` の更新、過剰な設計の削減。
- Service Harness は、本体が完成した後の、独立したサブ機能。商用化は、必須の目標ではない。

---

# 第3部：E4（残存課題の解決、正本の決まり、最終の整合）

> この部は E4 の版（DRAFT）。新しい機能は加えていない。残存課題の解決と、実装の計画を優先した。
> ロードマップは `ROADMAP.md`、Phase 1 の計画は `PHASE_1_IMPLEMENTATION_PLAN.md`、監査の準備は `docs/reviews/FINAL_AUDIT_CHECKLIST.md` に置いた。
> 初回 Phase 0 報告書の全文は、Claude Code の側では受け取っていない。全文との照合は行っていない（A-P6 は未解決のまま）。

## 28. 正本の決まり（資料が食い違うときに、どれを有効とするか）

上にあるものほど優先する。

1. `docs/decisions/OWNER_DECISIONS.md`（オーナーの確定事項）。
2. `E_INTEGRATION.md`。同じ文書の中では、新しい部を優先する（第3部 ＞ 第2部 ＞ 第1部）。
3. `RESOLUTION_MATRIX.md`（課題の状態）。
4. 各資料の「E2・E3・E4 での統合」の注記の章。
5. 各資料の本文。

- 進捗の正本は `docs/STATUS.md`。
- 予定の正本は `ROADMAP.md`。

## 29. E3 の残存課題の解決

### 29.1 E3-P01：費用と holdout の巻き戻り

| 方式 | 良い点 | 問題 |
|---|---|---|
| (1) 実行の前に、予約を独立した記録に保存する | 失われた期間の支出も、確実に数えられる | 呼び出しのたびに、外の記録に書く必要がある |
| (2) 独立した記録で予算の枠を割り当て、復元のときに、まだ照合していない分を多めに押さえる | 外に書く回数が減る | 枠の大きさの分だけ、使えない予算が出る |
| (3) 最新の利用の実績が分からなければ、支出を止める | 単純 | 止まる時間が長くなる |

**選んだ最小の構成（個人での利用）：(1) を Run の単位で行い、(3) を予備にする。**

- Run を始める前に、その Run の承認の最大の費用（E の4.5）を、独立した記録（証人の記録の種類 `spend_allocation`）に書いて、枠を押さえる。
  - 書いた結果は、`seq` と連鎖で確かめる。
  - 書けなければ、Run を始めない。
- Run の中の呼び出しの予約は、手元の台帳で行う。合計が枠を超えることはできない。
- Run が終わり、照合が済んだら、`spend_release` の記録を追記する。
- **復元のとき：** 証人にある `spend_allocation` のうち、`spend_release` のないものは、すべて使い切ったものとして押さえる。
- **最新性：** 合計の額は、照合や解放で減ることがある。そのため、合計を単調に増える値としては扱わない。最新性は、単調に増える `seq` と、記録の連鎖で確かめる。
- **holdout の使用の枠：** 評価を実行する前に、評価の側の独立した記録に、使用の枠（`holdout_slot`）を `seq` 付きで押さえる。集計を公開する前にも、同じく公開の枠（`disclosure_slot`）を押さえる。
  - 結果が分からなくても、枠を戻さない。
- **失われた期間の外部操作：** 証人に `spend_allocation` があるのに、復元した Journal にない `run_id` は、「失われた期間の Run」と判定する。
  - その Run の承認の計画は、使用済みとする。
  - 同じタスクとツールの組の自動の再実行を止め、範囲を隔離する。
  - オーナーが、プロバイダーの記録などで照合してから、隔離を解く。
  - 古いバックアップだけからは、失われた期間に何が起きたかを特定できない場合がある（限界）。

### 29.2 E3-P02：削除と、鍵の更新の競合

1. **削除を受け付けたら、鍵の更新と、派生データの生成を止める。**
   - `ACCESS_STOPPED` の時点で、拒否リストに、対象の記憶の ID、系譜の blob のハッシュ、`key_id` を入れる。
   - Key Service は、拒否リストに当たる対象の、鍵の更新を始めない。
2. **更新の前と後の系譜：** `ObjectRekeyed{old_ref, new_ref, old_key_id, new_key_id, object_id}` を系譜に入れる。削除は、両方の blob と、両方の鍵に及ぶ。
3. **確定した鍵と、準備中の鍵の両方を消す：** 鍵の記録に `object_ref` を持たせる。そうすれば、準備中（`PENDING`）の鍵と、確定していない新しい blob も、対象から見つけられる。それらを破棄して消す。
4. **競合の順序：** 鍵の更新の確定のトランザクションは、拒否リストを確かめる。P1 は書き込みの主体が1つなので、削除の受付と鍵の更新の確定は、必ずどちらかが先になる。
   - 削除が先なら：更新は確定せずに取り消す。
   - 更新が先なら：新しいものも削除の対象になる。
5. **古いバックアップからの復元：** 削除の記録の「破棄した鍵の一覧」には、更新の前と後の `key_id` をすべて入れる。復元の R2 で、もう一度破棄する。
6. **参照を切り替えるとき：** 鍵の記録の版と、削除の状態を、同じトランザクションの中で確かめる。

- **削除を優先し、鍵の更新によって、削除した情報が復活しないようにする**（`E.Z26`）。

### 29.3 E3-P03：復元の環境から、証人への接続

**2つの通信を分ける**

| | 通常の Agent の外部通信 | 復元の管理の処理（Restore Manager） |
|---|---|---|
| 主体 | P3 の Egress Gateway | 信頼する保守のためのプロセス（別の OS ユーザー）。復元のモードでだけ動く |
| 認証情報 | 外部の API の認証情報 | **復元専用の、限定した認証情報**：複製と証人の読み取り。証人への「復元を記録する」追記だけを許す |
| 送信先 | 登録した接続先 | 登録した複製と証人だけ |
| 操作 | 認可された操作 | 読み取り（一覧、取得）と、決まった追記だけ |
| 監査 | 通常の監査 | 1回ごとの操作を、監査ログに記録する |

- 復元の間、通常の Agent と、復元の中の Run には、通信の権限を与えない（R0 の PEP の無効化は、そのまま）。

**証人が古い値に書き換えられないための条件（どれも前提であり、確かめていない）**

- 証人は、`seq = 前の値 + 1` で、P1 の鍵の署名がある追記だけを受け付ける。
- P1 の認証情報には、書き換えと削除の権限を与えない（追記だけ）。管理用の権限は、オーナーが別に持つ。

**競合と、更新の途中の障害**

- **競合：** 同じ `seq` への追記は、片方だけが成功する。失敗した側は、読み直してから進む。
- **更新の途中の障害：** 追記が成功したかどうか分からない場合は、読み直して確かめる。確かめられなければ、確定していないものとして扱う。

### 29.4 E3-P04：バックアップと、鍵の保存の期間

**復元できるバックアップの期間**

- 復元できる期間 = `min(R_data, R_blob, R_key, 複製と証人の保管の期間)`。
- そのうえで、**復元の試験に合格した世代だけ**を「復元できる」と言う。バックアップを持っているだけでは、復元できるとは言わない。

**鍵の保存の期間**

- `R_key` は、`R_data` 以上にする（候補は、どちらも 30 日）。これまでの候補の「`R_key` 7 日」は、取り下げる。

**削除の対象の例外**

- 削除のために破棄した鍵は、鍵のバックアップの中に残っていても、復元のときに必ず破棄し直す（`E.Z21`）。
- `FULLY_COMPLETED` は、`R_key` が過ぎるまで待つ。

**KEK の更新**

- 復元できる期間の中の世代が使う、古い KEK は、オーナーが別の場所に保管して残す。
- その世代がすべて期限切れになったら、古い KEK を破棄する。

**完全性の試験**

- 定期的に、隔離した環境で、R0〜R12 を通す復元の試験を行う（候補は月に1回）。
- 結果は、世代ごとに `restore_verified_at` として記録する。

**RPO の意味**

- RPO（候補は 1 時間）は、**作業の状態**を失ってよい範囲である。
- 予算、削除、失効、外部操作の記録を失ってよい、という意味ではない。
  - これらは、独立した記録（予算の枠、削除の複製、ポリシーの版と鍵の失効の証人）を、**同期で**書いて守る。
  - 外部操作は、Run の枠（29.1）で守る。

**RPO や RTO を満たせない場合**

- 状態を `BACKUP_DEGRADED` にして、オーナーに知らせる。
- 独立した記録に書けない場合は、支出と削除の確定を止める（安全側に倒す）。

### 29.5 E3-P05：成果物の反映と、検証の順序（6章、7.3、22章の循環を直す）

```
1. 隔離した、管理された作業ツリーで、成果物を作る
2. 反映の前の検証（Verification）：その作業ツリーの中で、完了条件、テスト、成果物の検査を行う
   → 検証した木のハッシュを記録する
3. ApplyPlan の認可：PDP の判定と、必要な承認。対象は、検証した木のハッシュに結び付ける
4. 反映、または管理された参照の切り替え
5. 反映の後の整合性の確認：反映した結果が、検証した木のハッシュと一致することを確かめる
6. 最終の完了の判定：1〜5 がすべて済み、ApplyPlan が COMMITTED であること
```

- **ApplyPlan が要らないタスク：** 読み取り専用の調べもの、回答だけのタスクなど。2 と 6 だけを行う。
- **外部のプログラムが自由に変えられる作業領域：** 検査と置き換えの時間差で、外部の変更を上書きする危険が残る。
  - そのため、**初期の実装では、管理された専用の作業ツリーだけを使う。**
  - 安全な反映を保証できない場合は、直接は反映しない（Phase 1 では、外部の作業領域への直接の反映を提供しない）。
- 7.3 の「完了する前に `VERIFYING` を通り、ApplyPlan が `COMMITTED` であることを確かめる」は、上の 2（反映の前の検証）と 6（最終の判定）の2段階に読み替える。

### 29.6 E3-P06：メタデータと、削除の完了

**メタデータの分類**

- ID、時刻、範囲、参照、ハッシュも、元のデータの機密を推測させることがある。そこで、メタデータにも、元のデータのラベルから導いた `meta` のラベルを付ける。
  - 元が `confidential` か `personal` のときは、`meta_confidential`。
- **減らす工夫：**
  - ID は、意味を持たない値にする。
  - 自由な文章をメタデータに入れない。
  - 削除の受付記録の `target_summary` は、件数と種類だけにする。

**SAG と、読み取りの権限、保存の期間**

- Journal、台帳、ログ、削除の記録への書き込みも SAG を通す。決まった項目だけを許す形（スキーマ）で検査する。
- 読めるのは、P1 とオーナーだけ。ログは、オーナーだけ。
- ログの保存の期間の候補は 30 日。Journal は無期限（オーナーが決める）。

**表の書き方の訂正**

- 15.2 の表で、「暗号化」の列に「署名」と書いていたのは誤りである。**署名は暗号化ではない。** 表を直した。

**削除の完了**

- `UNAVAILABLE`、`TRACE_LIMIT`、`PENDING_REVIEW`、未解決の `QUARANTINED` のどれかが残っていれば、削除を完了にしない（`C2.X16`）。

**台帳と Journal のどちらを優先するか**

- **削除については、削除台帳（と、その複製）を優先する。** 削除は、前へ進む向きだけが安全だからである。
- それ以外の状態については、Journal が正本である。
- 古い Journal を復元しても、R2 で台帳の削除を先に当てはめる。そのため、削除した情報は使えない。

## 30. E2 から残っている課題の解決

### 30.1 E2-P01：Lease と、送信開始の競合

**Lease の期限が切れただけでは、古い PEP が送れなくなったとは判断しない。**

- 確認を通った直後に、プロセスが止まったり（長い停止、OS の休止）、時計がずれたりしうるため。
- そのため、P1 が `REVOCATION_ENFORCED` にしてよいのは、PEP ごとに、次のどれかを確かめた場合だけとする。

| 条件 | 内容 |
|---|---|
| (a) ack | その PEP から ack を受け取った |
| (b) 停止の確認 | 信頼する Process Supervisor（OS の初期化の仕組みなど）が、PEP のプロセスを強制的に止め、終了したことを確かめた |
| (c) 送信の経路の遮断 | Supervisor が、P3 の OS ユーザーの外への通信を、OS のファイアウォールですべて拒否し、その規則が効いていることを確かめた |

- (b) と (c) は、OS による強制であり、PEP の協力に頼らない。

**時計の扱い**

- Lease の期限は、OS の休止の時間も数える単調な時計で測る。
- OS が休止から戻ったら、PEP は登録し直すまで、送信しない。
- どの時計がこの性質を持つかは、確かめていない（候補は Linux の `CLOCK_BOOTTIME`）。

**再起動のとき**

- 新しい PEP のインスタンスを登録できるのは、Supervisor が、古いプロセスの終了を確かめた後だけ。

**Phase 3A の安全のゲート**

- (b) と (c) が働くことを試験で確かめることを、3A の Safety Gate に入れる（ROADMAP）。

### 30.2 E2-P04：Run ごとの承認の再利用

**RunPlanBinding（計画と Run の結び付け）**

`RunPlanBinding{run_plan_id（UNIQUE）, run_id（UNIQUE）, approval_id, approval_epoch, environment_id, site_id, bound_at, state: AVAILABLE|BOUND|CONSUMED|VOID}`

- Run を始めるときに、計画の項目を `BOUND` にする。1つの計画から、作れる Run は1つだけ。

**承認の内容**

| 項目 | 内容 |
|---|---|
| モデル | `model_id` |
| 構成 | `config_hash` |
| タスク | `task_id` |
| 費用 | `max_cost` |
| 期間 | `valid_from`、`expires_at` |
| 環境 | `environment_id`、`site_id` |
| 世代 | `approval_epoch` |

**違いを分ける**

| 場合 | 扱い |
|---|---|
| **承認された Run の再開** | 同じ `run_id` が、終了の状態でなく、`RECOVERING` から戻る場合だけ。同じ結び付けを使う |
| **新しい Run の開始** | 新しい `run_id` には、新しい計画の項目（新しい承認）が必要。同じタスクでも同じ |
| **再試行** | Run の中の Attempt。Run の予算の中で行う。計画の項目は増やさない |

**再使用の禁止**

- `environment_id` か `site_id` が違えば、拒否する。
- 復元すると `approval_epoch` が新しくなるので、古い承認は無効になる（`E.Z23`）。

### 30.3 E2-P05：外部通信の開始の定義

**外部通信の段階**

| 段階 | 外に伝わるもの |
|---|---|
| X0 名前解決（DNS） | ホスト名 |
| X1 接続（TCP） | IP とポート |
| X2 TLS の握手 | SNI（ホスト名） |
| X3 HTTP のリクエストの行とヘッダー | パス、クエリ、ヘッダー、認証情報 |
| X4 本文 | リクエストの本体 |
| X5 応答の受信 | — |

**決まり**

- **X0〜X4 は、どれも外部通信である。** どれも、認可（Permit の消費）と送信先の検査（C1 の9.2）を通った後に、送信の関門の中で始める。
- E2 の3.1 にあった「接続と TLS の握手は、関門の外で、あらかじめ済ませておく」は、取り下げる。
- **接続の使い回し：** 同じ `destination_id` について、認可された Permit のもとで作った接続だけを使い回してよい。新しいリクエストの中身を送るには、毎回、新しい Permit が必要。

**`DISPATCH_STARTED` の定義（改める）**

- ブロックしない書き込みが、X0〜X4 のどれかで、**OS に 1 バイト以上を受け取らせた**時点とする。
  - 書き込みが 0 バイトだった場合、または「後でやり直せ」という意味のエラーだった場合は、まだ始まっていない（`DISPATCH_AUTHORIZED` のまま）。
  - 一部だけ書けた場合は、すでに始まっている。
  - 1 バイト以上を書いた後に失敗した場合は、`OUTCOME_UNKNOWN`。

**ライブラリの中のバッファと、自動の再試行**

- Gateway が、書き込みを自分で制御する最小限のクライアント（C2 の5.6）を使う。
- ライブラリの内部のバッファや、自動の再試行は使わない。再試行は、どれも新しい Attempt として扱う。

**結果が分からない操作の、自動の再試行**

- `READ_ONLY` という分類だけでは、自動で再試行しない。自動で再試行してよいのは、次をすべて満たす場合だけ。
  1. マニフェストで、外部の課金と、副作用がないと宣言している。
  2. 再試行してよいことを、オーナーが確かめて登録している（`external_guarantee=verified_retry_safe`）。
  3. 予算を、新しく予約できる。
- **モデルの呼び出しは、課金があるので、自動では再試行しない。**
- E2 の3.4 の、「自動で再実行するのは、`READ_ONLY` の操作だけ」は、この条件に置き換える。

**限界**

- すでに始まった外部操作は、完全には取り消せない。

## 31. LiveProvider と、実API の費用（設計A の I10 の循環を解く）

### 31.1 4つの段階を分ける

| 段階 | 内容 | 許す時期 |
|---|---|---|
| L1 実装 | Anthropic の Adapter のコードを書き、偽の通信の経路（試験用のサーバー）で試す | **Phase 3A**（実API の費用は $0） |
| L2 ビルドへの組み込み | 実行用のビルドに Adapter を含め、設定で選べるようにする | 3A の Technical Gate と Safety Gate を通った後の、「実API 用」の構成だけ。既定のビルドには含めない |
| L3 認証情報の注入 | Egress Gateway の保管庫に、API キーを置く | オーナーが SpendApproval（Run ごとの計画、見積もり、最大の費用、月額の上限）を承認した後 |
| L4 通信の経路の有効化 | 送信先を登録し、ファイアウォールで許す | 承認した Phase 3B の期間と、Run の範囲の中だけ |

- この分け方なら、**3A で接続の機能を試すこと自体は禁止されない。** そのため、循環は起きない。
- D1（Phase 2 の終わりまで、実API の費用は $0）とは、食い違わない。3A も $0 である。

### 31.2 流れ

```
Phase 1〜2：Fake と Replay だけ（$0）
→ Phase 3A：L1（Fake を使った接続と、安全の検証。$0）→ 3A の Technical Gate と Safety Gate を通る
→ オーナーが、実API を使う Run、見積もり、最大の費用、月額の上限を承認する
→ Phase 3B の範囲でだけ、L2〜L4 を行う
→ 接続の試験と、費用の照合に合格する
→ Phase 3C へ
```

- 最初の実モデルの評価は、Anthropic の Claude API で行う（D2）。

### 31.3 オーナーの承認のための文案（`OWNER_DECISIONS.md` は変えていない）

**D5（追加の案）**

> D5：Phase 2 の後に実API を使う場合は、見積額を添えた実行（Run）ごとの承認と、月額の上限を必要とする。実験の全体の承認だけで、範囲の外の Run を自動では承認しない。

**I10 の書き換えの案（設計A。オーナーの承認の後に反映する）**

> I10：Phase 3A の Technical Gate と Safety Gate を通り、オーナーが D5 の承認を与えるまで、LiveProvider について L2（ビルドへの組み込み）、L3（認証情報の注入）、L4（通信の経路の有効化）を行わない。L1（実装と、偽の通信の経路での試験）は、Phase 3A で行ってよい。

## 32. E4 の不変条件（`E.Z` の続き）

| ID | 不変条件 |
|---|---|
| E.Z26 | 削除を受け付けた対象について、鍵の更新と派生データの生成をしない。更新の前と後の blob と鍵の両方を、削除の対象にする |
| E.Z27 | 実API の Run は、始める前に、独立した記録に予算の枠を押さえる。holdout の使用と、集計の公開も、事前に枠を押さえる。結果が分からなくても、枠を戻さない |
| E.Z28 | 最新性は、合計の額ではなく、単調に増える `seq` と記録の連鎖で確かめる |
| E.Z29 | `REVOCATION_ENFORCED` にしてよいのは、ack、停止の確認、送信の経路の遮断のどれかを確かめたときだけ。Lease の期限が切れただけでは足りない |
| E.Z30 | 1つの `run_plan_id` から作れる Run は1つだけ。承認は、環境、サイト、`approval_epoch` に結び付く |
| E.Z31 | DNS、接続、TLS、ヘッダー、本文は、どれも認可の後に、送信の関門の中で始める |
| E.Z32 | `READ_ONLY` という分類だけで、結果が分からない操作を自動で再試行しない |
| E.Z33 | 反映の前の検証、反映、反映の後の確認、最終の判定の順を守る。Phase 1 では、管理された作業ツリーだけに反映する |
| E.Z34 | 削除については、台帳を Journal より優先する |
| E.Z35 | LiveProvider の L2〜L4 は、3A のゲートと、オーナーの承認の後にだけ行う |
| E.Z36 | 復元の試験に合格していない世代について、「復元できる」と言わない |

## 33. 最終の整合の確認の結果

| 確認したもの | 結果 |
|---|---|
| 12 の論理 Engine | 第1部の1.1 のとおり。各 Engine は、ROADMAP の Phase に割り当てた |
| 共通の接続口 | 第1部の8章の 21 の型。E4 で、RunPlanBinding と、証人の記録の種類（`spend_allocation`、`holdout_slot` など）を加えた |
| 不変条件 | 接頭辞を付けて呼ぶ。`A.I1〜I18`、`B1.C1〜C26`、`B2.M1〜M28`、`C1.S1〜S27`、`C2.X1〜X28`、`D.V1〜V21`、`E.Z1〜Z36`。**資料の間で食い違う箇所は、28章の正本の決まりで解く** |
| P-1〜P-5、CP-1〜CP-17、すべての残存課題 | `RESOLUTION_MATRIX.md` の8章（最終の一覧） |
| 初回報告書との全文の照合 | **行っていない**（A-P6。未解決） |
