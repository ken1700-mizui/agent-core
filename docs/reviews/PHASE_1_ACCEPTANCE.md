# Phase 1 最終受入の準備

> 文書の状態：**DRAFT**（オーナーの判断と承認を待つ。正式な完了の宣言ではない）　作成：2026-11-27　更新：2026-11-27（FA-01 の修正）
>
> - 対象：`impl/phase-1-foundation`。RR-01 の補強は `a54d52b`、CI の固定は `89f8584`、この文書の初版は `e724b2a`。この版は FA-01（MEDIUM、文書の指摘）の修正で、製品のコードは変えていない。
> - **Phase の範囲は、承認済みの `docs/design/phase-0/ROADMAP.md` を正本とする。** この文書の初版は、Sandbox と子プロセスを Phase 3A としていた（FA-01）。ROADMAP では Phase 2 の範囲であり、この版で直した（6章）。
> - **Phase 1 の状態：最終受入の準備中。** 8章の条件がそろう前には、正式な完了としない。Phase 2 には進まない。

## 1. Codex の独立の検証の記録

### 1.1 独立品質再レビュー（対象 `20fa05813aca71266d6c862c534d4bfee6f64677`）

| 項目 | 内容 |
|---|---|
| 判定 | **CONDITIONAL_PASS**（新しい HIGH・MEDIUM 以上の指摘なし） |
| QR-01〜QR-04 | 主要な指摘はすべて解消（オーナーからの伝達） |
| 残った指摘 | RR-01（LOW）：巻き戻しの試験で `result_digest` がなく、`operation_results` への書き込みが起きていなかった（2章） |
| 独立して成功した検査 | 秘密情報の検査、uv の lock の検査。QR-04 の純粋な関数の試験 12 件（pytest を通していない実行） |
| 独立して実行できなかった検査 | 正式な依存環境は構築されなかった。ruff、mypy、import-linter、pytest の全体は未実行 |

- この判定は、主にコードを読んだ静的なレビューによる。全試験の独立した実行の結果ではない。

### 1.2 最終品質検証（対象 `e724b2ade02feac80705dd5e8bbbbf7bd8026bed`）

| 項目 | 内容 |
|---|---|
| 総合判定 | **CONDITIONAL_PASS** |
| RR-01 | 解消を確認 |
| 新しい製品の不具合 | なし |
| 新しい文書の指摘 | FA-01（MEDIUM）：この文書と ROADMAP の Phase の範囲の食い違い（6章で修正） |
| 環境 | Debian 13、Linux x86_64、Python 3.12.14、UID 1000、uv 0.11.32、`uv.lock` のとおりの 21 パッケージ |

| 検査 | Codex での結果 |
|---|---|
| 秘密情報の検査 | 成功 |
| `uv lock --check --offline` | 成功 |
| `uv sync --locked --offline --check` | 成功 |
| ruff check | 成功 |
| ruff format | 成功 |
| mypy | 成功 |
| import-linter | 5つの契約が成功、違反 0 |
| 通常の pytest | **1788 件が成功、130 件が失敗、215 件がエラー**（合計 2133 件。特権の5件を除いた件数と一致する） |
| 特権の試験（5件） | **未実行** |
| 重点の試験（RR-01 を含む照合の試験 9 件と CI の定義の試験 4 件） | 13 件すべて成功（上の通常の pytest の件数と重複する。合計に加えない） |

**失敗とエラーの扱い（環境の問題と製品の不具合の区別）：**

- Codex の環境では、ルートのディレクトリ（`/`）が UID 65534 の所有である。File Broker（`file_broker.py` の `_safe_ancestor`）と CLI のホームの作成は、祖先のディレクトリが root（0）か実行する利用者の所有で、グループと他人が書けない（スティッキー以外）ことを求め、満たさなければ安全な側で拒否する。UID 1000 で実行すると `/` がこの条件に合わず、管理領域とホームを使う試験は、製品の設計どおりに拒否される。
- Codex の分析（環境の条件の不適合）は、この製品の検査の仕様と整合する。**ただし 345 件（130 件と 215 件）の一つずつの原因を、Claude Code は確かめていない。** 環境の問題であるという推定にとどまり、これらの試験は**独立した実行では成功していない（未検証）**として扱う。成功に数えない。
- 製品の安全性の検査は変えない（祖先の所有者の条件を緩めて Codex の環境で通すことはしない）。
- 解決の方法は、条件に合う環境で独立に実行することである（9章の手順。CI の runner、または `/` と作業の場所の祖先が root か実行者の所有である環境）。
- パッケージの数：手元の `uv lock --check` は「23 packages を解決」と表示する。Codex の「21 パッケージ」は導入された数と理解しているが、差の内訳は確かめていない（未確認）。

### 1.3 共通の注意

- 包括的なセキュリティ監査は行われていない。Codex の検証は品質の検証である。
- 6章と 7章の試験の結果は Claude Code がこの環境で実行したもので、独立した実行ではない。

## 2. RR-01 の補強（`a54d52b`）

- 合成の結果の digest（非 None）を持つ実行報告で照合する。注入した障害の中で、同じトランザクションのカーソルで結果の行が書かれたことを確かめてから失敗させる。
- 開き直した後：結果の行がない、Attempt は OUTCOME_UNKNOWN のまま、保留 5,000 を保つ、費用は確定していない。照合し直した後：正しい digest の行が1行だけ。
- 以前の準備（digest なし）では、新しい確認が失敗した（書き込みが起きていなかったことの確認）。製品のコードは変えていない。
- Codex の最終品質検証で、解消を確認された（1.2）。

## 3. CI の公式資料の確認（2026-11-27、読み取りだけ）

GitHub Actions は実行していない。支払いの設定、リポジトリの公開の設定は変えていない。

| 確認事項 | 結果 | 出典 |
|---|---|---|
| `actions/checkout` の SHA | `v6` = `v6.1.0` = `d23441a48e516b6c34aea4fa41551a30e30af803`（軽量タグ）。以前の `v4` = `v4.4.0` = `11d5960a326750d5838078e36cf38b85af677262` | `git ls-remote --tags https://github.com/actions/checkout.git`、その SHA の README と `action.yml` |
| `actions/setup-python` の SHA | `v6` = `v6.3.0` = `ece7cb06caefa5fff74198d8649806c4678c61a1`（軽量タグ）。以前の `v5` = `v5.6.0` = `a26af69be951a213d495a4c3e4e4022e16d87065` | `git ls-remote --tags https://github.com/actions/setup-python.git`、同上 |
| メジャー版の互換性 | v4・v5 は `using: node20`。2026-09-23 から Node 20 は runner で使えない。v6 は両方とも `using: node24`。使う入力（`persist-credentials: false`、`python-version`）は v6 でも同じ | https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions/ 、 https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/ |
| `workflow_dispatch` | 起動するには、ワークフローが既定のブランチにある必要がある | https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow |
| runner の `sudo` | Linux の仮想マシンはパスワードなしの sudo で動く | https://docs.github.com/en/actions/reference/runners/github-hosted-runners |
| Action の固定 | 完全な長さのコミット SHA への固定が推奨されている | https://docs.github.com/en/actions/reference/security/secure-use |
| uv のハッシュ | pip の `--require-hashes` と `--only-binary :all:`。uv 0.11.32 の x86_64 glibc の wheel は `sha256:3da76cd4e2697de30928b8a8524bd39183ac1e08cb7e72833807c022b7cba6c4`（ハッシュ検査付きのダウンロードで一致を確認） | https://pip.pypa.io/en/stable/topics/secure-installs/ 、 https://pypi.org/pypi/uv/0.11.32/json |
| 無料の枠 | 標準の runner は公開リポジトリでは無料。非公開なら GitHub Free の含まれる利用分は月 $12。このリポジトリは公開 | https://docs.github.com/en/billing/concepts/product-billing/github-actions |
| 追加の課金の防止 | 予算の「Stop usage when budget limit is reached」 | https://docs.github.com/en/billing/how-tos/set-up-budgets |

### 3.1 CI の実行の方法の比較

| 方法 | 内容 | 問題 |
|---|---|---|
| A. `main` を変えない | `workflow_dispatch` は既定のブランチに定義が要るので、今の構成では起動できない。代わりは (a) 既定のブランチの一時的な変更、(b) 自動の契機、(c) GitHub Actions を使わない独立した再現 | (a) は既定のブランチの変更で承認が要る。(b) は禁止されている。(c) は CI の実行ではない |
| B. 最小限のワークフローだけを `main` に加える | `workflow_dispatch` だけの `ci.yml` を1つのコミットで `main` に加え、実行のときに実装ブランチを選ぶ | `main` の変更で承認が要る。選んだブランチの定義が使われることは未確認 |

- Claude Code の推奨は B。`main` の変更なのでオーナーが決める。今回は行わない。

### 3.2 CI の実行の前に必要なオーナーの操作

1. 実行の方法の決定（3.1）。
2. リポジトリの公開の設定の確認（公開なら標準の runner は無料）。
3. 予算と「上限で利用を止める」の確認（Claude Code は支払いの設定を変えない）。
4. 初回の実行の許可と、対象のコミットの SHA の指定。
5. 実行の後、ジョブの要約（対象の SHA、runner の環境、Python と uv の版、各検査の件数）を STATUS.md に写す。

## 4. 3つの設計差の承認案（**オーナーの承認待ち。今回は承認しない**）

### 4.1 A.I6 Snapshot

| 項目 | 案 |
|---|---|
| 扱い | Phase 4 までの延期の候補 |
| Journal の容量の上限 | 案：Journal の DB が 1 GiB、または 1,000,000 件のイベントの、先に達した方。**未承認・未計測の提案値** |
| 起動時間の許容の上限 | 案：開く処理（全体の検証と Rebuild を含む）が 60 秒以内。**未承認・未計測の提案値** |
| Rebuild の性能の確認の方法 | 事前に登録した計測：合成の Journal を 1万・10万・100万件のイベントで作り、開く処理を5回ずつ計り、中央値を記録する。Phase 2 の開始の前に行う案。**今は計測していない。性能の主張ではない** |
| 上限を超えたとき | 案：新しい Run の開始を止め、オーナーに知らせる。Journal の削除や切り詰めはしない。**この停止の措置は未実装**（今の製品は容量と起動時間を監視していない）。承認されたら、実装と試験が要る |

### 4.2 A.I12 承認待ちの Run の再開

| 項目 | 案 |
|---|---|
| 扱い | Phase 4 までの延期の候補（会話の記録の永続化と合わせて） |
| 再起動の後の自動の再開 | **禁止**（今の動作：承認待ちの Run は復旧で FAILED になり、再開しない。試験：`test_wp10_cli*.py`、`test_wp12_recovery.py`） |
| 古い権限の失効 | 開くときに、前の所有者の ISSUED・CLAIMED の Receipt を失効させる。承認は action_hash、ポリシーの版、アクセスの epoch に結び付き、変われば無効（試験：`test_wp12_fixes.py`、`test_permits.py`、`test_wp15_scenarios.py` S5） |
| 利用者への通知 | 今は CLI の `recovery` と `recover` が Run の状態（FAILED）と理由を示すだけ。案：`recover` の出力に、承認待ちの作業が失われたことと新しい Run で始め直すことの固定の文言を加える（**未実装。承認の後の候補**） |

**再起動の後に失われるもの、残るもの、費用の記録（訂正）：**

| 区分 | 内容 |
|---|---|
| 失われる | プロセスの中だけにある会話の記録（それまでのモデルの呼び出しの結果を含む Context）。承認待ちの操作の承認（無効になり、その操作は実行されない）。その Run の続き（再開できない） |
| 残る | **承認待ちの操作より前に、その Run で正常に完了したファイルの変更**（ブローカーが確定した CREATE・REPLACE）。復旧はそれを取り消さない。Journal と台帳の記録（各 Attempt の状態、決定、承認、Permit、Receipt、結果の digest） |
| 実行されていない | 承認待ちの操作そのもの（ファイルの変更を含む） |
| 費用 | それまでに確定した費用（モデルの呼び出し、ツール）は記録に残り、予算から引かれたまま。結果不明の保留は、証拠がなければ解放しない。二重に計上しない（試験：`test_wp12_*.py`、`test_qr01_reconcile.py`） |

- 初版の「ファイルの変更は起きていない」は誤りで、**承認待ちの操作について**だけ正しい。それ以前に完了したファイルの変更は残りうる。

### 4.3 A.I17 常時の期限の監視

| 項目 | 案 |
|---|---|
| 扱い | Phase 1 では、明示的な復旧（`recover`）の時だけ検査する延期の候補。ROADMAP のとおり、**Phase 2 で再評価する**（Sandbox と子プロセスを Phase 2 で入れるため） |
| Phase 1 の今の動作 | ループの上限（`max_elapsed_ms`、反復、呼び出しの回数）、Permit とグラントの期限、時計の高水位。RECOVERING・CANCELLING の期限は、`recover` を実行したときだけ `escalated` になる。停止は承認より優先し、新しい Permit を出さない。結果不明の保留は、期限切れでも解放しない |

**Phase 2 の開始の前に必要な設計上の判断（オーナーの判断を含む）：**

1. 常時の監視を Phase 2 で入れるか、明示的な復旧のままにするか。入れるなら、監視ができること（読み取りと昇格だけ）とできないこと（承認、保留の解放、結果の確定、完了）。
2. Sandbox の中の処理、子プロセス、シェルの実行のタイムアウトと停止の意味（停止・タイムアウト・異常終了を、それぞれ Attempt のどの状態にするか。子プロセスのタイムアウトや異常終了は、結果が分からないときは OUTCOME_UNKNOWN とし、失敗とみなさず、再実行しない）。
3. 長い処理の期限、RECOVERING・CANCELLING の期限の値（今は試験の値）。
4. 費用の保留と期限の関係（期限切れで保留を解放しない）。

**Phase 2 の中で、信頼しないコードを実行する前に必要な実装と試験（ROADMAP 0.2・Phase 2）：**

1. Docker の Sandbox の基本の設定（C1.S9：ホストのファイルを持たない、Docker のソケットがない、root でない、capabilities を外す、`no-new-privileges`、資源の上限）。
2. Sandbox が使えないか自己診断に失敗したら実行を拒否し、ホストで直接実行しない（C1.S10）。
3. ネットワークの隔離と自己診断（`--network none`、コンテナごとの設定の照合）。
4. 子プロセスの管理と、停止・タイムアウト・異常終了の処理。異常終了の後に残ったコンテナと一時データの回収（C1.S26）。
5. 受入の試験の案：
   1. 期限を過ぎた RECOVERING・CANCELLING の Run が、決めた方式（監視か明示的な復旧）で `escalated` になる。
   2. 監視（入れる場合）は、承認、保留の解放、結果の確定、Run の完了を行わない。
   3. 長い処理の最中の停止の後、新しい Permit が出ず、Sandbox の中の処理が止まる。
   4. 時計が戻っても、監視が再起動しても、期限は延びない。
   5. 監視が異常終了しても、状態は変わらない。
   6. 子プロセスのタイムアウトと異常終了は、結果が分からなければ OUTCOME_UNKNOWN になり、再実行されない。
   7. 自己診断に失敗した Sandbox では、コードが1行も実行されない。
- 3A の OS のレベルの隔離の強化を待つことを理由に、Phase 2 で隔離のないコードを実行しない（ROADMAP 0.2）。

## 5. 残っている受入の条件の区分

判定の全体は `docs/design/phase-1/WP-13_INTEGRATION.md` の3章。区分は ROADMAP に合わせた**案**で、オーナーが決める。

| 時期 | 項目 |
|---|---|
| **Phase 1 の正式な完了の前** | 8章の条件（CI での全品質検査、Phase 1 の Technical Gate と Safety Gate の確認、独立に実行できなかった試験の扱い、セキュリティ監査の範囲と時期の決定、3つの設計差の承認、最終承認） |
| **Phase 2 の開始の前（設計上の判断）** | ROADMAP の開始の条件：Docker の Sandbox の自己診断の方針（C1 の8.2）の確認。A.I17 の設計上の判断（4.3）。D.V19（安全の違反と通常の拒否、タスクの結果と安全の結果を分けて記録する形式）。A.I11（評価の再現に要る版の項目の記録の範囲）。A.I6 の計測の実施（承認されれば）。評価の環境と holdout の別のリポジトリ（オーナーの操作。開発のセッションは接続しない）。並行実行を入れるなら FINAL-R05 |
| **Phase 2 の中、信頼しないコードの実行の前（実装と試験）** | Docker の Sandbox の基本の設定と自己診断（C1.S9、S10）、Sandbox の中のシェルの実行、子プロセスの管理、停止・タイムアウト・異常終了の処理（C1.S22 の Sandbox の停止の部分、C1.S26）、ネットワークの隔離と自己診断、Sandbox へのコピーとホストへの反映（C1.S18、S19 の Sandbox の部分）、Docker の API を P2 だけが持つこと（C1.S24 の該当する部分）、A.I17 の再評価と 4.3 の受入の試験。Phase 2 の Safety Gate（C1 の12.2 のうち Sandbox に関わる試験） |
| **実 API の接続の前（Phase 3A）** | 追加の Technical Gate と Safety Gate（ROADMAP の Phase 3A）。OS のレベルの隔離の強化（C2 の5.5、rootless の Docker、C1-P04）。外部通信の Gateway（C1.S12、S21、送信の関門、`revocation_gen`、送信の Lease、ack。E.Z5）。Process Supervisor による停止（E の30.1）。Run の単位の独立した予算の台帳（E の34章）。認証情報と実データの保護（C1.S11、SAG、暗号化と鍵、鍵付きのハッシュ C2.X12・B1.C14、P3 を別の OS ユーザーにすること C1.S24）。AUDIT-02、AUDIT-04。D1・D5・I10 の文言の承認。実際の価格表（D.V14） |
| **後の Phase** | Snapshot（A.I6、承認されれば Phase 4）、承認待ちの Run の再開（A.I12、Phase 4）、MCP（C1.S25、Phase 4 で必要なら）、記憶・要約・取得（B1.C5 ほか、Phase 5）、削除と復元（AUDIT-03 は削除の機能の前）、E の DEFERRED の項目、`sqlite_ledger.py` の分割、A-P6（延期はオーナーが承認済み） |

- Phase 2 に必要な安全の条件を、Phase 3A まで延ばさない。

### 5.1 包括的なセキュリティ監査で残っている範囲

- **セキュリティ監査は行われていない。** CI が成功しても、Codex の品質の検証が合格しても、セキュリティ監査を済ませたことにはならない。
- 監査の対象の候補：権限の経路（Permit、Receipt、承認、停止）を迂回できないこと、台帳と Journal の改変の検出の範囲、File Broker のパスの解決（ADR-0008）、CLI の入力と出力、秘密情報の検査の限界、供給網（`uv.lock`、Action の SHA、uv の wheel、`setup-python` の Python と runner のイメージ）。
- 主張しないこと：同じ利用者で協調しないプロセス、root、DB を持つ者の一貫した書き換えの検出。DB とファイルの間の原子性。OS のレベルのネットワークの隔離（試験の中は Python の `socket` だけ）。秘密情報の検査が、すべての秘密の形を見つけること。

## 6. FA-01 の修正（ROADMAP との整合）

| 初版の記述 | ROADMAP（正本） | この版 |
|---|---|---|
| 4.3：Sandbox は Phase 3A | Phase 2 に Docker の Sandbox の基本の設定、自己診断、Sandbox の中のシェルの実行。信頼しないコードを実行する前に隔離を試験で確かめる（Phase 2、0.2） | Phase 2 の中の、信頼しないコードの実行の前の条件に移した |
| 4.3：子プロセスのタイムアウトは Phase 3A の要件 | Phase 2 で Sandbox の中の実行と、その停止とタイムアウトを扱う | Phase 2 に移した。Phase 3A は Process Supervisor による停止の強化 |
| 4.3：A.I17 は「Phase 2 の開始の前に再評価」 | A.I17 の再評価は Phase 2 の範囲 | 「Phase 2 の開始の前の設計上の判断」と「Phase 2 の中の実装と試験」に分けた |
| 5：Sandbox と OS の隔離（C1.S9・S10・S18〜S26）を実 API の接続の前にまとめていた | Sandbox は Phase 2。OS のレベルの隔離の強化、Gateway、Process Supervisor、独立の予算の台帳、認証情報と実データの保護は 3A | Phase 2 と 3A に分けた（5章） |

- 同じ食い違いがあった `WP-13_INTEGRATION.md`（3章の C1 の表と 3.6）と、実装報告書の 3.2 も直した。

## 7. 品質の検査（Claude Code の環境で実行、独立した実行ではない）

HEAD `89f8584`（2026-11-27）の結果。依存は既存の `uv.lock` のまま。試験は 2138 件。

| 実行 | 利用者・環境 | 成功 | 失敗 | 除外・未実行 |
|---|---|---|---|---|
| 8種類の検査 | root | すべて成功（pytest 2138） | 0 | 0 |
| 全試験の連続2回 | root | 2138、2138 | 0 | 0 |
| ネットワークのない環境 | root、全試験 | 2138 | 0 | 0 |
| CI の順序の検査 | `nobody`（`nobody` が所有する clone） | 秘密情報、`uv lock --check`、ruff、ruff format、mypy、import-linter | 0 | `uv sync --locked --offline --check` は `nobody` では未実行（root では成功） |
| 通常の試験（`-m "not privileged"`） | `nobody` | 2133 | 0 | 5（除外） |
| 特権の試験（`-m privileged`） | root | 5 | 0 | 2133（除外） |
| ネットワークのない環境、通常の試験 | `nobody` | 2133 | 0 | 5（除外） |

この版（文書の修正だけ）の8種類の検査の結果は、STATUS.md と最終報告に記す。

## 8. Phase 1 の正式な完了の条件の一覧

1. **GitHub Actions での全品質検査：** オーナーの決めた方法（3.1）で CI を実行し、8種類の検査（通常の試験を runner の利用者で）と特権の試験（root）が成功し、対象の SHA、環境、件数を記録する。
2. **Phase 1 の Technical Gate と Safety Gate の確認**（ROADMAP の Phase 1）：
   - Technical Gate（状態遷移、Journal、復旧の不変条件の試験の合格、障害の注入を含む）：Claude Code の環境では成功。独立した環境での全件の成功は、まだない（1.2）。CI で確かめる。
   - Safety Gate（外部への通信 0 件、Permit のない副作用 0 件、認証情報が存在しないこと）：import の契約、試験の中の通信の遮断、ネットワークのない環境での全試験の成功、Permit の試験（A.I2、A.I13）、秘密情報の検査で確かめた範囲。OS のレベルの遮断は Phase 1 の範囲では主張しない。オーナーがこの範囲で合格と認めるかを決める。
3. **独立に実行できなかった試験の扱い：** Codex の環境で成功しなかった 345 件（130 件の失敗、215 件のエラー）と、未実行の特権の5件は、条件に合う環境（CI など）で独立に成功するまで未検証とする。環境の問題という推定を成功として扱わない。
4. **包括的なセキュリティ監査：** 行われていない。Phase 1 の完了の前に行うか、どの範囲を後の Phase の Gate で行うかをオーナーが決める（5.1）。CI の成功は監査の代わりにならない。
5. **A.I6、A.I12、A.I17 の承認：** オーナーが承認・修正・却下を決める（今回は承認していない）。
6. **最終のオーナーの承認：** 上の条件と、残った課題（5章）の扱い（移しただけで解決とはしない）を確かめたうえでの承認。

## 9. 独立した環境で正式な依存環境を再現する手順

前提：Linux（x86_64、glibc）、Python 3.12、git。依存は `uv.lock` のとおり（新しい依存はない）。

**環境の条件（製品の安全性の検査が求めるもの。緩めない）：**

- 作業ツリー、一時ディレクトリ（`TMPDIR`、pytest の一時ディレクトリ）、CLI のホームの、すべての祖先のディレクトリ（`/` を含む）は、root（UID 0）か試験を実行する利用者の所有で、グループと他人が書けない（`/tmp` のようなスティッキーのディレクトリは可）。`/` が別の UID（例：65534）の所有の環境では、File Broker と CLI の試験は設計どおりに拒否され、成功しない。
- 作業ツリーと `.git` は、試験を実行する利用者が所有する（秘密情報の検査の自己検査が Git の所有者の検査を使う）。

**手順：**

1. 対象のコミットを取得する（`git clone https://github.com/ken1700-mizui/agent-core.git`、`git checkout <対象の SHA>`）。
2. uv 0.11.32 をハッシュ検査付きで導入する：`python3.12 -m pip install --require-hashes --only-binary :all: --no-deps -r tools/ci/uv-requirements.txt`。
3. 依存を入れる（ネットワークが要るのはここだけ）：`uv sync --locked`。ネットワークのない環境では、ネットワークのある環境で `UV_CACHE_DIR=<共有の場所> uv sync --locked` を先に実行し、同じキャッシュで `UV_CACHE_DIR=<共有の場所> uv sync --locked --offline`。
4. 8種類の検査：`python3 -I tools/secret_scan.py --mode commit --rev HEAD`、`uv lock --check`、`uv sync --locked --offline --check`、`uv run --locked --offline ruff check .`、`uv run --locked --offline ruff format --check .`、`uv run --locked --offline mypy`、`PYTHONPATH=src uv run --locked --offline lint-imports`、`uv run --locked --offline pytest -m "not privileged"`。
5. 特権の試験（root）：`sudo .venv/bin/python -m pytest -p no:cacheprovider -m privileged`（5件）。root で実行できなければ「未実行」と記録する。
6. 記録すること：対象の SHA、OS とカーネル、Python と uv の版、利用者（uid）、`/` と作業の場所の祖先の所有者、各検査の件数（成功・失敗・エラー・除外・未実行）。
