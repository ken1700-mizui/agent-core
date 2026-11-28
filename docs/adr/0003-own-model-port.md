# ADR-0003：独自の ModelPort

- **状態：** PROPOSED
- **日付：** 2026-11-26

> 出典：独立審査者が確かめた、初回 Phase 0 報告書の予定（7件の ADR）。初回報告書の全文は、Claude Code の側では受け取っていない（A-P6 は未完了）。

## 決定

- モデルとのやり取りは、独自の共通の型（Message、ContentBlock、ModelRequest、ModelResponse、Usage、ModelProfile）と、ModelPort で行う。
- 初期は Fake と Replay だけ。
- 将来、公式の SDK と、HTTP の Adapter を加える。

## 理由

- プロバイダーに縛られずに、同じ条件で比べられる。

## 影響と、注意すること

- 各プロバイダーの仕様は、確かめていない。

## 参照

E の7.4・31章、B1 の2.4
