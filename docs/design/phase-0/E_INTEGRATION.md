# 設計E1：全体の整合と、Engine の責務

> 文書の状態：**DRAFT**（設計E の E1。独立審査を待っている）　作成日：2026-11-26
>
> 元にした資料（`bb1097e` の時点）
>
> | 資料 | 状態 |
> |---|---|
> | A_RUNTIME | 設計段階で合格 |
> | B1_CONTEXT | 条件付き合格 |
> | B2_MEMORY | 条件付き合格 |
> | C1_SAFETY | 条件付き合格 |
> | C2_DATA_SECURITY | 条件付き合格 |
> | D_EVALUATION_COST | 第2版は 95/100 の条件付き合格（審査の結果の詳細は未受領） |
> | OWNER_DECISIONS、STATUS、PHASE_0_REVIEW | — |
>
> 読み込みの方法
> - 各資料の見出し、残存課題の表、変更案の表、不変条件の一覧、各資料の間の接続に関わる節を、抜き出して照合した。
> - 資料の全文を、1行ずつ読み直したわけではない（大量の出力を避けるため）。
>
> 届いていないもの
> - 初回 Phase 0 報告書の全文。
> - 以前に出された「設計E の詳細な指示」。このセッションには届いていない。そのため、次のものは**E1 での暫定の案**である。
>   - E2 以降の分け方（`RESOLUTION_MATRIX.md` の0章）
>   - 12 の Engine の一覧（5章）
>   - 初回報告書が定める Engine の一覧との照合が必要である。
>
> 方針（オーナーの最新の方針）
> - 自分専用の AI の基盤として、性能・品質・自律性・費用の効率を最も優先する。
> - 商用化は、必須の目標から外す。
> - Service Harness は、本体が完成した後の、独立したサブ機能にする。
> - 過剰な設計を避ける。ただし、安全のために必須の機能は省かない。

## 1. この文書の役割

- 設計A〜D の間の矛盾と依存を整理する。
- 12 の Engine の責務、共通の接続口、状態の正本を決める。
- 個別の課題の一覧は `RESOLUTION_MATRIX.md` に置く。この文書には、統一の**案**を書く。
- 確定させるのは E2 以降である。各資料の本文は、まだ変えていない。

## 2. 資料の間の依存関係

```
OWNER_DECISIONS（D1〜D4）
   │
   ▼
A（Runtime：Journal、Step、Permit、復旧、費用の基本）
   ├──▶ B1（Context：context_hash、Rebuild と Replay、送信の認可の入口）
   │        └──▶ B2（Memory：記憶の照会、削除、系譜）
   ├──▶ C1（権限、PEP、Sandbox、Gateway、停止）
   │        └──▶ C2（データの分類、SAG、鍵、削除台帳、監査、評価環境）
   └──▶ D（評価、統計、Cost Engine、予算）
             ▲  評価環境と holdout は C2 に、費用の状態は A に依存する
```

**実装を始める前に、それぞれ必要になるもの**

| 段階 | 必要になるもの |
|---|---|
| Phase 1 の Runtime の実装 | 処理の順序の統一（4章）、ID と seq（5.3）、Rebuild の用語（X-2） |
| Phase 1 の PEP と Sandbox | C1-P03、C1-P05、A-P2（対応する OS） |
| Phase 3A | C1-P04、C2 の5.5、外部の仕様の確認（EXT） |
| Phase 3B | E-OWN-1（LiveProvider と D1） |
| Phase 3C | 評価の側の環境（C2 の10章）、D の候補の値の承認 |
| Memory v1 | C2-P03〜P05、B2-P01、A-P1 |

## 3. 設計の間の矛盾（E1 で見つけたもの）

### 3.1 一覧

| # | 矛盾 | 関係する資料 | 統一の案 | 担当 |
|---|---|---|---|---|
| X-1 | **処理の順序。** 4つの資料で、順序が違う。<br>- A の3.1：Safety → 承認 → 予約 → Intent → Permit<br>- B1 の6.1：予約が Safety より先<br>- C1 の6.2：11の段階<br>- C2 の4.3：保存の認可 → 送信の認可 → 保存 → 消費 | A、B1、C1、C2、D | 4章の統一の順序 | E2 |
| X-2 | **Rebuild の意味。** A の2.2 は「Journal から状態を作り直す」で、Materialized State を書き戻すことが読み取れる。一方、P-5 は「Rebuild はメモリの中での計算だけ」 | A、B1、B2 | Rebuild は「メモリの中での fold」に限る。Materialized State と Memory の投影を書き戻すのは ProjectionRepair とする。A の2.2 の書き方を直す | E2 |
| X-3 | **Worker の定義。** A の Worker と、C1 の Worker-Control・Worker-Exec | A、C1 | CP-3 のとおり | E2 |
| X-4 | **Permit の項目と、MAC の検証の場所。** A の5.1 は項目が少ない。C1 の7.4 は PEP で MAC を検証する。C2 の6.4 は P1 だけが検証する | A、C1、C2 | 項目は、CP-4・CP-8 を合わせたものにする。検証は CP-9（P1 の消費の中） | E2 |
| X-5 | **receipt と送信開始。** C1 の7.5 の receipt、C2 の5.4 の状態遷移、D の10.2 の `ACTIVE` の関係。`SEND_STARTED` の定義（C2-P01）。読みのロックの不足（C2-P02） | C1、C2、D | 4章の⑪⑫ | E2 |
| X-6 | **ハッシュの鍵。** B1 の6.2 は「テナントの鍵で HMAC」。B2 の P-2 は「記憶ごとの MAC 鍵」。C2 の6.2 は rendering と request で鍵を分ける | B1、B2、C2 | 項目の種類ごとに鍵を決める（P-2、CP-12）。鍵の一覧は C2 の6.2 を正本にする | E2 |
| X-7 | **削除の状態。** B2 の7.5 と C2 の7.1 で、状態の名前と数が違う | B2、C2 | CP-11 | E3 |
| X-8 | **費用の状態。** A の4.1 は5項目、D の10.2 は8つの状態。A の I5 の式と、D の `hold` の考え方 | A、D | CP-14。A の I5 を「Σ hold ≤ 上限」に言い換える | E2 |
| X-9 | **比べる構成の名前。** B1 の11章（基準 F・B）、B2 の11章、D の4.1 | B1、B2、D | CP-13 | E2 |
| X-10 | **seq の単位。** A は Run 単位、B2 はテナント単位（B2-P05） | A、B2、C2 | 5.3 の ID と seq の方式 | E2 |
| X-11 | **LiveProvider と Phase 3B。** A の I10 は「3B のゲートを通るまで、LiveProvider に到達する経路がない」。しかし、3B の作業そのものに LiveProvider が要る。<br>D1（Phase 2 の終わりまで $0）と、A05 の「3A も $0」とは食い違わない。循環は、I10 の書き方にある | A、D、OWNER_DECISIONS | 案：I10 を、次のように言い換える。<br>「3A の Technical Gate と Safety Gate の合格、**かつ**有効な SpendApproval がそろうまで、LiveProvider をビルドに含めない。認証情報も注入しない」<br>3B は、その条件で、承認の範囲の中だけで有効にする。**オーナーの承認が必要** | E4 |
| X-12 | **holdout の使用の回数。** C2 の11.1（評価の実行ごと）と、D の6.2（評価セットの版ごとの累積） | C2、D | CP-16 | E2 |
| X-13 | **マルチテナントの優先度。** C1・C2・D は「マルチテナント化の前に必要」とした機能を持つ。一方、オーナーの最新の方針は「必須の目標ではない」 | C1、C2、D、STATUS | 7章のとおり、優先度を下げる。テナントの ID の項目は残す | E4 |
| X-14 | **長時間の稼働。** D の11.2 の「Run の上限は 2 時間」と、オーナーの方針（長時間の稼働を重視） | D、STATUS | 長時間用の Run のプロファイル（区切りの点ごとに、予算の承認を継ぎ足す）を、E2 で設計する | E2 |
| X-15 | **不変条件の ID の衝突**（E-DOC-2） | 全体 | 接頭辞を付けて呼ぶ | E2 |
| X-16 | **`AGENTS.md` の進捗の表が古い**（CP-6） | AGENTS、STATUS | 進捗の正本を `STATUS.md` にする | E4 |

### 3.2 矛盾していないことを確かめた点

- D1（Phase 2 の終わりまで $0）と、Phase 3A を $0 とする A05、D の14章は、互いに一致している。
- Journal と予算の台帳が、同じ `journal.db` の、同じトランザクションの中にあること。A の2.1、C2 の7.1 の D1、D の11.1 で一致している。
- 記憶の本文を Journal に入れないこと。B2 の M8 と C2 の X4 で一致している。
- 安全のために必須の機能を、比較のために外さないこと。D の4.1 と C1 で一致している。

## 4. 処理の順序の統一の案（X-1、X-4、X-5 を解決する候補。E2 で確定させる）

```
① 提案            モデルが、構造化されたツール呼び出しを出す（権限なし）
② 検証            スキーマ、マニフェストと実行物の固定、RunGrant にあるか
③ 内容を確定      Context を組み立て（B1）、最終的なリクエストと request_hash を作る
                  （送信の場合。C2 の5.6）
④ PDP の判定      ポリシー、範囲、Safety の規則、EgressCheck（C2 の5.2）
⑤ 保存の認可      リクエストの記録の保存について、SAG で認可する（C2 の4.3。B1-P01、CP-2）
⑥ 承認            必要な場合だけ。action_hash と request_hash に結び付ける（C1 の7.6）
⑦ 予算の予約      階層の全体と、SpendApproval を確かめる（D の11章。費用の台帳は RESERVED）
⑧ Intent の記録   暗号化したリクエストの記録を保存し、Intent を Journal に記録する（A）
⑨ Permit の発行   PDP がもう一度判定する。epoch、拒否リスト、承認、停止を確かめる
⑩ PEP の事前検査  MAC を使わない項目だけを確かめる（CP-9）
⑪ 消費            P1 の1つのトランザクションで、次をすべて確かめる（C1 の7.5、CP-7・CP-9）。
                  MAC、承認の使用回数、予約、Intent、owner_epoch、停止、拒否リスト、ハッシュの一致
                  → receipt を ISSUED にする（pep_boot_id、revocation_gen、短い期限。CP-10）
⑫ 自分のものにして送る（C2-P01・P02 の修正案）
                  PEP の中で、receipt を比較して交換する操作（CAS）で ISSUED → CLAIMED にする（1回だけ成功する）
                  → その時点の revocation_gen、boot_id、承認の期限、停止の状態を確かめる
                  → SEND_STARTED を記録してから、ネットワークへの書き込みを始める
                  （SEND_STARTED は「書き込みを始めてよいと確定した状態」と定義する。
                    最初のバイトが届いたことは意味しない）
                  → 費用の台帳を ACTIVE にする（CP-17）
⑬ 結果            費用の台帳は USAGE_REPORTED か UNKNOWN（D の10.2）
⑭ 結果の検査と記録  大きさ、スキーマ、機密の検査、SAG → COMMITTED → あとで照合
```

**失効との順序**

- 失効の処理は、PEP の中で、⑫の CAS と同じロックで順序付ける。
- `SEND_STARTED` の後に失効が届いた場合、その送信は「失効の前の送信」として照合の対象になる。
- ネットワークへの書き込みは、完全には原子的に扱えない。`SEND_STARTED` と、相手に実際に届いた量の間には、ずれが残る（限界）。

**設計A の Step の状態遷移との対応**

| A の Step の状態 | 統一の順序 |
|---|---|
| `SAFETY_CHECKED` | ④⑤ |
| `APPROVED` | ⑥ |
| `BUDGET_RESERVED` | ⑦ |
| `INTENT_RECORDED` | ⑧ |
| `PERMIT_ISSUED` | ⑨ |
| `DISPATCHED` | ⑫ |

## 5. 12 の Engine（暫定の一覧。初回報告書との照合が必要）

### 5.1 一覧と責務

| # | Engine | 責務 | 主な出典 | 持っている設計の状態 |
|---|---|---|---|---|
| E01 | Runtime Engine | Run と Step の状態機械、Journal への唯一の書き込み、Lease と epoch、Permit の発行と消費の処理、RunGrant の保持 | A | 設計済み |
| E02 | Context Engine | 文脈の組み立て、圧縮、ProtectedFact の表示、`context_hash` | B1 | 設計済み |
| E03 | Memory Engine | 記憶の候補、検証、検索、品質、系譜 | B2 | 設計済み |
| E04 | Planning Engine | 計画の作成、計画の立て直し、タスクの分割（B1 の4.3 の計画の立て直しを受け持つ） | D の9.6 で指標だけ | **未設計**（E-GAP-1） |
| E05 | Tool Engine | ツールの選択、Tool Manifest と実行物の登録簿、Adapter | C1 の5章、D の9.6 | 登録簿は設計済み。選択は未設計 |
| E06 | Verification Engine | 完了の前の検証（A の6章の `VERIFYING`）、成果物の検査、自分の作業の確認 | A の6章、D の9.6 | 一部だけ |
| E07 | Recovery Engine | 復旧、照合、`RECONCILIATION_REQUIRED`、残ったものの回収（C1 の8.7）、取り消し | A、C1 | 設計済み（E01 と同じプロセスの中の、論理的な部品） |
| E08 | Safety Engine（PDP） | 判定、Policy Store、承認、AuthorizedScope、停止の状態 | C1 | 設計済み |
| E09 | Execution Engine（PEP） | Broker、Sandbox、Gateway、Provider Adapter（送信の側）、receipt | C1、C2 の5章 | 設計済み |
| E10 | Data Security Engine | 分類、SAG、鍵、削除の調整、バックアップ、監査 | C2 | 設計済み |
| E11 | Cost Engine | 見積もり、予約、費用の台帳、照合、SpendApproval | A の4章、D の10〜12章 | 設計済み |
| E12 | Evaluation Engine | 実験の登録、実行、採点、統計、判定（評価の側の構成も含む） | D、C2 の10章 | 設計済み |

- Model Provider Adapter は、2つの Engine で使う、共通の部品として扱う。
  - 文脈を変換するところ：E02 が使う。
  - 送信するところ：E09 の中にある。
- **自分専用の基盤という方針に照らすと、** E04〜E06 は性能に直接効く部分である。E2 で、最小限の設計を加える必要がある。

### 5.2 状態の正本

| 状態 | 正本 | 写しや投影 | 書き込む主体 |
|---|---|---|---|
| Run と Step の状態、ProtectedFact、承認、Permit、receipt の状態、`access_epoch`、拒否リスト | Journal（`journal.db`） | Materialized State（キャッシュ） | E01（P1） |
| 予算の `hold` と、費用のエントリー | Journal と同じ DB の台帳 | 集計の表 | E11 → E01（P1） |
| 停止の状態 | Journal | 停止の状態のファイル（署名付き） | E08 → E01 |
| 削除の要求 | Journal（受付）と、削除台帳（独立の写し） | 外部の証人（`seq` とハッシュ） | E10 → E01 |
| 記憶の状態 | Journal のコマンド | `memory/<tenant>.db`、区画の索引 | E03 → E01（ProjectionRepair） |
| 記憶と成果物の本文 | 暗号化した blob | — | E10 の SAG を通して、E01 |
| 鍵 | 鍵の DB（KEK でラップ） | 鍵のバックアップ（保管の期間は短い） | E10 |
| 認証情報 | Gateway の認証情報の保管庫 | なし | オーナー → E09（P3） |
| ポリシー、マニフェスト、実行物の登録 | Policy Store（版付き） | Journal にはハッシュだけ | オーナー → E08 |
| 監査ログ | 監査ログ（別の追記の経路） | 署名付きのチェックポイントの写し | E10（Audit Writer） |
| 実験の仕様と結果（dev・validation） | Experiment Registry と Results Store（開発の側） | — | E12 |
| holdout と、その評価の記録 | 評価の側の保存先と、holdout のリポジトリ | 制限した報告 | 評価の側の E12、オーナー |
| 進捗 | `docs/STATUS.md`（CP-6） | `AGENTS.md` には参照だけ | — |

### 5.3 共通の接続口（Engine の契約）

**1. 判断の部分は、副作用を持たない**

- Engine の判断の部分は、`decide(snapshot) → Decision | Request[]` の形にする。
- 副作用（モデル、通信、保存、実行）は、Request として E01 に返し、E01 が④〜⑭の経路で実行する。
- これは B1 の1.1 と B2 の1章の決まりを、すべての Engine に広げたものである。

**2. 要求の共通の形（EngineRequest）**

`{request_id, kind, engine@version, run_id?, step_id?, attempt_id?, tenant_id, payload_ref, labels, estimated_cost?, policy_version, access_epoch}`

**3. イベントの共通の見出し（Journal）**

`{event_id, type, schema_version, tenant_id, run_id?, run_seq?, tenant_seq, engine@version, policy_version, access_epoch, owner_epoch, labels, refs, keyed_hashes, prev_hash}`

**4. ID と seq の方式（B2-P05、X-10 の案）**

- ID は全体で一意にする。種類を表す接頭辞を付けた、時刻の順に並ぶ ID を使う（例：`run_…`、`stp_…`、`att_…`、`mem_…`、`del_…`）。こうすれば、異なる Run からの記憶のイベントでも、ID がぶつからない。
- `run_seq` は Run の中の順序（A の I1）を表す。
- `tenant_seq` は、テナントの単位の、単調に増える番号とする。P1 が1つの書き込み手なので、1つのトランザクションの中で両方を付けられる。Memory Store、索引、削除台帳は、`tenant_seq` で `last_applied` を管理する。

**5. エラーの区分（共通）**

`RETRYABLE`、`FATAL`、`UNKNOWN_OUTCOME`、`BLOCKED`（Safety）、`BUDGET`、`STOPPED`、`UNAVAILABLE`（隔離など）

**6. バージョンの記録**

- すべての出力に、`engine@version` と `schema_version` を付ける（A の I11）。

**7. Rebuild と Replay の契約**

- Rebuild は、Journal をメモリの中で fold するだけ。書き込まない。
- Replay は、記録したデータだけを使う。今の状態は参照しない。
- ProjectionRepair は、保守のための別のモード。

**8. ラベルの継承**

- 出力には、入力のラベルの和集合を付ける（C2 の3.2）。

## 6. 安全のために必須の機能と、外さないもの（共通の土台）

次は、どの Engine の構成でも、どの Phase でも外さない（D の4.1、C1、C2）。

- PDP と PEP、Permit とその原子的な消費
- Sandbox（自己診断に失敗したら、実行を拒否する）、Gateway
- SAG（認証情報を保存しない）、予算の強制（階層、SpendApproval）、緊急停止
- B1 の権限の階層
- ProtectedFact の安全上の制約

## 7. オーナーの最新の方針に合わせた、過剰な設計の見直し（案。確定は E4）

| 機能 | これまでの位置付け | 見直しの案 |
|---|---|---|
| テナントごとの鍵、テナントの一時停止（K2）、テナントの間の隔離の試験 | マルチテナント化の前に必要 | 優先度を下げる。`tenant_id` の項目と、範囲の判定の形は残す（テナントは1つに固定） |
| 組織の役割の追加、SSO、KMS、HSM | 将来 | 将来のまま |
| MCP の登録簿と、MCP の実行時の信頼境界 | MCP を導入する前 | MCP を使うと決めるまで、実装しない（設計は残す） |
| 中央の予算台帳 | 将来 | 将来のまま |
| Baseline C | 参考の値 | 任意。行わなくてもよい |
| 評価の側の環境の分離（holdout） | 実API で評価する前に必須 | **維持する**（性能を正しく測るために必要） |
| User Memory の削除と、派生データの削除 | Memory v1 の前 | **維持する**（自分のデータでも、誤った記憶や機密を消す必要がある） |
| スマートフォンからの遠隔操作、長時間の稼働 | 設計がない | **優先度を上げる**（E-GAP-2）。遠隔の承認と停止の経路の認証、通知、長時間用の Run のプロファイル |
| Service Harness（LINE、X、YouTube、Instagram、Facebook など） | — | 本体が完成した後の、独立したサブ機能。本体の完成の条件に含めない。外への通信は、C1 の Gateway と承認の上に作る、という前提だけを残す |

## 8. E2 以降へ渡すもの（E2 には進んでいない）

1. **E2：** 4章の統一の順序を確定し、関係する資料（A、B1、C1、C2、D）に反映する。あわせて、次も行う。
   - X-2（Rebuild の用語）、5.3（ID と seq の方式）、CP-1〜CP-5、CP-7〜CP-10、CP-12〜CP-17、P-1〜P-5 の文言の統一。
   - E04〜E06 の最小限の設計（E-GAP-1）。
   - 長時間用の Run のプロファイル（X-14）。
2. **E3：** 削除、バックアップ、鍵、復元（A-P1、B2-P01、C2-P03〜P05、CP-11）。
3. **E4：** ロードマップ、Phase とゲート、X-11（オーナーの承認）、7章の見直しの確定、対応する OS の範囲、遠隔操作、`AGENTS.md` の整理。
4. **EXT：** 外部の仕様の確認の一覧（`RESOLUTION_MATRIX.md` の4章）。

## 9. 残存リスクと、未確認事項

1. **12 の Engine の一覧：** 暫定のものである。初回報告書の一覧と食い違う可能性がある。
2. **E2 以降の分け方：** 以前に出された E の詳細な指示と、合っていない可能性がある。
3. **読み込みの範囲：** 資料の全文を、1行ずつ読み直したわけではない。抜き出しの照合で見落とした矛盾が、残っている可能性がある。
4. **設計D 第2版の残存課題：** 受け取っていないので、この一覧に入っていない。
5. **外部の仕様：** `RESOLUTION_MATRIX.md` の4章のとおり、どれも確認していない。
