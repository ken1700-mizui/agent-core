# Agent Core

> 文書の状態：**DRAFT**（Phase 1 の実装の提出の時点）

Agent Core は、オーナー自身が使うための、専用の AI エージェントの基盤である。商用の製品ではない。

- 進捗の正本：`docs/STATUS.md`
- 開発の決まり（AI 向け、英語）：`AGENTS.md`
- Phase 1 の承認：`docs/decisions/PHASE_1_APPROVAL.md`
- Phase 1 の計画：`docs/design/phase-0/PHASE_1_IMPLEMENTATION_PLAN.md`

## 今の状態

- **Phase 1 は「独立品質レビュー指摘の修正提出済み・再レビュー待ち」**である。正式な完了ではない（再レビュー、CI の実行、包括的なセキュリティ監査、必要な修正、オーナーの最終承認の後に完了とする）。
- WP-01〜WP-15 を実装した（設計の記録は `docs/design/phase-1/`、実装の報告は `docs/reviews/PHASE_1_IMPLEMENTATION_REPORT.md`）。WP-01 の CI は、まだ GitHub Actions で実行していない。
- Phase 1 は、合成のデータだけを使う。実モデル、実 API、認証情報、外部への通信は使わない（費用は 0 ドル）。

## CLI（合成のデータだけ）

このリポジトリはパッケージをインストールしない。`src` をパスに入れて起動する（詳しくは `docs/design/phase-1/WP-10_CLI.md`）。

```bash
PYTHONPATH=src uv run --locked --offline python -m agent_core_cli init --home /path/to/new/home
```

```bash
PYTHONPATH=src uv run --locked --offline python -m agent_core_cli shell --home /path/to/new/home
```

- `status` と `recovery` は読み取りだけ（Runtime の所有権を取らない）。
- 対話のセッションの中で `help` を入力すると、命令の一覧を出す（合成のデモの開始、承認、停止、`recover <run>|all`、`reconcile` など）。

## 開発の環境（Linux）

必要なもの：Python 3.12、uv（0.11 以上）、git。

```bash
uv sync --locked
```

## 検査（CI と同じ順序）

依存関係を取得した後は、`uv run --locked --offline` で動かす（uv が依存関係を解決し直したり、取得したりしない）。

```bash
python3 -I tools/secret_scan.py --mode commit --rev HEAD
```

```bash
uv lock --check
```

```bash
uv sync --locked
```

```bash
uv sync --locked --offline --check
```

```bash
uv run --locked --offline ruff check .
```

```bash
uv run --locked --offline ruff format --check .
```

```bash
uv run --locked --offline mypy
```

```bash
PYTHONPATH=src uv run --locked --offline lint-imports
```

```bash
uv run --locked --offline pytest
```

**コミットの前**には、ステージした内容（次のコミットに入るもの）を検査する（`AGENTS.md` の 4.9）。

```bash
python3 -I tools/secret_scan.py --mode index
```

- `--mode worktree` は、作業ツリーのファイル（まだステージしていないものを含む）を検査する。
- ビルドの仕組みは使っていない（`docs/adr/0006-toolchain.md`）。そのため、import-linter には `PYTHONPATH=src` が必要である。
- CI（`.github/workflows/ci.yml`）は、手動で実行したときだけ動く。GitHub Actions の無料の枠と、課金を防ぐ条件を確かめるまでは、実行しない。

## 今の検査が保証できる範囲

| 検査 | 保証できること | 保証できないこと |
|---|---|---|
| `UV_OFFLINE` と `uv run --locked --offline` | uv が、依存関係の取得や解決のために通信しないこと。`uv.lock` と違う版を入れないこと | uv 以外のプログラムの通信。OS の全体の通信の遮断 |
| 試験の中のネットワークの遮断（`tests/conftest.py`） | pytest のプロセスの中で、Python の `socket` を通る通信（TCP、UDP、IPv4、IPv6、名前解決、`sendto`、`bind` など）を拒否すること | 子プロセスの通信。`_socket` を直接使うコードや、C の拡張が開く通信。OS のレベルの遮断 |
| import-linter の契約 | `agent_core` が、通信、プロバイダーの SDK、プロセスの起動のためのモジュール（`socket`、`_socket`、`http`、`urllib`、`asyncio`、`subprocess`、`multiprocessing`、`ctypes` など）を、静的に import しないこと | 実行するときの動的な import（下の検査で禁止している）。外部のパッケージの中での import |
| 静的な検査（`tests/unit/test_no_process_execution.py`） | `agent_core` が、`os.system`、`os.exec*`、`os.spawn*`、`os.fork`、`eval`、`exec`、`__import__`、`importlib.import_module` などを使わないこと | 構文の木に現れない書き方のすべて（検査の範囲は、試験に書いた形だけ） |
| 秘密情報の検査（`tools/secret_scan.py`） | 決まった形の鍵と、禁止したファイル名を見つけること。シンボリックリンク、gitlink、バイナリ、UTF-8 でないファイル、特殊なファイルを拒否すること。読めないときは失敗とすること | 決まった形に当てはまらない秘密情報。専用の道具と同じ網羅性 |

- **OS のレベルでのネットワークの隔離は、Phase 3A で確かめる**（ROADMAP の 0.2）。今の検査を合わせても、OS の全体の通信の遮断は保証しない。

## 構成

```
src/agent_core/
  types/             共通の型（ID、値、記録、エラー、書き出しと復元）。最も下の層
  ports/             接続口（Clock、RandomSource、IdRegistry、JournalPort、EvidenceVerifier、ModelPort、ReplayBlobStore、ReplayAccessPort）
  identity/          ID の生成
  runtime/           Task、Run、Step、Attempt の状態機械、実行のゲート、エージェントのループ、検証、復旧（recovery）
  context/           最小の Context Engine（Context IR、信頼の階層、ProtectedFact、予算）
  tool/              Tool Registry（オーナーが承認したツールの登録と、提案の検証）
  safety/            Safety Engine（PDP、ActionDescriptor、ExactApproval。入出力なしの判断）
  cost/              Cost Engine の規則（整数の計算、予約の状態遷移。入出力なし）
  observability/     構造化ログと、監査の書き込み
  adapters/          接続口の実装（システムの時計、OS の乱数、SQLite の Journal、OS のロック、合成の Fake Provider、合成の記録の Replay Provider、JSON Lines のログ、Journal と同じ DB の予算と権限の台帳、ファイルのブローカー）
src/agent_core_cli/  CLI（agent_core の外の合成の根。init、status、recovery、対話のセッション）
tests/support/       試験だけで使う、合成の偽の実装
tests/unit/          単体の試験（合成のデータだけ。Python の通信は試験の中で遮断する）
tools/secret_scan.py 秘密情報の検査（標準ライブラリだけ）
.github/workflows/   CI（手動の実行だけ）
docs/                設計資料、決定、審査の記録
```
