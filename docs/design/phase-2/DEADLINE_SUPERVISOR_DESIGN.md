# Deadline Supervisor（S-R01 の最終設計案。実装しない）

> 文書の状態：**DRAFT**（設計案。D20 で A1 の製品のコード・型・接続口・試験・設定のテンプレートを実装した（11章）。新しい OS の権限は追加していない）　作成：2026-11-28　承認：D19（案 B の基本の方針の受入、最終設計への補強）
> 名前：**Deadline Supervisor**（DS）。Phase 3A の P3 Gateway と重ならないように、「P3」は使わない。
> 実装には別の承認が要る。要るものは、新しい OS の利用者、systemd の単位、cgroup の委任、Docker の設定の変更。

## 1. 目的と、保証の範囲

**目的：** P1 と P2 が両方止まっても、また中のコードが PID 1 の監督を止めても、登録された期限（＋猶予＋DS の周期）の内に、その Sandbox のすべてのプロセスを止める。

**保証しないこと：**
- ホスト、カーネル、systemd の障害。
- root による変更。
- 電源の断。
- cgroup の仕組みそのものの欠陥。

主張しない。

## 2. 権限の境界（最小権限）

| 方式 | 内容 | 権限 | 判定 |
|---|---|---|---|
| **A1（推奨）：委任された cgroup の部分木** | Docker の `--cgroup-parent` で、すべての Sandbox を専用の親（例：`agentcore-sandbox.slice`）の下に置く。systemd の委任（`Delegate=`）で、その部分木だけを DS の利用者（`agentcore-ds`、root でない）に渡す。止めるのは `cgroup.kill`（cgroup v2、カーネル 5.14 以上）への書き込み | シグナルの権限（CAP_KILL）なし。root でない。書けるのは委任された部分木の cgroup のファイルだけ | **推奨。実現の可否は未確認**（下の 2.1） |
| A2（A1 が使えない場合の予備） | DS を uid 0 で動かし、systemd で権限をすべて外す。具体的には `CapabilityBoundingSet=`（空）、`NoNewPrivileges=yes`、`ProtectSystem=strict`、`ReadWritePaths=` を委任の部分木と DS の記録だけにする | 能力なしの uid 0。書けるのは、root が所有する、上の部分木の cgroup のファイルだけ | A1 より広い。オーナーの判断を要する |
| 採らない | CAP_KILL を持つ利用者で、PID にシグナルを送る | userns-remap の uid のどのプロセスにも送れる（広すぎる）。PID の再利用の競合もある | 不採用 |
| 採らない | Docker API での停止 | Docker のソケットの権限（実質 root）が要る | 不採用（P2 だけが持つ） |

DS が持たないもの：
- Docker のソケット。
- P1 の Journal と台帳。
- 鍵の一部。DS 自身の MAC の鍵だけは持つ。
- 承認、Permit、費用の確定、再実行の経路。

この分離は、import の契約と OS の利用者で保つ。

### 2.1 cgroup v2 での実現の可否（確認していない項目）

| 項目 | 状態 |
|---|---|
| `cgroup.kill`（v2、5.14 以上） | カーネルの文書の仕様。**この環境は cgroup v1、cgroupfs の driver で、systemd は PID 1 ではない。確かめられない** |
| systemd の `Delegate=` を、Docker が作る scope の親の slice に適用できるか | **未確認**。版に依る。できなければ、DS の service が所有する部分木を作り、dockerd の `--cgroup-parent` にその部分木を指す方式を確かめる |
| userns-remap のコンテナの cgroup が、委任の部分木の下に入るか | **未確認** |
| `cgroup.events` の `populated 0` で、全プロセスの停止を確かめられるか | 仕様上は可。**未確認** |

確認は E-6（SG-1 の環境）で行う。確かめられなければ A2 か、信頼しないコードを実行しない。

## 3. 対象の識別と、登録の寿命

**登録の内容：** (receipt_id, attempt_id, container_id, cgroup のパス, boot_id, 受け付けた CLOCK_BOOTTIME, deadline_ms, grace_ms, P2 の instance)

- cgroup のパスは、container_id と親から決まる（例：`<親>/docker-<id>.scope`）。

**状態の流れ：**

```
REGISTERED → ATTACHED（cgroup が現れた）
  → (期限) KILLING → KILLED（populated 0 を確かめた）
  | (P2 の回収) RELEASED
  | NOT_STARTED（期限に cgroup がなかった）
  | KILL_UNCONFIRMED
```

- **登録は開始の前。** P2 は、DS の受付の確認（MAC 付き、記録を同期した後）を受けてから、コンテナを start する。
- **終わり：** KILLED、RELEASED、NOT_STARTED。
  - RELEASED：P2 が回収を報告し、DS が cgroup がないことを確かめた。
  - 記録は残し、消さない（保存の期間はオーナーが決める）。
- 登録できるのは委任の部分木の直下の、`agentcore-` で始まる1つのパスの要素だけ（`/`、`..`、ほかの名前は型で拒否）。

### 3.1 操作の対象の範囲（D20 で整合を取った）

| 状態 | 触れてよい対象 | 理由 |
|---|---|---|
| 通常（OK、UNCONFIRMED_STOP） | **登録された対象だけ**。登録されていない cgroup には、部分木の下にあっても何もしない | 未登録の対象への操作の禁止 |
| FAIL_SAFE（記録を信頼できない） | 委任の部分木の**直下**の、Sandbox の名前（`agentcore-...`）の cgroup **すべて**。部分木の外と、ほかの名前には触れない | 記録がないので、どれが登録済みかを確かめられない。部分木は Agent Core の Sandbox だけを置く専用の場所（設定の時に確かめる、E-6）なので、そのすべてを、使い捨ての Sandbox として止める。未登録の対象の禁止の、ただ1つの例外として、範囲をこれに限る |

### 3.2 開始の窓と、状態の不一致（D20）

- 期限は**登録の時点**から数える。開始が遅れても、期限は延びない。
- 開始の確認（`confirm_start`）は、登録から `start_window_ms`（10 秒）以内だけ。過ぎたら `EXPIRED_BEFORE_START` で拒否し、NOT_RUNNING にする。期限を過ぎていても同じ。
- 開始の前に対象がすでに存在すれば、`STATE_MISMATCH` で拒否する（登録の時と、確認の時の両方）。
- 確認なしに対象が現れた場合（REGISTERED のまま cgroup がある）：不一致として**直ちに止める**。登録された対象なので、範囲の内。

## 4. 開始の前の確認と、新しい実行の拒否の条件

P2 の `prepare` と `start` は、次のどれかなら**開始の前に拒否**する（`DEADLINE_UNAVAILABLE`、P1 の側では実行されていない）。
1. DS の生存の応答がない。古い、MAC が合わない、別の DS の実行（instance）である場合も含む。
2. DS が登録を同期して記録したという確認がない。
3. DS が FAIL_SAFE か、KILL_UNCONFIRMED を1件でも持っている。
4. boot の時計か boot ID が読めない（S-R02 のまま）。
5. 委任の部分木が見つからない、または cgroup v2 でない。
6. systemd の管理の下にない（7章）。

P1 の側の確認（S-R03 の admission。D19 で省略できない）は、これに加えて残す。

## 5. 異常終了の扱い

| 地点 | 扱い |
|---|---|
| P2 が登録の前に止まる | 開始されない |
| P2 が登録の後、start の前に止まる | 期限に cgroup がなければ NOT_STARTED。P2 は再起動の後に、作ったコンテナを回収する |
| P2 が start の直後に止まる | DS が期限で止める（P2 に頼らない） |
| P1 と P2 の両方が止まる | 同上 |
| 中のコードが PID 1 の監督を止める | 同上。cgroup の全体を止める |
| DS が止まる | systemd の `Restart=always`。止まっている間、新しい開始は4章で拒否される。再起動の後、記録から期限を戻す。boot ID が違う、または期限を過ぎていれば、直ちに止める |
| DS の記録の破損 | 起動の時に MAC、連鎖、解析、末尾の行の切れのどれかが失敗した場合：**FAIL_SAFE**（範囲は 3.1）。オーナーが確かめて記録を戻すまで、登録を拒否する |
| 停止を確かめられない | 書き込みが失敗した、または一定の時間の後も populated が 1 の場合：KILL_UNCONFIRMED として記録し、警告する。新しい開始は拒否する。P1 の側は OUTCOME_UNKNOWN と保留のまま（Phase 1 の規則）。DS は周期ごとに止め直す |

## 6. 記録

DS の利用者だけが読み書きできるディレクトリ（0700）に置く。中身は、追記だけで MAC とハッシュの連鎖を持つ記録（B1A の実行の記録と同じ方式。head のファイル、fsync、ディレクトリの fsync）。

## 7. systemd が使えない環境

- `Restart=`、`WatchdogSec=`、委任が保証されない。そのため **DS は使えないものとして扱い、信頼しないコードの Sandbox は開始しない**（4章の6）。
- 手動で起動する DS は、保証の根拠にしない。
- 開発の環境（この環境）では、DS なしで、固定の診断のプログラムだけを扱う（D17 のまま）。

## 8. 受入の条件（実装の前に固定する案）

1. P1 と P2 を SIGKILL しても、期限＋猶予＋DS の周期の内に、cgroup の populated が 0 になる。
   - 中のコードが PID 1 の監督を止めた場合を含む。
   - fork を繰り返すプロセスの場合を含む。
2. DS を SIGKILL すると、新しい開始は拒否される。再起動の後、期限を過ぎたものは止まる。
3. 壁時計を戻しても、期限は延びない。boot ID の違い、記録の破損では、直ちに止める（FAIL_SAFE）。
4. DS は、委任の部分木の外と、登録されていない cgroup には触れない。Journal、台帳、Docker のソケットに届かない（OS の権限の試験）。
5. 停止を確かめられない場合は、KILL_UNCONFIRMED になり、新しい開始が拒否される。P1 の側は OUTCOME_UNKNOWN と保留のまま。
6. systemd の管理の下にない、または cgroup v2 でない場合、開始は拒否される。

## 9. Phase 3A への引き継ぎ

- DS の記録と、P1 の停止の状態（Phase 3A の停止のファイルと署名）を、独立の証人に結び付ける。
- 外部の通信の Gateway（P3 Gateway）とは別のプロセスのまま。権限の境界を混ぜない。
- 複数のホスト、遠隔の停止は、Phase 4 以後。

## 10. 必要な承認（実装の前）

1. 新しい OS の利用者 `agentcore-ds`。
2. systemd の単位：DS の service と、Sandbox の slice（委任）。
3. dockerd の `--cgroup-parent` の設定。
4. cgroup v2 の環境（E-6）。
5. A1 が確かめられない場合の、A2 の採否。

## 11. D20 の実装（環境に依存しない部分と、Fake による契約の試験）

| 部品 | 場所 |
|---|---|
| 型（登録、状態、拒否の理由、健康の状態） | `src/agent_core/types/supervisor.py` |
| 規則（登録、開始の確認、開始の報告、回収、期限の強制、停止の要求と確認の区別、再起動の後の期限、FAIL_SAFE） | `src/agent_core_supervisor/supervisor.py` |
| 記録（追記だけ、ハッシュの連鎖、HMAC、行ごとの fsync、0600） | `src/agent_core_supervisor/record.py` |
| cgroup v2 の接続口（`cgroup.kill`、`cgroup.events`、名前と種類の検査、cgroup2 の mount の検査） | `src/agent_core_supervisor/cgroups.py` |
| IPC（P2 の OS 利用者だけ、方向ごとの MAC）、クライアント | `src/agent_core_supervisor/server.py`、`client.py`、`ipc.py` |
| サービス（systemd の下で、cgroup v2 のときだけ動く。ほかは終了の状態 2 で拒否） | `src/agent_core_supervisor/__main__.py` |
| P2 の統合 | `Broker(supervisor=...)` は既定の値なし。開始の前に「健康の確認 → 登録 → 開始の確認」を行い、拒否はどれも `DEADLINE_UNAVAILABLE`（実行されていない）。開始の後は報告し、回収の後は解放する |
| 設定のテンプレート | `deploy/deadline-supervisor/`（適用していない） |
| import の契約 | DS は runtime、adapters、safety、cost、identity、tool、P2、CLI を import しない。P1 は DS を import しない |

**開発の環境の扱い：**
- この環境には cgroup v2 と systemd がないので、DS は動かない（サービスは拒否する）。
- Docker のコンテナを DS の対象にするには、cgroup のパスがコンテナの ID で決まる。そのため、作成と開始を分けて、作成と開始の間で登録する必要がある。これは未実装で、E-5 の前の作業とする。
- そのため Docker の試験だけは、明示の `diagnostics_without_supervisor`（D17 の固定の診断のプログラムだけ）で動かす。これは正式な経路ではなく、SG-1 でも使わない。
- DS なしの Broker は、この指定がなければ、すべての開始を拒否する。

**試験：** `tests/unit/test_sr01_deadline_supervisor.py`（22 件）。8章の6項目を、Fake で契約として確かめた。**試験の成功は、安全の条件を満たしたことではない**（E-6 で確かめるまで未達成）。
