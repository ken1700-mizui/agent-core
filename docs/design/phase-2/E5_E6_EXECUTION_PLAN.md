# E-5 の承認の案と、E-6（SG-1）の実行の条件

> 文書の状態：**DRAFT**（次回の承認の案。D20 では実行も CI の設定の変更もしていない）　作成：2026-11-28
> **E-5 は SG-1 ではない。** 開発者が、適合しうる環境での動作を確かめるだけである。SG-1 は E-6（開発者から独立した担当、適合する環境）で行う。

## 1. E-5：GitHub Actions の標準の runner での確認（承認の案）

### 1.1 確かめる条件（runner の上で、実行の始めに確かめる。満たさなければ不合格または未検証として記録する）

| # | 条件 | 確かめ方 | 満たさない場合 |
|---|---|---|---|
| 1 | cgroup v2 | `stat -fc %T /sys/fs/cgroup` が `cgroup2fs` | E-5 は未検証。DS の試験はしない |
| 2 | systemd が PID 1 | `ps -p 1 -o comm=` が `systemd` | 同上 |
| 3 | AppArmor か SELinux の強制のモード | `aa-status --enabled`、docker の SecurityOptions に `apparmor` | MAC の項目は未検証 |
| 4 | Docker | `docker info`。userns-remap を設定して再起動した後の SecurityOptions | Sandbox の試験は未検証 |
| 5 | 最小権限の方式（A1） | 次の4つ。<br>- DS の利用者（`agentcore-ds`、root でない、能力なし）<br>- `Delegate=yes` の service<br>- Docker の `cgroup-parent`<br>- DS の利用者が委任の部分木の外に書けないことの試験 | A1 は不合格。A2 には切り替えない |
| 6 | 別の OS 利用者 | `agentcore-p1`、`agentcore-p2`、`agentcore-ds`。それぞれの到達の試験（既存の `test_wp2_02_os_users` と DS の版） | 不合格 |
| 7 | ネットワークの隔離 | Sandbox の `network=none`。DS の `PrivateNetwork=yes`。コンテナから外へ届かない試験 | 不合格 |
| 8 | Deadline Supervisor の動作 | `DEADLINE_SUPERVISOR_DESIGN.md` の8章の6項目を、本物の cgroup v2 で。<br>- P1・P2 の SIGKILL の後の停止<br>- DS の SIGKILL と再起動<br>- 時計<br>- 範囲<br>- 記録の破損<br>- 停止を確かめられない場合 | 項目ごとに不合格か未検証 |

GitHub の runner の環境の事実（cgroup v2、systemd、AppArmor）は、**確かめていない**。E-5 の実行の結果として記録する。

### 1.2 承認をお願いしたいこと（次回）

| 項目 | 案 |
|---|---|
| 対象の SHA | この Batch の最後の HEAD（報告に書く）。承認の時に、オーナーが SHA を固定する |
| workflow の変更 | `.github/workflows/ci.yml` に、手動の実行（`workflow_dispatch`、入力は対象の SHA）だけの別のジョブ `sandbox-e5` を加える。次のことを行う。<br>- Action は SHA で固定し、`permissions: contents: read`、`persist-credentials: false` とする。<br>- 1.1 の確かめ。<br>- 利用者の作成、`daemon.json`、DS の単位（`deploy/deadline-supervisor` のテンプレートから）の設置と起動。<br>- イメージをダイジェストで取得。<br>- `pytest -m "sandbox or e5"` と自己診断。<br>- 結果は記録の成果物として残す。<br>既存のジョブは変えない |
| 新しい試験 | `e5` の印の試験（本物の cgroup v2 と systemd の DS）。この印は、条件を満たさない環境では実行されず、**未実行と表示**する（成功に数えない）。E-5 の承認の前に、別の作業として作る |
| Docker と DS の接続 | 作成と開始を分け、その間で登録する。Docker の cgroup のパスの決まり（`docker-<id>.scope`）を DS の対象の名前に対応させる。取り出し（tmpfs から）と、入力の束の展開。いずれも E-5 の前に実装と試験が要る（今は未実装） |
| 費用 | 公開のリポジトリの標準の runner は $0（D7 の方式）。**リポジトリが公開か、無料の枠かは、実行の前に確かめる**（非公開なら分の枠の消費。追加の課金はしない設定を確かめる） |
| 実行の条件 | 手動で1回。秘密は使わない。結果は公開される |
| 位置づけ | 開発の確認。**SG-1 ではない** |

承認の文案：

> E-5 として、`ci.yml` への `sandbox-e5` の手動のジョブの追加と、SHA ____ での1回の実行を承認する。追加の課金がないことを実行の前に確かめる。

## 2. E-6（SG-1）の実行の条件

| 条件 | 内容 |
|---|---|
| 担当 | 開発者（Claude Code）でない者。オーナーが選ぶ |
| 環境 | 開発の環境から独立した Linux の VM。次の条件をすべて満たす。<br>- cgroup v2<br>- systemd が PID 1<br>- AppArmor か SELinux が強制のモード<br>- Docker（userns-remap、固定の版）<br>- ホストの側での外への通信の遮断<br>- 秘密、実データ、validation、holdout を置かない |
| 確認の一覧（開始の前に固定する。開発者は変えない） | 次の6つ。<br>- P-5 の 18 項目<br>- 自己診断の9項目<br>- 逃げ道の探索<br>- DS の受入の条件の6項目<br>- S-R04 の受入の条件<br>- E-5 で未検証だった項目 |
| 合格の基準 | すべての項目を満たす。結果を見た後に基準を変えない |
| 記録 | 担当がオーナーに直接返す |
| 前提（E-6 の前に満たす） | 次の5つ。<br>- Docker と DS の接続<br>- 取り出しと入力の展開の実装<br>- E-5 の結果の確認<br>- 審査の合格<br>- オーナーの承認 |

## 3. 試験の成功と、安全の条件の達成の区別（D20）

| 項目 | 今の試験の結果 | 安全の条件の判定 |
|---|---|---|
| P-5 の 18 項目 | 開発の環境の試験は成功（`WP2-04_P5.md`、S-R05） | **未達成**。P1 と P2 の両方が止まった時の時間の上限（3、8、H、A）は、DS を適合する環境で確かめるまで満たさない。項目 6 は同じ boot の中だけ。B は「止められる」ことの確認だけ。E の時間の上限は P2 が動いている間だけ |
| DS の受入の条件の6項目 | Fake の cgroup での契約の試験は成功（`test_sr01_deadline_supervisor.py`） | **未達成**。本物の cgroup v2、systemd、別の OS 利用者で確かめていない（E-5 で開発の確認、E-6 で SG-1） |
