# WP2-06 Planning Engine v1（実装の記録）

> 文書の状態：**DRAFT**（審査待ち）　作成：2026-11-28　承認：D18

`agent_core/planning/planner.py`（純粋。Runtime・Adapter・台帳を import しない。層の契約に加えた）。

| 対象 | 実装 |
|---|---|
| 難易度 | 固定の規則（完了の条件が2つ以上、対象のファイルが3つ以上、宣言された部分が2つ以上なら complex） |
| simple | Phase 1 のループ（提案・実行・検証）の3段の計画 |
| complex | 依存のある Step の DAG（依存は既知、循環なし、最後は検証）。`plan_ref`（決定的なハッシュ）を Step の provenance に使う |
| 進捗 | `Progress`（完了、失敗）。準備のできていない Step の完了を拒否 |
| 計画の修正 | 上限付き：修正の回数、Step の数。完了した Step の変更・削除、完了の条件・タスクの変更、版の後退、残りの予算を超える修正を拒否 |
| 失敗の時の停止 | 失敗があり修正の残りがなければ `STOPPED` |

**権限：** 計画は提案だけ。Step を作る・Permit を出す・承認するのは Runtime の既存の経路（Policy、RunGrant、Permit、予算、完了の条件）で、この部品は呼ばない。

**試験：** `tests/unit/test_wp2_06_planning.py`（11件）。**未実装：** Agent のループへの組み込み（WP2-12 で評価の Run とともに）、実モデルによる計画の提案。
