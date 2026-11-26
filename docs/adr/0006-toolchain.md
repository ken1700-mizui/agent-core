# ADR-0006：ツールチェーン

- **状態：** PROPOSED（オーナーの承認を待っている）
- **日付：** 2026-11-26

> 出典：独立審査者が確かめた、初回 Phase 0 報告書の予定（7件の ADR）。初回報告書の全文は、Claude Code の側では受け取っていない（A-P6 は未完了）。

## 決定

- 候補は、次のとおり。
  - Python 3.12 以上、uv、Pydantic v2
  - sqlite3、pytest、ruff、mypy、import-linter
  - Docker（Phase 2）
  - FastAPI と Next.js（Phase 8）
- 依存関係を加えるときは、この ADR か、新しい ADR を更新する。

## 理由

- 初回報告書の推奨に従う。

## 影響と、注意すること

- 版の指定と、互いに合うかどうかは、確かめていない。

## 参照

PHASE_1_IMPLEMENTATION_PLAN の0.1
