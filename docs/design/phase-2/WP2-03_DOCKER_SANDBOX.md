# WP2-03 Docker の Sandbox（実装の記録）と環境の操作 E-1〜E-4 の記録

> 文書の状態：**DRAFT**（実装と試験の記録。審査待ち）　作成：2026-11-28　承認：D17
> **この環境には AppArmor も SELinux もない。ここでの結果は開発の確認だけで、正式な安全の合格でも SG-1 でもない。信頼しないコードは実行していない（実行の経路もない）。**

## 1. 環境の操作の記録（E-1〜E-4、2026-11-28）

| # | 行ったこと | 結果 |
|---|---|---|
| E-1 | 導入済みの dockerd を起動（`/etc/docker/daemon.json`：`userns-remap: default`、`no-new-privileges: true`、`icc: false`、ログの上限 1 MiB）。外への通信の設定は変えていない | 起動。SecurityOptions：`seccomp（builtin）`、`userns`、`no-new-privileges`。cgroup v1（cgroupfs）、memory・pids・CPU の制限あり。AppArmor・SELinux なし（cgroup v1 の非推奨の警告あり） |
| E-2 | 利用者 `agentcore-p1`（uid 996）、`agentcore-p2`（uid 995）、グループ `agentcore-ipc`。P2 だけ `docker` グループ。`/run/agentcore`（`agentcore-p2:agentcore-ipc`、2750、setgid）、`/var/lib/agentcore-p1`、`/var/lib/agentcore-p2`（各 0700） | 作成済み |
| E-3 | 公式の配布元のイメージ：`docker.io/library/busybox:1.37.0`（Docker Hub の公式のライブラリのイメージ）。取得：dockerd が環境の正規の HTTPS の proxy を通して `docker pull`（拒否なし）。マニフェストのダイジェスト `sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec`、イメージの ID `sha256:30ecbe1509090b38ef3260f3609d64d8261230464eaa4d235901db496e9647fb`、amd64 | 用意済み。プロファイルは ID とダイジェストで固定 |
| E-4 | 開発者が管理する固定の診断のプログラム（`DIAGNOSTIC_PROGRAMS`、14個）と固定の試験だけを実行 | 2章 |

## 2. 実装

| 対象 | ファイル | 内容 |
|---|---|---|
| Docker API | `agent_core_broker/docker_api.py` | 標準の `http.client` を AF_UNIX の上で使う（D17 の例外。import の契約で `agent_core_broker.docker_api -> http` だけ）。API の版を 1.44 に固定し、範囲を確かめる。すべての呼び出しにタイムアウト。応答の大きさの上限 |
| Backend | `agent_core_broker/docker_backend.py` | 固定のプロファイル（下）、9項目の自己診断、コンテナごとの設定の照合、固定の診断のプログラムだけ、入力の安全な写し、標準出力（上限 64 KiB）による出力、停止、回収、異常終了の後の引き取り |
| Broker | `agent_core_broker/server.py`、`__main__.py` | `prepare` は自己診断の合格を要する。期限は、受け付けた P2 の単調な時計、再起動の後は壁時計（記録の `deadline_unix_ms`）。`recover_backend`（結果が記録済みか記録にないものは回収、ACCEPTED は残して期限を守る）。`--backend docker` |

**プロファイル（1つ、`profile_hash` に含める）：** イメージの ID とダイジェスト、利用者 65534、`--network none`、読み取りだけのルート、cap をすべて外す、`no-new-privileges`、既定の seccomp、privileged・デバイス・ポート・ホストの名前空間なし、cgroup と IPC の名前空間は private、`Init`（PID 1 は Docker の init）、メモリ 256 MiB（スワップなし）、CPU 1、プロセス 64、`/tmp` は 16 MiB の tmpfs（`noexec`、`nosuid`、`nodev`）、入力は P2 の写しを `/in` に読み取りだけ、環境変数は `PATH` だけ。

**ファイルの4区分：** ①入力（P2 の写し、読み取りだけ。通常のファイルだけ、リンク・特殊なファイルは拒否、64 個・1 MiB まで）②作業（`/tmp` の tmpfs、16 MiB、実行できない）③出力（標準出力、64 KiB まで。`output_ref` はそのハッシュ）④それ以外（読み取りだけのルート。書けない）。**書き込みの量の上限**は tmpfs の大きさとログの上限。

**変更の反映：** 出力は P2 が持ち、`collect` で参照を返す。作業ツリーへの反映は、別に承認されるファイルの操作（WP-08 の経路）で行う（自動では反映しない）。P1 から P2 への入力の受け渡しの経路は未実装（P2 の写しの関数だけ）。

## 3. 自己診断（9項目）とこの環境の結果

| 項目 | 結果 |
|---|---|
| daemon、API の版、seccomp、userns、no-new-privileges、cgroup の制限、イメージ（ID とダイジェスト）、管理外の残り | 合格 |
| MAC（AppArmor か SELinux） | **不合格（この環境にない）** |

- 固定の診断のプログラム：MAC 以外の8項目の合格で許す。信頼しないコード：9項目すべてを要する（**この環境では不可**。そもそも実行の経路がない）。
- 自己診断に合格していなければ、`prepare` が拒否し、実行の前に中止する（試験で確認）。

## 4. 実環境での確認（固定の診断のプログラム。開発の確認）

| 確認 | 結果 |
|---|---|
| 利用者 | uid=65534 gid=65534 |
| ネットワーク | 届かない（`no-network`） |
| ルート | 読み取りだけ |
| tmpfs | 16 MiB で止まる。実行できない |
| プロセスの数 | `pids.max=64` |
| メモリ | `memory.limit_in_bytes=268435456` |
| cap・NoNewPrivs・seccomp | CapEff 0、NoNewPrivs 1、Seccomp 2（filter） |
| 入力 | 読める、書けない |
| 設定の違うコンテナ（ネットワーク、ルート、cap、privileged、プロセス、メモリ、SecurityOpt の7通り） | 開始の前に拒否して削除（privileged は daemon が先に拒否） |
| 停止 | 確認できる。TERM を無視するプログラムは猶予の後に KILL |
| P2 の期限 | 期限を過ぎたコンテナを止めて STOPPED を記録 |
| コンテナの中の補助の監督 | Docker の init があるときだけ働く（下）。P2 の期限の 5 秒後の予備 |
| P2 の異常終了の後 | 残ったコンテナを引き取り、期限（壁時計）で止める |
| Phase 1 のゲートの経路 | Docker の固定のプログラムで COMPLETED・FAILED_DEFINITE、停止の確定は閉じ込めの証拠つきで FAILED_DEFINITE |

**実測で分かったこと：** busybox の `timeout` は、プログラムを自分のプロセスのまま動かすので、それが PID 1 のときは、PID 名前空間の PID 1 は扱いのない信号を無視し、監督が自分のプログラムを止められなかった。Docker の init を PID 1 にすると止まる（終了の符号 137）。**PID 1 の監督を保護の根拠にしない設計（P-4）が正しいことの実測**。

## 5. 確かめていないこと

- AppArmor・SELinux のある環境での確認（E-5・E-6、承認されていない）。
- 実際のメモリの確保による OOM の確認（cgroup の設定の値だけを確かめた）。
- cgroup v2 の環境。
- P1 から P2 への入力の受け渡し、作業ツリーへの変更の反映の自動化。
- 信頼しないコード（承認されていない）。

## 6. 試験

`sandbox` の区分（`-m sandbox` で実行。既定の実行と既存の CI の命令では選ばれず、「SANDBOX_NOT_RUN」として件数を表示。環境がなければ失敗し、成功に数えない）：Docker 30件（`test_wp2_03_docker.py`）、別の OS 利用者 11件（`test_wp2_02_os_users.py`）。`sandbox` の試験だけ、AF_UNIX で `/var/run/docker.sock` への接続を許す（試験の中の遮断のほかの規則は変えない）。
