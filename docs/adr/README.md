# 設計判断の記録（ADR）

> 人間向けの文書（日本語）。各 ADR は短い記録で、詳細は参照先の設計資料にある。

| ADR | 内容 | 状態 |
|---|---|---|
| [0001](0001-modular-monolith-ports-adapters.md) | モジュラーモノリスと、Ports & Adapters | ACCEPTED（Phase 1 の承認） |
| [0002](0002-sqlite-event-log.md) | SQLite とイベントログ | 一部 ACCEPTED（SQLite の WAL） |
| [0003](0003-own-model-port.md) | 独自の ModelPort | PROPOSED |
| [0004](0004-code-enforced-safety-sandbox.md) | コードで強制する Safety と Sandbox | PROPOSED |
| [0005](0005-eval-driven-holdout-separation.md) | 評価駆動の開発と、holdout の分離 | 一部 ACCEPTED（D3） |
| [0006](0006-toolchain.md) | ツールチェーン | 一部 ACCEPTED（Phase 1 の部分） |
| [0007](0007-documentation-language.md) | 文書の言語の方針 | ACCEPTED（D4） |

**状態の意味**

| 状態 | 意味 |
|---|---|
| PROPOSED | 提案した。オーナーの承認を待っている |
| ACCEPTED | オーナーが承認した、または、オーナーの確定事項にもとづく |
