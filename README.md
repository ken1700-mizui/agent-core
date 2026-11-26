# Agent Core

> 文書の状態：**DRAFT**（WP-01）

Agent Core は、オーナー自身が使うための、専用の AI エージェントの基盤である。商用の製品ではない。

- 進捗の正本：`docs/STATUS.md`
- 開発の決まり（AI 向け、英語）：`AGENTS.md`
- Phase 1 の承認：`docs/decisions/PHASE_1_APPROVAL.md`
- Phase 1 の計画：`docs/design/phase-0/PHASE_1_IMPLEMENTATION_PLAN.md`

## 今の状態

- Phase 1 の WP-01（プロジェクトの構成と CI）だけを実装した。Engine の機能は、まだない。
- Phase 1 は、合成のデータだけを使う。実 API、認証情報、外部への通信は使わない（費用は 0 ドル）。

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
src/agent_core/      Agent Core の本体（Engine のモジュールは、それぞれの WP で加える）
tests/unit/          単体の試験（合成のデータだけ。Python の通信は試験の中で遮断する）
tools/secret_scan.py 秘密情報の検査（標準ライブラリだけ）
.github/workflows/   CI（手動の実行だけ）
docs/                設計資料、決定、審査の記録
```
