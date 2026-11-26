# WP-07：Tool Registry（実装の記録）

> 文書の状態：**PROVISIONAL**（レビューは 95/100 の条件付き合格。WP07-R01・R02 を修正した。10章）　作成日：2026-11-26
>
> - この文書は、WP-07 で実装した内容と、**設計資料に書かれていなかったために、WP-07 で決めたこと**を記録する。
> - 正本の規則は `E_INTEGRATION.md` の28章に従う。この文書は、設計資料を上書きしない。
> - 実装：`src/agent_core/types/tools.py`（型）、`src/agent_core/tool/registry.py`（登録簿）。

## 1. 構成

```
agent_core.runtime
        ↓
agent_core.adapters | agent_core.identity | agent_core.tool    （互いに import しない）
        ↓
agent_core.ports → agent_core.types
```

- `agent_core.tool`（Tool Engine）は、`types` だけに依存する。`runtime`、`adapters`、SQLite を import できないことを、import-linter の契約と試験で確かめた。
- 依存関係は加えていない。スキーマの検証は、Pydantic と標準ライブラリだけで行う。

## 2. 型（C1 の5.1）

| 型 | 内容 |
|---|---|
| `ToolId` | 小文字の識別子（`read_file` など） |
| `ToolVersion` | `major.minor.patch` |
| `ToolKind` | `BUILTIN`、`SANDBOXED_COMMAND`、`MCP`、`MODEL_CALL`。**Phase 1 で登録できるのは `BUILTIN` だけ** |
| `ToolEffectClass` | `READ_ONLY`、`IDEMPOTENT`、`COMPENSATABLE`、`IRREVERSIBLE`、`MODEL_CALL`（A の3.2） |
| `ExternalGuarantee` | `NONE`、`IDEMPOTENCY_KEY`、`CONDITIONAL`（A08） |
| `ToolManifestBody` | C1 の5.1 の、すべての項目（`tool_id`、`version`、`kind`、`input_schema`、`output_schema`、`effect_class`、`external_guarantee`、`required_permissions`、`resources`、`destinations`、`timeout_ms`、`max_output_bytes`、`cost_estimate`、`approval_conditions`、`sandbox_profile`） |
| `ToolManifest` | `ToolManifestBody` ＋ `manifest_hash`。検証のたびに計算し直して、一致しなければ拒否する（改ざんの検出） |
| `ToolRegistration` | 受け付けた `ToolManifest`、`artifact_hash`、オーナーの承認の記録への参照 |
| `ToolProposal` | 検証した、ツールの呼び出しの提案。**Permit ではない** |
| `ObjectSchema`、`FieldSpec` | 入力と出力のスキーマ（4章） |
| 型付きのエラー | `UnknownToolError`、`UnknownToolVersionError`、`ToolNotApprovedError`、`ToolAlreadyRegisteredError`、`ToolKindNotAllowedError`、`ToolInputError`、`ToolOutputError` |

**記録の段階での制約**

- `READ_ONLY` のツールは、書き込みの資源も `FILE_WRITE` の権限も宣言できない。
- `resources` のパスのパターンは、相対のパスだけ（`..` と絶対のパスを拒否する）。
- 並べる項目（権限、承認の条件、接続先）は、並べた順で、重複なし。

## 3. 登録と固定（C1 の5.3、C1.S8、C1.S23）

- **信頼する登録の情報：** `TrustedToolCatalog` は、オーナーが管理する、承認済みの `(tool_id, version, manifest_hash, artifact_hash, approval_ref)` の一覧である。
  - 1つの ID と版には、1つの項目だけ。
  - 登録簿は、この一覧に項目を加えられない。
  - Phase 1 では、オーナーが設定として与える。Policy Store と承認の連鎖は、後の段階で加える。
- **登録の条件**（`register`）：次のすべてを満たすときだけ、受け付ける。
  1. 封をした `ToolManifest` であること（`manifest_hash` を計算し直して確かめる）。
  2. Phase 1 で許すものだけであること：`BUILTIN` だけ。接続先（`destinations`）なし。`sandbox_profile` なし。
  3. 一覧に、同じ ID と版の項目があり、`manifest_hash` と `artifact_hash` の両方が一致すること。
- **呼び出し側やモデルが宣言したハッシュだけでは、登録されない。** 正しく封をした変更でも、オーナーの一覧と一致しなければ拒否する（審査していない変更を、自動で受け入れない）。
- **上書きの禁止：** 登録済みの ID と版に、違う内容を登録しようとすると拒否する。同じ内容なら、すでにある登録を返す。
- **`manifest_hash` と `artifact_hash` の区別：** 宣言の固定と、実行するコードの固定は、別の値である。
- **外部の保証：** 宣言はそのまま記録する。ただし Phase 1 では保証を確かめられないので、実際の扱い（`effective_external_guarantee`）は、いつも `NONE` とする（C1 の5.2）。
- **実行物の固定を、今どこまで確かめられるか：** 確かめられるのは、`artifact_hash` がオーナーの一覧の値と一致することだけ。実際に動くコードがそのハッシュを持つことは、実行するときに PEP が照合する（WP-08、WP-09、C1 の5.3）。**WP-07 では確かめていない。**

## 4. 入力と出力の検証

- **スキーマ：** 型の付いた欄（`STRING`、`INTEGER`、`BOOLEAN`）を持つ、JSON のオブジェクト。
  - 文字列には長さの上限が必須。整数には範囲を付けられる。全体には大きさ（バイト数）の上限がある。
  - スキーマの型は閉じているので、表せない機能（JSON Schema の任意の機能）は、検証で拒否される。黙って無視することはない。
- **検証**（`propose` の入力、`validate_output` の出力）：
  - 厳密な JSON の検査（`parse_strict_json`）：重複したキー、非有限の数、深すぎる入れ子などを拒否する。
  - オブジェクトであること。
  - 宣言していない欄を拒否する。
  - 必須の欄があること。
  - 型を厳密に確かめる（`true` を整数としない、`1.5` を整数としない）。
  - 長さ、範囲、大きさの上限を確かめる。出力は、`max_output_bytes` も確かめる。
- **エラー：** 場所（宣言した欄の名前か `<unknown>`）と種類だけを持つ。入力の名前や値は入れない。元の例外もつながない。
- **正規化とハッシュ：** 鍵を並べた、空白のない JSON にする。入力と出力のハッシュは、それぞれ別のドメインのタグを付けた SHA-256。**合成のデータだけに使う**（WP05-R03 と同じ。実データでは HMAC が必要）。

## 5. 提案は Permit ではない

- `propose` が返す `ToolProposal` には、次のものが入る。どれも**データ**である。
  - ID と版、`manifest_hash`、`artifact_hash`、効果の分類。
  - 実際の外部の保証（`NONE`）。
  - 承認が要るかどうか（`approval_conditions` と効果の分類から決める）。
  - 正規化した入力と、そのハッシュ。
- 提案には、Permit も、実行の方法もない。ツールの選択から、直接実行することはない。実行には、WP-09 の Safety と Permit が必要になる。
- 登録簿は、実行できる関数を一切持たない（試験で確かめた）。モデルが関数を取り出せる構成ではない。
- モデルの `ToolUseBlock` は、登録も認可もしない。登録していないツールの名前は拒否される。登録済みのツールでも、得られるのは提案だけである。
- 実際のファイル操作や、任意のコマンドの実行は、実装していない（WP-08 以降）。

## 6. 設計資料との対応

| 設計 | 実装 |
|---|---|
| C1 の5.1（宣言する項目） | `ToolManifestBody` |
| C1 の5.2（宣言を信じず、実行するときに強制する。外部の保証は確かめるまで `none`） | 入力と出力の検証、`effective_external_guarantee`。実行するときの強制（マウント、時間の上限など）は WP-08、WP-09 |
| C1 の5.3（マニフェストの固定と、実行コードの固定を分ける。組で登録し、変われば再審査） | 3章 |
| C1.S8（登録していないツール、版が固定されていないツールの拒否） | `UnknownToolError`、`UnknownToolVersionError` |
| C1.S23（どちらかのハッシュが変われば、再審査するまで実行しない） | 一覧と一致しない組を拒否する |
| PHASE_1 の WP-07（`builtin` だけ、選択は提案だけ） | 3・5章 |

## 7. 設計資料にない判断（WP-07 で決めたこと）

| 判断 | 理由 |
|---|---|
| スキーマを、閉じた小さな型（3種類の欄）にした | 完全な JSON Schema の処理系を作らず、表せない機能を確実に拒否するため |
| `manifest_hash` は、ドメインのタグ（`agent-core/tool-manifest/v1`）付きの、正規化した JSON の SHA-256 | 設計が計算の方法を決めていない |
| 同じ内容の再登録は、すでにある登録を返す | 上書きではないため |
| `ToolPermission` は `FILE_READ` と `FILE_WRITE` だけ | Phase 1 で要るものだけ |
| 承認が要る条件の決め方（`ALWAYS`、書き込みのとき、取り消せないとき） | `approval_conditions` の値の意味を決めた |

## 8. 検証の結果

- **TDD：** 試験を先に書き、モジュールがない状態で失敗することを確かめてから、実装した。
- WP-07 で 42 件と、import-linter の契約の試験 3 件を加えた。8種類の品質の検査が、すべて成功した。

## 10. レビューの指摘への対応（WP07-R01、R02）

この章は、2〜5章の記述より優先する。

### 10.1 JSON を解析する前の大きさの制限（WP07-R01）

- 解析の前に、生の入力の大きさを確かめる。
  - 上限は、正規化した後の上限の **2 倍**（`RAW_SIZE_FACTOR`）。空白などの余分を許す分である。
  - まず文字数で確かめる（安く確かめられ、文字数はバイト数を超えない）。次に UTF-8 のバイト数で確かめる。
  - 超えたら、解析せずに拒否する（試験で、解析の関数が呼ばれないことを確かめた）。
- 正規化した後の UTF-8 のバイト数の上限も、そのまま保つ。出力では、`max_output_bytes` との小さい方を上限にする。
- **文字数とバイト数を区別する：** `max_length` は Unicode の文字数を数える。大きさの上限は、UTF-8 のバイト数を数える。
- 型付きのエラーにするもの：
  - 文字列でない入力（`string_required`）。
  - UTF-8 にできない文字列（対になっていないサロゲート。`json_invalid_string`）。
  - 64 ビットを超える整数（`int_out_of_range`）。
- エラーに、未知の欄の名前や本文は出さない（変えていない）。

### 10.2 登録と権限の整合（WP07-R02）

- **宣言の整合を、両方の向きで確かめる。**
  - `WRITE` の資源があること ⇔ `FILE_WRITE` の権限があること。
  - `READ` の資源があること ⇔ `FILE_READ` の権限があること。
- `READ_ONLY` のツールは、書き込みを宣言できない。
- **効果の分類：** `MODEL_CALL` の効果は、`MODEL_CALL` の種類のツールだけが持てる。
  - `builtin` として登録できる効果の分類は、`READ_ONLY`、`IDEMPOTENT`、`COMPENSATABLE`、`IRREVERSIBLE`。
- **`ON_WRITE` の判定：** 書き込みの宣言（`FILE_WRITE`）があるときだけ、承認が要るとする。以前は、`READ_ONLY` でない効果をすべて書き込みとみなしていた。
- **再検証：**
  - 信頼する一覧（カタログ）は、登録簿を作るときに検証し直す。検証を通さずに作られた一覧は信用しない。
  - マニフェストは、登録のたびに検証し直す。
  - 不正なものは、型付きのエラー（`ToolManifestError`）で拒否する。
- `requires_approval` は、宣言から導いた提案であり、最終的な認可の判定ではない（変えていない。判定は WP-09 の PDP）。
- 実際の実行物のハッシュの照合、Policy Store、オーナーの承認の連鎖は、WP-08 と WP-09 に残した。

## 9. 確かめていないこと

- 実際に動くコードと `artifact_hash` の照合（WP-08、WP-09 の PEP）。
- Policy Store と、オーナーの承認の連鎖（後の段階）。
- 実行するときの強制（マウント、時間の上限、出力の切り捨て）（WP-08、WP-09）。
