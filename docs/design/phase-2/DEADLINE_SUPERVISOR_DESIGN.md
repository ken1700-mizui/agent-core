# Deadline Supervisor（S-R01 の最終設計案。実装しない）

> 文書の状態：**DRAFT**（設計案。製品の実装、新しい OS の権限の追加はしていない）　作成：2026-11-28　承認：D19（案 B の基本の方針の受入、最終設計への補強）
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
- 登録できるのは委任の部分木の下のパスだけ。それ以外は拒否する。

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
| DS の記録の破損 | 起動の時に MAC、連鎖、解析のどれかが失敗した場合：**FAIL_SAFE**。委任の部分木の**すべて**の cgroup を止め（Sandbox はどれも使い捨て）、オーナーが確かめて戻すまで登録を拒否する |
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
