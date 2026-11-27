# Phase 1 最終受入の準備

> 文書の状態：**DRAFT**（オーナーの判断と承認を待つ。正式な完了の宣言ではない）　作成：2026-11-27
>
> - 対象：`impl/phase-1-foundation`。作業の開始時点は `20fa058`。RR-01 の補強は `a54d52b`、CI の固定は `89f8584`、この文書はその次のコミット。
> - **Phase 1 の状態：最終受入の準備中。** CI の実行、包括的なセキュリティ監査、3つの設計差のオーナーの判断、オーナーの最終承認の前には、正式な完了としない。Phase 2 には進まない。

## 1. Codex の独立品質再レビューの記録

| 項目 | 内容 |
|---|---|
| 対象 SHA | `20fa05813aca71266d6c862c534d4bfee6f64677`（オーナーが今回の開始 HEAD として示した SHA。再レビューはこの時点の実装を対象にしたと理解している） |
| 判定 | **CONDITIONAL_PASS**（新しい HIGH・MEDIUM 以上の指摘なし） |
| QR-01（HIGH） | 主要な指摘は解消（オーナーからの伝達） |
| QR-02（MEDIUM） | 主要な指摘は解消（同） |
| QR-03（MEDIUM） | 主要な指摘は解消（同） |
| QR-04（MEDIUM） | 主要な指摘は解消（同） |
| 残った指摘 | RR-01（LOW）：`test_qr01_reconcile.py` の巻き戻しの試験で、`SyntheticResult` に `result_digest` がなく、`operation_results` への書き込みが起きていなかった（2章） |
| 独立して成功した検査 | **未受領。** Codex がどの検査をどの環境で成功させたかの詳細は、Claude Code の側に伝えられていない |
| 独立して実行できなかった検査 | **未受領。** 正式な依存環境の再現の手順（9章）を求められたが、Codex の側で何が実行できなかったかの詳細は伝えられていない |
| セキュリティ監査 | **包括的なセキュリティ監査は行われていない。** 今回の再レビューは品質のレビューである |

- 判定は、コードを読んで判断した静的なレビューの結果として扱う。Codex の側での全試験の実行結果（件数、成功、失敗）は受け取っていないので、ここに書かない。
- 下の 2.2 と 7章の試験の結果は、Claude Code がこの環境で実行したものであり、独立した実行ではない。
- Codex の報告書の該当する部分を受け取ったら、この表の「未受領」を置き換える。

## 2. RR-01 の補強

### 2.1 内容

- 合成の結果の digest（非 None）を持つ実行報告で照合する。
- 注入した障害（`_record_cost` の中）が、同じトランザクションのカーソルで `operation_results` を読み、結果の行が書かれていたことを確かめてから失敗させる。
- 開き直した後：結果の行がない（巻き戻された）、Attempt は OUTCOME_UNKNOWN のまま、予約は UNKNOWN で保留 5,000 を保つ、費用は確定していない。
- 正常に照合し直した後：結果の行は正しい digest で1行だけ。
- 製品のコードは変えていない。

### 2.2 結果

- 補強した試験は成功する（`a54d52b`）。
- 補強の効果の確認：以前の準備（digest なし）のまま新しい確認を実行すると、「トランザクションの中に結果の行がある」の確認で失敗した（`[] == ['sha256:…']`）。以前の試験では、巻き戻しの対象の行がそもそも書かれていなかったことを示す。

## 3. CI の公式資料の確認（2026-11-27、読み取りだけ）

この項目に限って許可された外部の照会で、GitHub などの公式の資料を読んだ。GitHub Actions は実行していない。支払いの設定も変えていない。

| 確認事項 | 結果 | 出典 |
|---|---|---|
| `actions/checkout` の正式な SHA | `v6` = `v6.1.0` = `d23441a48e516b6c34aea4fa41551a30e30af803`（軽量タグなので、そのままコミット）。これまで参照していた `v4` = `v4.4.0` = `11d5960a326750d5838078e36cf38b85af677262` | 公式リポジトリへの `git ls-remote --tags https://github.com/actions/checkout.git`、https://github.com/actions/checkout の README と `action.yml`（その SHA の版） |
| `actions/setup-python` の正式な SHA | `v6` = `v6.3.0` = `ece7cb06caefa5fff74198d8649806c4678c61a1`（軽量タグ）。これまでの `v5` = `v5.6.0` = `a26af69be951a213d495a4c3e4e4022e16d87065` | `git ls-remote --tags https://github.com/actions/setup-python.git`、https://github.com/actions/setup-python の README と `action.yml` |
| 今のメジャー版との互換性 | `checkout@v4` と `setup-python@v5` の `action.yml` は `using: node20`。GitHub の告知では、2026-09-23 から Node 20 は runner で使えず、JavaScript の action は Node 24 で動き、最新の版への更新が求められる。v6 は両方とも `using: node24`。私たちの入力（`persist-credentials: false`、`python-version`）は v6 でも同じ。checkout v6 の変更は認証情報の保存先（`persist-credentials` を使わないので影響なし）。setup-python v6 の破壊的な変更は Node 24 への移行だけ（runner v2.327.1 以上） | https://github.blog/changelog/2026-09-23-node-20-is-no-longer-available-in-github-actions/ 、 https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/ 、各 README |
| `workflow_dispatch` と既定のブランチ | 「`workflow_dispatch` を起動するには、ワークフローが既定のブランチにある必要がある」 | https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow |
| GitHub-hosted Linux runner の `sudo` | 「Linux と macOS の仮想マシンは、パスワードなしの sudo で動く」 | https://docs.github.com/en/actions/reference/runners/github-hosted-runners （Administrative privileges） |
| Action の固定 | 完全な長さのコミット SHA に固定することが推奨されている | https://docs.github.com/en/actions/reference/security/secure-use |
| uv の導入のハッシュの固定 | pip のハッシュ検査のモード（`--require-hashes`）と、ソースの配布物の禁止（`--only-binary :all:`）。uv 0.11.32 の x86_64 glibc の wheel の sha256 は `3da76cd4e2697de30928b8a8524bd39183ac1e08cb7e72833807c022b7cba6c4`。手元でハッシュ検査付きのダウンロードを行い、一致を確かめた（導入はしていない） | https://pip.pypa.io/en/stable/topics/secure-installs/ 、 https://pypi.org/pypi/uv/0.11.32/json |
| 無料の枠 | 標準の GitHub-hosted runner は、公開リポジトリでは無料（含まれる利用分も減らない）。非公開のリポジトリでは、GitHub Free の含まれる利用分は月 $12（標準の Linux で $0.006/分、約 2,000 分）、成果物の保存は 500 MB。**このリポジトリは公開（public）**（セッションのリポジトリ一覧で確認） | https://docs.github.com/en/billing/concepts/product-billing/github-actions |
| 追加の課金の防止 | 予算（budget）に「Stop usage when budget limit is reached」を設定すると、上限に達した後の利用は止まる | https://docs.github.com/en/billing/how-tos/set-up-budgets |

**実装ブランチで行った CI の最小限の修正（`89f8584`、実行していない）：**

- 2つの Action を、確かめた v6 の完全なコミット SHA に固定した（版をコメントで併記）。
- uv を `tools/ci/uv-requirements.txt` から、ハッシュ検査付き・wheel だけで導入する。
- `tests/unit/test_ci_definition.py`：SHA の固定、手動の起動だけ、ハッシュ付きの uv が `UV_VERSION` と一致、通常の試験と特権の試験の別の手順。
- 自動の起動の契機は加えていない。

### 3.1 CI の実行の方法の比較

| 方法 | 内容 | 利点 | 問題 |
|---|---|---|---|
| A. `main` を変えない | `workflow_dispatch` は既定のブランチに定義が必要なので、今の構成では起動できない。代わりの道は、(a) 既定のブランチを一時的に実装ブランチに変える、(b) 自動の契機（`push`、`pull_request`）を加える、(c) GitHub Actions を使わず、Codex や手元で同じ手順を再現する | (c) は `main` も既定のブランチも変えない | (a) は既定のブランチの変更（オーナーの承認が要る。Draft PR #1 の基準にも影響）。(b) は今回禁止されている自動の契機。(c) は CI の実行ではない（同じ手順の独立した再現にとどまる） |
| B. 最小限のワークフローだけを `main` に加える | `main` に `.github/workflows/ci.yml`（`workflow_dispatch` だけ）を1つのコミットで加え、実行のときに実装ブランチを選ぶ | 手動の起動のままで、既定のブランチは変えない。コード本体は `main` に入らない | `main` の変更（オーナーの承認が要る）。実行のときに選んだブランチの定義が使われるかは、資料の一般的な理解で、未確認。`main` の版と実装ブランチの版の食い違いを避けるため、同じ内容にする必要がある |

- Claude Code の推奨：**B**（変更が最も小さく、自動の契機を加えず、既定のブランチも変えない）。ただし `main` の変更なので、オーナーが決める。今回は行わない。

### 3.2 CI の実行の前に必要なオーナーの操作

1. 実行の方法の決定（3.1 の A または B）。B なら、`main` へのワークフローの追加の承認（作業は Claude Code が行い、マージはしない方法も含めて指示を受ける）。
2. リポジトリが公開であることの確認（公開のままなら標準の runner は無料。非公開にするなら、月の含まれる利用分と予算の設定）。
3. 予算（budget）の設定と「上限で利用を止める」の有効化の確認（支払いの設定はオーナーが行う。Claude Code は変えない）。
4. 初回の CI の実行の許可と、実行するコミットの SHA の指定。
5. 実行の後の記録：ジョブの要約（対象の SHA、runner の環境、Python と uv の版）を STATUS.md に写す。

## 4. 3つの設計差の承認案（**オーナーの承認待ち。今回は承認しない**）

### 4.1 A.I6 Snapshot

| 項目 | 案 |
|---|---|
| 扱い | Phase 4 までの延期の候補 |
| Journal の容量の上限（案） | Journal の DB が 1 GiB、または 1,000,000 件のイベントの、先に達した方 |
| 起動時間の許容の上限（案） | 開く処理（全体の検証と Rebuild を含む）が 60 秒以内（手元の SSD、合成のデータ） |
| Rebuild の性能の確認の方法 | 事前に登録した計測：合成の Journal を 1万・10万・100万件のイベントで作り、開く処理を5回ずつ計り、中央値を記録する。Phase 2 の開始の前に行う。**今は計測していない。上の数値は承認を求める閾値の案であり、性能の主張ではない** |
| 上限を超えたとき | 新しい Run の開始を止め（安全な側）、オーナーに知らせる。Journal の削除や切り詰めはしない（正本）。Snapshot の前倒しを、オーナーの判断で決める |

### 4.2 A.I12 承認待ちの Run の再開

| 項目 | 案 |
|---|---|
| 扱い | Phase 4 までの延期の候補（会話の記録の永続化と合わせて） |
| 再起動の後の自動の再開 | **禁止**（今の動作：承認待ちの Run は復旧で FAILED になり、再開しない。試験：`test_wp10_cli*.py`、`test_wp12_recovery.py`） |
| 古い権限の失効 | 開くときに、前の所有者の ISSUED・CLAIMED の Receipt を失効させる。承認は action_hash、ポリシーの版、アクセスの epoch に結び付き、変われば無効（試験：`test_wp12_fixes.py`、`test_permits.py`、`test_wp15_scenarios.py` S5） |
| 費用の記録と保留 | 確定した費用は記録に残る。結果不明の保留は、証拠がなければ解放しない。二重に計上しない（試験：`test_wp12_*.py`、`test_qr01_reconcile.py`） |
| 作業の喪失の範囲 | 承認待ちの前に行ったモデルの呼び出しの結果（会話の内容）。ファイルの変更は起きていない（承認待ちの操作は実行されない） |
| 利用者への通知 | 今は CLI の `recovery` と `recover` が、Run の状態（FAILED）と理由を示すだけ。案：`recover` の出力に「承認待ちの作業は失われた。新しい Run で始め直す」旨の固定の文言を加える（**未実装。承認の後に実装する候補**） |

### 4.3 A.I17 常時の期限の監視

| 項目 | 案 |
|---|---|
| 扱い | Phase 1 では、明示的な復旧（`recover`）の時だけ検査する延期の候補。**Phase 2 の開始の前に再評価する** |
| Sandbox | Phase 1 にはない（Phase 3A）。Sandbox の中の処理の期限は、Sandbox の停止の手順と合わせて決める |
| 長い処理 | ループの上限（`max_elapsed_ms`、反復、呼び出しの回数）、Permit とグラントの期限がある。RECOVERING・CANCELLING の期限は、`recover` を実行したときだけ `escalated` になる |
| 子プロセス | Phase 1 では起動しない（import の契約で禁止）。Phase 3A で、子プロセスのタイムアウトは「結果不明」として扱い、失敗とみなさないことを要件にする |
| タイムアウト | 時計の高水位で期限が延びない（`test_wp12_fixes.py` R04、`test_qr02_qr03_consistency.py`） |
| 停止 | 停止は承認より優先し、新しい Permit を出さない。停止の意図は再起動で失われない |
| 費用の保留 | 期限切れでも、結果不明の保留は解放しない（証拠が要る） |

**Phase 2 の前に提案する受入試験：**

1. RECOVERING・CANCELLING のまま期限を過ぎた Run が、オーナーの操作なしに、監視の1周期の中で `escalated` になる。
2. 監視は、承認、保留の解放、結果の確定、Run の完了を行わない（読み取りと昇格だけ）。
3. 長い処理の最中の停止の後、新しい Permit が出ない。
4. 時計が戻っても期限は延びない。監視の再起動でも延びない。
5. 監視が異常終了しても、状態は変わらない（冪等）。
6. 子プロセス（Phase 3A）のタイムアウトは OUTCOME_UNKNOWN になり、再実行されない。

## 5. 残っている受入の条件の区分

判定の全体は `docs/design/phase-1/WP-13_INTEGRATION.md` の3章。下の区分は**案**で、オーナーが決める。

| 時期 | 項目 |
|---|---|
| **Phase 1 の正式な完了の前** | CI の実行（3章の操作）。3つの設計差（A.I6、A.I12、A.I17）のオーナーの判断。包括的なセキュリティ監査を Phase 1 の完了の前に行うかのオーナーの決定。Codex の CONDITIONAL_PASS の条件（RR-01）の確認。オーナーの最終承認 |
| **Phase 2 の開始の前** | A.I17 の再評価。D.V19（安全の違反と通常の拒否、タスクの結果と安全の結果を分けて記録すること）。A.I11（評価の再現に要る版の項目の Step のイベントへの記録の範囲）。A.I6 の計測。評価の環境と holdout の別のリポジトリ（オーナーの操作。開発のセッションは接続しない）。並行実行を入れるなら FINAL-R05 |
| **実 API の接続の前** | AUDIT-02（予算のタイムゾーン）、AUDIT-04、D1・D5 の文言、鍵付きのハッシュ（C2.X12、B1.C14）、認証情報の扱い（C1.S11）、外部通信の Gateway（C1.S12）、Sandbox と OS の隔離（C1.S9・S10・S18〜S26）、実際の価格表（D.V14）、ack と送信の Lease（E.Z5）、Phase 3A の Technical と Safety の Gate |
| **後の Phase** | Snapshot（A.I6、承認されれば）、承認待ちの Run の再開（A.I12）、記憶・要約・取得（B1.C5 ほか）、削除と復元（AUDIT-03 は削除の機能の前）、E の DEFERRED の項目、`sqlite_ledger.py` の分割、A-P6（初回 Phase 0 報告書の全文との照合。延期はオーナーが承認済み） |

### 5.1 包括的な安全性の確認で残っている範囲

- セキュリティ監査は行われていない（品質のレビューとは別）。
- 主張しないこと：同じ利用者で協調しないプロセス、root、DB を持つ者の一貫した書き換えの検出。DB とファイルの間の原子性。OS のレベルのネットワークの隔離（試験の中は Python の `socket` だけ）。
- 供給網：`uv.lock` のハッシュ、Action の SHA、uv の wheel のハッシュは固定した。Python 自体（`setup-python`）と runner のイメージは GitHub の提供のまま。
- 秘密情報の検査は、決まった規則によるもので、すべての秘密の形を見つけるとは主張しない。

## 6. 品質の検査（2026-11-27、HEAD `89f8584`、この環境で実行）

Claude Code がこの環境で実行した結果である（独立した実行ではない）。依存は既存の `uv.lock` のまま。試験は 2138 件（2134 件に CI の定義の試験4件を加えた）。

| 実行 | 利用者・環境 | 成功 | 失敗 | 除外・未実行 |
|---|---|---|---|---|
| 8種類の検査 | root、手元 | すべて成功（pytest 2138） | 0 | 0 |
| 全試験の連続2回 | root | 2138、2138 | 0 | 0 |
| ネットワークのない環境（`unshare -n`） | root、全試験 | 2138 | 0 | 0 |
| CI の順序の検査 | `nobody`（`nobody` が所有する clone） | 秘密情報（3方式）、`uv lock --check`、ruff、ruff format、mypy、import-linter | 0 | `uv sync --locked --offline --check` は `nobody` では未実行（環境が root の作業ツリーにある。root では成功） |
| 一般ユーザーの通常の試験（`-m "not privileged"`） | `nobody` | 2133 | 0 | 5（除外） |
| 同（`-m` なし） | `nobody` | 2133 | 5（`PRIVILEGED_NOT_RUN`。成功に数えない） | 0 |
| 特権の試験（`-m privileged`） | root | 5 | 0 | 2133（除外） |
| ネットワークのない環境、通常の試験 | `nobody` | 2133 | 0 | 5（除外） |

## 8. Phase 1 の正式な完了の条件の一覧

1. CI を、オーナーの決めた方法で実行し、8種類の検査と特権の試験が成功し、対象の SHA と環境を記録する。
2. A.I6、A.I12、A.I17 について、オーナーが承認・修正・却下を決める（今回は承認していない）。
3. 包括的なセキュリティ監査の時期をオーナーが決め、Phase 1 の前と決まれば、それを受けて必要な修正を行う。
4. Codex の CONDITIONAL_PASS の条件（RR-01）が満たされたことの確認。
5. 残った課題（5章）の扱いをオーナーが承認する（移しただけで解決とはしない）。
6. オーナーの最終承認。

## 9. Codex で正式な依存環境を再現する手順

前提：Linux（x86_64、glibc）、Python 3.12、git、一般ユーザーの権限（特権の試験だけ root）。依存は `uv.lock` のとおり（新しい依存はない）。

1. 対象のコミットを取得する（例：`git clone https://github.com/ken1700-mizui/agent-core.git`、`git checkout <対象の SHA>`）。作業ツリーと `.git` は、試験を実行する利用者が所有すること（秘密情報の検査の自己検査が Git の所有者の検査を使うため）。
2. uv 0.11.32 を、ハッシュ検査付きで導入する：`python3.12 -m pip install --require-hashes --only-binary :all: --no-deps -r tools/ci/uv-requirements.txt`。
3. 依存を lock のとおりに入れる（ネットワークが要るのはここだけ）：`uv sync --locked`。ネットワークのない環境では、ネットワークのある環境で `UV_CACHE_DIR=<共有の場所> uv sync --locked` を先に実行してキャッシュを作り、同じキャッシュを渡して `UV_CACHE_DIR=<共有の場所> uv sync --locked --offline` とする。
4. 8種類の検査（`README.md` の「検査」と同じ）：`python3 -I tools/secret_scan.py --mode commit --rev HEAD`、`uv lock --check`、`uv sync --locked --offline --check`、`uv run --locked --offline ruff check .`、`uv run --locked --offline ruff format --check .`、`uv run --locked --offline mypy`、`PYTHONPATH=src uv run --locked --offline lint-imports`、`uv run --locked --offline pytest -m "not privileged"`。
5. 特権の試験（root）：`sudo .venv/bin/python -m pytest -p no:cacheprovider -m privileged`（5件）。root で実行できない環境では、特権の5件は「未実行」と記録する（成功に数えない）。
6. 記録すること：対象の SHA、OS とカーネル、Python と uv の版、利用者（uid）、各検査の件数（成功・失敗・除外・未実行）。
