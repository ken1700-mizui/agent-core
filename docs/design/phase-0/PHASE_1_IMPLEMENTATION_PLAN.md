# Phase 1 の実装計画

> 文書の状態：**DRAFT**（E4 で作成し、Phase 0 の最終修正で直した。Codex の監査（CONDITIONAL_PASS）の指摘 AUDIT-01〜04 に対応した）　作成日：2026-11-26
>
> - **Phase 1 の開始は、2026-11-26 にオーナーが承認した**（`docs/decisions/PHASE_1_APPROVAL.md`）。実装は、指示が認めた WP だけを、`impl/phase-1-foundation` で行う。
> - Phase 1 で、すべての Engine を完成させることはしない。ただし、Phase 1 で使う機能に必要な安全の条件は、省かない。
> - 実API、外部への通信、認証情報、Sandbox での任意のコマンドの実行は、Phase 1 では扱わない（ROADMAP の0.1）。

## 0. 始める前に必要なもの（オーナーの承認）

### 0.1 技術の選択（Phase 1 の部分は ACCEPTED。Docker、FastAPI、Next.js は PROPOSED）

審査者が確かめた、初回報告書の推奨（第6・9・13・14・16・17章）を、承認の候補とする。**版の指定と、互いに合うかどうかは、確かめていない。**

| 項目 | 候補 | 使う時期 |
|---|---|---|
| 言語 | Python 3.12 以上 | Phase 1 |
| パッケージとツールの管理 | uv | Phase 1 |
| 型とデータの検証 | Pydantic v2 | Phase 1 |
| 保存 | sqlite3（WAL。将来、FTS5） | Phase 1（FTS5 は Phase 5） |
| 試験 | pytest | Phase 1 |
| 静的な検査 | ruff、mypy | Phase 1 |
| 層の依存の方向の強制 | import-linter | Phase 1 |
| Sandbox | Docker | Phase 2 |
| モデルへの接続 | 将来の、公式の SDK と HTTP の Adapter | Phase 3A（L1） |
| 管理画面 | FastAPI と Next.js | Phase 8 |

- 依存関係を加えるときは、ADR を書き（`docs/adr/0006-toolchain.md`）、オーナーの承認を得る（AGENTS.md）。

### 0.2 そのほかの承認の事項

| 項目 | 内容 |
|---|---|
| 対応する OS | **案 1 を承認**（Linux を優先し、Windows は後から） |
| CI | **GitHub Actions を承認**（無料の枠の中だけ。無料の条件は確かめていない）。依存関係を取得した後の検査では、ネットワークも秘密も使わない |

**対応する OS の比較**

| 案 | 内容 | 良い点 | 問題 |
|---|---|---|---|
| **1（推奨）** | Phase 1〜3 は Linux だけ。Windows は、WSL2 で確かめた後（Phase 4 以降の候補） | 排他のロック、安全なファイル操作の手段、プロセスの分離、Docker の前提を、1つに絞れる | Windows で直接は使えない |
| 2 | 最初から Linux と Windows の両方 | 使える場所が広い | 未確認の点が多い |

## 1. 作業の単位（WP）と、安全な順序

### 1.1 順序

```
WP-01 → WP-02 → WP-03 → WP-04 → { WP-05, WP-07, WP-11 } → WP-06 → WP-09 → WP-08 → WP-14 → WP-15 → WP-10 → WP-12 → WP-13
```

- **認可を通らないファイル操作を、使える状態にしない（F07）。**
  - WP-08（ファイル操作）は、WP-09（Safety・Permit）の後に置く。
  - 単体の試験のために、ファイル操作を先に作る場合も、次の2つで守る。
    - 操作の入口は、消費済みの Permit の型を引数として必ず受け取る形にする。
    - Execution のモジュール以外からは import できないようにする。Agent と CLI からの経路は、WP-09 の後にだけつなぐ。この決まりは、import-linter の契約で強制する。

### 1.2 一覧

| WP | 名前 | 依存する設計 | 対象のモジュール | 完了の条件 | 守る不変条件 | 試験 | 実装しない範囲 |
|---|---|---|---|---|---|---|---|
| WP-01 | プロジェクトの構成と CI | E の1章、ADR-0001・0006 | リポジトリの構成（Engine ごとのモジュール）、import-linter の層の契約、秘密情報の検査 | ビルド、試験、静的な検査、秘密情報の検査が CI で通る。ネットワークも秘密も使わない | — | CI | 配布 |
| WP-02 | 共通の型とポート | E の8章、2.2 | 共通の型、ID の生成 | Phase 1 で使う型と、ID の生成と重複の検査 | E.Z2 | 型の試験、重複の注入 | 使わない型の詳細 |
| WP-03 | SQLite の Journal | A の2章、E の2.3・15章、ADR-0002 | Runtime | 追記、`run_seq` と `tenant_seq`、ハッシュの連鎖、UNIQUE の制約、書き込みの排他 | A.I1・I3・I6、E.Z3 | 欠番と重複の注入、2つ目のプロセスの拒否 | 複製、証人、バックアップ |
| WP-04 | 状態機械 | A の3・6章、E の3.1・9章 | Runtime | Task、Run、Step、Attempt の状態遷移 | A.I7・I16 | 状態遷移の性質の試験 | 長時間の Task の全体の機能 |
| WP-05 | Fake Provider | E の7.4、ADR-0003 | Model Gateway | 共通の型、決まった応答、Usage（XTU） | B1.C16 | 決定的であることの試験 | 実際のプロバイダー |
| WP-06 | Replay | B1 の6.3 | Model Gateway | 人工的な記録、3つのハッシュの検証 | B1.C10・C24、E.Z14 | 改ざんと欠損、`REPLAY_UNAVAILABLE` | 実API の記録 |
| WP-07 | Tool Registry | C1 の5章 | Tool | マニフェストと実行物の固定、`builtin` だけ、選択は提案だけ | C1.S8・S23、E.Z15 | 登録していないツールの拒否 | MCP、Sandbox のコマンド |
| WP-09 | Safety・Permit・予算の最小の実装 | C1 の4・6・7章、E の3〜5章、D の11章、ADR-0004 | Safety、Cost | 静的なポリシー、RunGrant、デフォルトの拒否、ExactApproval、Permit の原子的な消費、プロセスの中の Receipt、XTU の台帳、金銭以外の上限、停止の状態。**予約の ID の確保（`ID_ALLOCATED`）と、承認の後の有効化（`RESERVED`）を分け、Permit の発行と消費のときに、承認・ActionDescriptor・予約の一致を確かめる**（E の38章） | C1.S1〜S5・S20、D.V10・V11、E.Z6・Z9・Z42・Z43 | 同時の消費、承認の改ざん、予算の超過、停止との競合。**AUDIT-01 の試験：** ID の確保だけで実行できないこと、予約の失敗で実行しないこと、内容・費用の上限・Attempt を変えると承認が無効になること、古い ID の再使用と二重の予約の拒否 | 外部への通信、ScopedApproval、SpendApproval |
| WP-08 | 基本のファイル操作 | C1 の8.3・8.5、E の29.5 | Runtime の Execution | **管理された作業ツリーの中だけ**の読み書き。Permit を必須にする。パスの検査。安全な手段がなければ拒否する | C1.S16・S19、E.Z33 | パスの悪用、リンク、Permit のない呼び出しの拒否 | 外部の作業領域への直接の反映 |
| WP-14 | 最小の Context の組み立て | B1 の3・4章、ROADMAP の0.1 | Context | 階層、ProtectedFact、決まった並べ方、予算の推定、`context_hash` | B1.C1〜C3・C6・C7 | 階層の格上げの拒否、予算の超過で止まること | 圧縮、取り直し（Phase 2） |
| WP-15 | エージェントのループと、完了の検証 | E の7.1（`simple`）・7.3・29.5 | Runtime、Planning（`simple`）、Verification | Fake で、提案 → 認可 → 実行 → 検証 → 上限付きの修正のループ → 完了の判定 | E.Z15・Z33 | 完了の宣言だけでは完了にならないこと、修正の回数の上限 | `complex` の計画（Phase 2） |
| WP-10 | CLI | E の7章 | 入口 | タスクの開始、状態、承認、停止 | C1.S2・S17 | 承認の表示に機密が出ないこと | 遠隔操作 |
| WP-11 | 構造化ログ | E の7.7、C2 の4.5 | Observability & Recovery | ID、状態、時間だけ。本文を入れない | C2.X4・X25 | 目印の値の検査 | 外部への送信 |
| WP-12 | 障害からの復旧 | A の7章、E の2.4・16章 | Runtime、Observability & Recovery | Rebuild（メモリの中だけ）、ProjectionRepair、`OUTCOME_UNKNOWN`、自動で再実行しない | A.I9、E.Z17・Z32 | 障害の注入 | バックアップと復元（Phase 4） |
| WP-13 | 統合試験 | 全体 | — | Fake で、タスクを最後まで動かす | 上のすべて | 障害の注入を含む、通しの試験 | 性能の評価 |

## 2. 合成のデータ専用であることを、実行するときに強制する（F07）

Phase 1 では、次のすべてを、実行するときの制限として強制する（注意書きだけにしない）。

1. **起動の条件：**
   - 起動するときの構成の `profile` が `phase1_synthetic` でなければ、Runtime は起動を拒否する。
   - そのプロファイルでは、LiveProvider と Gateway は存在しない。
2. **作業の場所：**
   - ファイル操作の対象は、Agent Core が自分で作った合成の作業ツリーだけにする。
   - 作業ツリーには、作ったときの目録（署名付き）を置き、それがないディレクトリへの操作は拒否する。
3. **タスク：**
   - PDP は、`synthetic: true` の印がある、合成の Task Catalog のタスクだけを許す。
4. **保存：**
   - SAG は、`synthetic` のラベルがない書き込みを、すべて拒否する。
5. **ネットワーク：**
   - Phase 1 の Runtime には、外部へ通信するコードの経路がない（import-linter の契約）。
   - OS のレベルの遮断は、Phase 3A で加える。

**試験用の機能と、実データ・実API 用の機能を分ける**

| 区分 | Phase 1 での扱い |
|---|---|
| 試験用の機能（Fake の保存、XTU の価格表、Fake の認可） | 使う。`phase1_synthetic` の構成でだけ動く |
| 実データ・実API 用の機能（実際の暗号化、鍵の管理、SAG の機密の検出、外部への送信、SpendApproval） | **実装しない** |

## 3. Phase 1 の完了の条件

- WP-01〜WP-15 が、すべて完了の条件を満たす。
- 最小の Context の組み立てと、完了の検証を含む、Fake による実行のループが動く（ROADMAP の0.1）。
- 外部への通信が 0 件。認証情報が、どこにも存在しない。
- 2章の制限が、すべて試験で確かめられている。
- Fake を使った結果を、性能が上がった証拠とはしない。

## 4. 確かめる必要のある外部の仕様（それぞれの Phase を始める前）

| 項目 | 必要になる Phase |
|---|---|
| 0.1 の技術の候補の版と、互いに合うかどうか | Phase 1 |
| SQLite の WAL、排他、`BEGIN IMMEDIATE` の挙動 | Phase 1 |
| `openat2` など、安全なファイル操作の手段 | Phase 1（使えなければ、ファイル操作の範囲を狭める） |
| OS の排他のロック | Phase 1 |
| rootless の Docker と、自己診断の項目 | Phase 2（信頼しないコードを実行する前） |
| 単調な時計、Process Supervisor、ファイアウォール、ネットワークのライブラリが通信を始める呼び出し（E の35.5） | Phase 3A |
| Anthropic の API の仕様、条件付きの追記ができる独立した台帳（E の34.3） | Phase 3A〜3B |
| 暗号のライブラリ | 実データを保存する前（3A）、または Phase 5 |
