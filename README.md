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

依存関係を取得した後は、`UV_OFFLINE=1` で、ネットワークを使わずに動かす。

```bash
python3 -I tools/secret_scan.py
```

```bash
uv lock --check
```

```bash
UV_OFFLINE=1 uv sync --locked --check
```

```bash
UV_OFFLINE=1 uv run ruff check .
```

```bash
UV_OFFLINE=1 uv run ruff format --check .
```

```bash
UV_OFFLINE=1 uv run mypy
```

```bash
UV_OFFLINE=1 PYTHONPATH=src uv run lint-imports
```

```bash
UV_OFFLINE=1 uv run pytest
```

- 秘密情報の検査は、コミットの前にも必ず動かす（`AGENTS.md` の 4.9）。
- ビルドの仕組みは使っていない（`docs/adr/0006-toolchain.md`）。そのため、import-linter には `PYTHONPATH=src` が必要である。

## 構成

```
src/agent_core/      Agent Core の本体（Engine のモジュールは、それぞれの WP で加える）
tests/unit/          単体の試験（合成のデータだけ。ネットワークは試験の中で遮断する）
tools/secret_scan.py 秘密情報の検査（標準ライブラリだけ）
.github/workflows/   CI（pull request と手動の実行だけ）
docs/                設計資料、決定、審査の記録
```
