# ADR-0006：ツールチェーン

- **状態：** Phase 1 の部分（Python 3.12、uv、Pydantic v2、sqlite3、pytest、ruff、mypy、import-linter、GitHub Actions の無料の枠）は **ACCEPTED**（`docs/decisions/PHASE_1_APPROVAL.md`）。Docker、FastAPI、Next.js は **PROPOSED**
- **日付：** 2026-11-26（WP-01 で、版とライセンスを記録した）

> 出典：独立審査者が確かめた、初回 Phase 0 報告書の予定（7件の ADR）。初回報告書の全文は、Claude Code の側では受け取っていない（A-P6 は、延期が承認された未完了の事項）。

## 決定

- Phase 1 で使うもの（ACCEPTED）：
  - Python 3.12、uv、Pydantic v2
  - sqlite3（標準ライブラリ。WAL）、pytest、ruff、mypy、import-linter
  - CI は GitHub Actions（無料の枠の中だけ）
- 将来の候補（PROPOSED。承認されていない）：
  - Docker（Phase 2）
  - FastAPI と Next.js（Phase 8）
- 依存関係を加えるときは、この ADR か、新しい ADR を更新し、オーナーの承認を得る。

## WP-01 で固定した版（`uv.lock`）

### 直接の依存関係

| パッケージ | 区分 | 指定の範囲（`pyproject.toml`） | 固定した版 | ライセンス | 加える理由 |
|---|---|---|---|---|---|
| pydantic | 実行時 | `>=2.12,<3` | 2.14.4 | MIT | データの検証（承認済み） |
| pytest | 開発時 | `>=9,<10` | 9.2.0 | MIT | 試験 |
| ruff | 開発時 | `>=0.14,<1` | 0.17.6 | MIT | Lint とフォーマット |
| mypy | 開発時 | `>=1.18,<2` | 1.20.2 | MIT | 型の検査 |
| import-linter | 開発時 | `>=2.5,<3` | 2.17 | BSD（パッケージの分類による） | 層の依存の方向の検査 |

### 推移的な依存関係（上の直接の依存関係が必要とするもの）

| パッケージ | 版 | ライセンス | 必要とするもの |
|---|---|---|---|
| annotated-types | 0.8.0 | MIT | pydantic |
| pydantic-core | 2.50.5 | MIT | pydantic |
| typing-extensions | 4.16.0 | PSF-2.0 | pydantic、mypy |
| typing-inspection | 0.4.5 | MIT | pydantic |
| click | 8.5.1 | BSD-3-Clause | import-linter |
| grimp | 3.20 | BSD（パッケージの分類による） | import-linter |
| rich | 15.0.0 | MIT | import-linter |
| markdown-it-py | 4.3.0 | MIT | rich |
| mdurl | 0.1.2 | MIT | markdown-it-py |
| librt | 0.17.0 | MIT | mypy |
| mypy-extensions | 1.2.0 | MIT | mypy |
| pathspec | 1.1.1 | MPL-2.0 | mypy |
| iniconfig | 2.3.1 | MIT | pytest |
| packaging | 26.4 | Apache-2.0 OR BSD-2-Clause | pytest |
| pluggy | 1.7.0 | MIT | pytest |
| pygments | 2.21.0 | BSD-2-Clause | pytest、rich |
| colorama | 0.4.6 | （Windows だけで入る。Linux では入らないため、ライセンスの表記を確かめていない） | pytest（`sys_platform == 'win32'` のときだけ） |

- ライセンスは、入れたパッケージのメタデータ（`License-Expression` か、分類の表記）から読んだ。法的な確認はしていない。
- pathspec は MPL-2.0（ファイル単位のコピーレフト）である。開発時だけに使い、配布物には含めない。

### そのほかの道具

| 道具 | 版 | 確認 |
|---|---|---|
| Python | 3.12.3（開発の環境。`/usr/bin/python3.12`） | `.python-version` は `3.12`。`requires-python = ">=3.12,<3.13"` |
| uv | 0.11.32（開発の環境）。CI でも同じ版を PyPI から入れる | `required-version = ">=0.11"` |
| SQLite | 3.45.1（Python 3.12 の `sqlite3`） | WAL が使えることを試験で確かめた |

## 構成の判断

- **ビルドの仕組みを使わない**（`[tool.uv] package = false`）。ビルドの仕組み（例：hatchling、uv_build）は、承認された依存関係に含まれないため。
  - そのため、`src/` は、各道具の設定で読み込む（pytest の `pythonpath`、mypy の `mypy_path`、import-linter の実行のときの `PYTHONPATH=src`）。
  - 配布用のパッケージを作るときは、ビルドの仕組みを、ADR とオーナーの承認で加える。
- **秘密情報の検査は、標準ライブラリだけで書いた** `tools/secret_scan.py` で行う。専用の道具（例：gitleaks）は、承認されていないため使わない。
- **CI は、GitHub 自身の Action**（`actions/checkout`、`actions/setup-python`）だけを使う。uv は、版を固定して PyPI から入れる。

## 互換性の確認（WP-01 で、実際に確かめたこと）

- 上の版の組み合わせで、`uv lock` が解決でき、`uv sync --locked` で入った。
- Python 3.12.3 の上で、ruff、mypy（strict）、import-linter、pytest が、すべて成功した。
- **確かめていないこと：** GitHub Actions の上での実行（CI は、まだ動かしていない）、Windows、Python 3.12 のほかの小さな版。

## 理由

- 初回報告書の推奨と、オーナーの承認に従う。

## 参照

PHASE_1_IMPLEMENTATION_PLAN の0.1、`docs/decisions/PHASE_1_APPROVAL.md`
