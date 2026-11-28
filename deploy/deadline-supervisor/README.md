# Deadline Supervisor の設定のテンプレート（A1。適用していない）

> 状態：**テンプレートだけ**（D20）。この環境では適用していない。
> OS の利用者の追加、cgroup の委任、systemd の設定の適用、Docker の設定の変更は、適合する環境（E-5・E-6）での**別の承認**とする。
> A2（root の方式）は承認されていないので、テンプレートもない。

| ファイル | 内容 |
|---|---|
| `agentcore-sandbox.slice` | Sandbox だけを置く親の cgroup。中身は Agent Core の Sandbox だけにする |
| `agentcore-deadline-supervisor.service` | DS の service。利用者は `agentcore-ds`（root でない）。能力は持たせない。`Delegate=yes` で、自分の cgroup の部分木だけを書ける。`Restart=always`、`WatchdogSec=` |
| `daemon.json.example` | dockerd の例。`cgroup-parent` で、コンテナを委任の部分木の下に置く。userns-remap は WP2-03 のまま |

## 確かめていないこと（E-5・E-6 で確かめる）

1. systemd の版での `Delegate=` の挙動。
   - Docker が作るコンテナの cgroup が、DS の service の委任の部分木の下に入るか。
   - 入らない場合の方式：DS の service が自分の部分木の下に `sandboxes/` を作り、dockerd の `cgroup-parent` にそこを指す。この方式の可否も確かめる。
2. `cgroup.kill` の動作（カーネル 5.14 以上）と、`cgroup.events` の `populated 0` での確認。
3. userns-remap のコンテナの cgroup の所有者と、`agentcore-ds` が `cgroup.kill` を書けるか。
4. DS が委任の部分木の外に書けないこと（`ProtectSystem=strict`、`ReadWritePaths=`）。
