# WP2-11 Task Catalog（dev）：実装の記録と、タスクの候補

> 文書の状態：**DRAFT**（審査待ち）　作成：2026-11-28　承認：D18・D19　正本：D（5章、6.1、6.3、16.2）、`PHASE_2_IMPLEMENTATION_PLAN.md` の WP2-11
> **候補の集合（CANDIDATE）である。正式な固定はオーナーの別の承認。** すべて合成のデータで作った。validation と holdout の本体と隠しテストは作っていない。取得も接続もしていない。タスクは実行していない（信頼しないコードの実行なし）。

## 1. 仕組み

| 部品 | 内容 |
|---|---|
| 型（`types/catalog.py`） | `SyntheticRepo`（小さな合成のリポジトリ。クラスタ）、`DevTask`（種類、難易度、必要な能力、目的、客観的な完了の条件、採点、始めの変更、出どころ、汚染の記録）。`DevTask.to_spec()` は WP2-01 の `TaskSpec`（参照だけ）を作る |
| 始めの作業ツリー | リポジトリのファイルに、タスクの始めの変更を重ねる（`start_worktree`、`worktree_hash`）。パスは相対で正規化したものだけ（`..`、絶対パス、`\` は拒否） |
| 版 | タスクとリポジトリに版を持たせる。内容か版が変われば `task_ref` も変わる |
| 採点の参照 | 採点の方法、採点のプログラムの ID と版、dev の隠しテストのハッシュ。隠しテストは固定のときに書くので、候補の段階では None |
| 汚染の記録 | 出どころ（SYNTHETIC、作成者、日付）と `contamination_notes` |
| 分割の重複の確認（D 6.3） | 指紋（`task_ref`、目的のハッシュ、リポジトリ、語の 3-gram のハッシュの集合）だけで比べる。判定する重なりは、同じタスク、同じ目的、似た文章（Jaccard ≥ 0.6、PROVISIONAL）、同じリポジトリ。タスクの文章を送らずに評価の側で照合できる |
| 固定（D.V8、D 16.2） | オーナーの鍵の MAC で manifest を固定する（`freeze`）。`verify` は定義から manifest を作り直し、違えば拒否する（目的、条件、採点の版、ファイルの書き換え）。実験のタスクは固定し確かめた集合からだけ取り出せる（`experiment_task_refs`） |
| 読み込み（`TaskCatalog.load(root, split, version)`） | `evaluation/dev/<版>/` の `repos.json`、`tasks.json`、固定の後は `frozen.json`。VALIDATION と HOLDOUT は拒否する（このリポジトリにない）。固定された集合は、オーナーの鍵がなければ読まない |

試験：`tests/unit/test_wp2_11_task_catalog.py`（13 件）。

## 2. タスクの候補（`evaluation/dev/0.1.0`、30 件、6つのリポジトリ）

難易度の目安（D 5.1）：S は1ファイル・数行・数手順、M は1〜2ファイル・数十行、L は複数のファイル・設計の判断・多くの手順。

| # | task_key | 種類 | 難易度 | リポジトリ | 採点の方法 | 必要な能力 | 完了の条件（件数） | 始めの変更 |
|---|---|---|---|---|---|---|---|---|
| 1 | `calc.divide_by_zero` | バグの修正 | S | calc | HIDDEN_TESTS（pytest_hidden@0.1.0） | code_reading, python | 3 | 0 ファイル |
| 2 | `textstats.multiple_spaces` | バグの修正 | M | textstats | HIDDEN_TESTS（pytest_hidden@0.1.0） | code_reading, edge_cases, python | 3 | 0 ファイル |
| 3 | `ledgerlite.self_transfer` | バグの修正 | L | ledgerlite | HIDDEN_TESTS（pytest_hidden@0.1.0） | accounting_rules, code_reading, python | 3 | 0 ファイル |
| 4 | `calc.median` | 小さな機能の追加 | S | calc | HIDDEN_TESTS（pytest_hidden@0.1.0） | python, spec_following | 4 | 0 ファイル |
| 5 | `inventory.low_stock` | 小さな機能の追加 | M | inventory | HIDDEN_TESTS（pytest_hidden@0.1.0） | api_design, python | 3 | 0 ファイル |
| 6 | `confparse.comments_and_types` | 小さな機能の追加 | L | confparse | HIDDEN_TESTS（pytest_hidden@0.1.0） | parsing, python, spec_following | 3 | 0 ファイル |
| 7 | `textstats.single_tokenizer` | リファクタリング | S | textstats | HIDDEN_TESTS（pytest_hidden@0.1.0） | python, refactoring | 3 | 0 ファイル |
| 8 | `taskqueue.heap` | リファクタリング | M | taskqueue | HIDDEN_TESTS（pytest_hidden@0.1.0） | algorithms, python, refactoring | 3 | 0 ファイル |
| 9 | `ledgerlite.cached_balances` | リファクタリング | L | ledgerlite | HIDDEN_TESTS（pytest_hidden@0.1.0） | performance, python, refactoring | 3 | 0 ファイル |
| 10 | `calc.rename_mean` | 複数のファイル | S | calc | FILE_STATE（file_predicates@0.1.0） | python, repository_navigation | 3 | 0 ファイル |
| 11 | `inventory.persistence` | 複数のファイル | M | inventory | HIDDEN_TESTS（pytest_hidden@0.1.0） | file_formats, python, repository_navigation | 3 | 0 ファイル |
| 12 | `taskqueue.retry_policy` | 複数のファイル | L | taskqueue | HIDDEN_TESTS（pytest_hidden@0.1.0） | design, python, repository_navigation | 4 | 0 ファイル |
| 13 | `confparse.tests_for_parse` | 試験の追加 | S | confparse | HIDDEN_TESTS（pytest_hidden@0.1.0） | python, testing | 3 | 0 ファイル |
| 14 | `inventory.tests_for_remove` | 試験の追加 | M | inventory | HIDDEN_TESTS（pytest_hidden@0.1.0） | python, testing | 3 | 0 ファイル |
| 15 | `ledgerlite.property_tests` | 試験の追加 | L | ledgerlite | HIDDEN_TESTS（pytest_hidden@0.1.0） | python, testing, testing_properties | 3 | 0 ファイル |
| 16 | `calc.answer_mean_of_empty` | 仕様の理解 | S | calc | ANSWER_MATCH（answer_exact@0.1.0） | code_reading | 2 | 0 ファイル |
| 17 | `taskqueue.answer_order` | 仕様の理解 | M | taskqueue | ANSWER_MATCH（answer_exact@0.1.0） | code_reading, reasoning | 2 | 0 ファイル |
| 18 | `confparse.answer_spec_gap` | 仕様の理解 | L | confparse | ANSWER_MATCH（answer_exact@0.1.0） | code_reading, requirements | 2 | 1 ファイル |
| 19 | `textstats.recover_failing_test` | エラーからの復旧 | S | textstats | HIDDEN_TESTS（pytest_hidden@0.1.0） | debugging, python | 3 | 1 ファイル |
| 20 | `inventory.recover_corrupt_snapshot` | エラーからの復旧 | M | inventory | HIDDEN_TESTS（pytest_hidden@0.1.0） | debugging, file_formats, python | 3 | 2 ファイル |
| 21 | `taskqueue.recover_after_interruption` | エラーからの復旧 | L | taskqueue | HIDDEN_TESTS（pytest_hidden@0.1.0） | debugging, python, state_recovery | 3 | 2 ファイル |
| 22 | `ledgerlite.long_history` | Context の管理 | S | ledgerlite | ANSWER_MATCH（answer_exact@0.1.0） | context_management, summarization | 2 | 1 ファイル |
| 23 | `confparse.many_files` | Context の管理 | M | confparse | HIDDEN_TESTS（pytest_hidden@0.1.0） | context_management, python, repository_navigation | 3 | 40 ファイル |
| 24 | `textstats.keep_decisions` | Context の管理 | L | textstats | FILE_STATE（file_predicates@0.1.0） | context_management, planning, python | 3 | 1 ファイル |
| 25 | `calc.plan_statistics_module` | Planning | S | calc | HIDDEN_TESTS（pytest_hidden@0.1.0） | planning, python | 3 | 0 ファイル |
| 26 | `inventory.plan_reservations` | Planning | M | inventory | HIDDEN_TESTS（pytest_hidden@0.1.0） | design, planning, python | 3 | 0 ファイル |
| 27 | `ledgerlite.plan_multi_currency` | Planning | L | ledgerlite | HIDDEN_TESTS（pytest_hidden@0.1.0） | design, planning, python | 3 | 0 ファイル |
| 28 | `confparse.no_secrets_in_logs` | 安全の条件の維持 | S | confparse | HIDDEN_TESTS（pytest_hidden@0.1.0） | python, safety | 3 | 0 ファイル |
| 29 | `textstats.stay_inside_worktree` | 安全の条件の維持 | M | textstats | TRACE_CHECK（journal_trace@0.1.0） | safety, tool_use | 3 | 1 ファイル |
| 30 | `taskqueue.ask_before_destructive` | 安全の条件の維持 | L | taskqueue | TRACE_CHECK（journal_trace@0.1.0） | safety, tool_use | 3 | 1 ファイル |

種類ごと: {'バグの修正': 3, '小さな機能の追加': 3, 'リファクタリング': 3, '複数のファイル': 3, '試験の追加': 3, '仕様の理解': 3, 'エラーからの復旧': 3, 'Context の管理': 3, 'Planning': 3, '安全の条件の維持': 3}
難易度ごと: {'S': 10, 'M': 10, 'L': 10}
リポジトリごと: {'calc': 5, 'textstats': 5, 'ledgerlite': 5, 'inventory': 5, 'confparse': 5, 'taskqueue': 5}
採点の方法ごと: {'HIDDEN_TESTS': 22, 'FILE_STATE': 2, 'ANSWER_MATCH': 4, 'TRACE_CHECK': 2}

**記録した点：** どのタスクも、次のものを持つ。
- 1つ以上の客観的な完了の条件。
- 採点の方法と、採点のプログラムの ID と版。
- 必要な能力。
- 汚染の記録。

**採点の方法：**
- HIDDEN_TESTS：エージェントが見ない試験。
- FILE_STATE：ファイルの最後の内容が、述べた条件を満たすか。
- ANSWER_MATCH：決まった答えと照合。
- TRACE_CHECK：Journal に、述べた操作がある、またはないか。

## 3. 固定の前に要ること（別の承認）

1. オーナーによる候補の確認と、固定する版の決定。
2. dev の隠しテストの作成（HIDDEN_TESTS の 22 件）と、そのハッシュの記録。
3. 採点のプログラム（`pytest_hidden`、`file_predicates`、`answer_exact`、`journal_trace`）の実装。WP2-09 で、範囲の外。
4. オーナーの鍵での `freeze` と、`frozen.json` の記録。
5. 評価の側での、validation の指紋との重複の確認。結果だけを受け取る。

## 4. 確かめていないこと・範囲の外

- 隠しテストと採点のプログラムの本体。
- タスクの実行と Sandbox の中での採点（SG-1 の前は信頼しないコードを実行しない）。
- Phase 1 の `trusted_synthetic_tasks` への接続（Runner の WP2-12 で）。
- 似た文章の閾値 0.6 の妥当性。
- 公開の課題と似ていないことの確認。一般的な練習の課題は公開されたものと似うるので、どれも dev だけに使う。
