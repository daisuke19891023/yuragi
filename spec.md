# yuragi 最終仕様書

以下は、これまでの検討をすべて踏まえ、FastMCP（stdio）対応・expose 切替の factory パターン・pyright 前提を反映した **最終版の仕様書＋タスク（DoD/Acceptance 付き）**です。
LLM 側は OpenAI Python SDK / OpenAI Agents SDK を使用し、構造化出力は Pydantic v2 のスキーマをそのまま適用します（Structured Outputs）。Agents SDK の実行ループ／ツール実行・型付き出力／トレーシングは公式リファレンス準拠です。
MCP 側は FastMCP を採用し、STDIO がデフォルトのトランスポートで動作するサーバを interfases/mcp に実装します。

---

📦 プロジェクト名：yuragi

LLM（Structured Output）＋ Tool Call で「曖昧な痕跡」を検証付きの依存グラフへ正規化する Python ライブラリ
Expose：CLI / FastMCP(stdio) を factory で切替

---

## 1. 目的・スコープ

目的：コード／設定／ログなど出自が多様で“揺らぎ”のある情報を、LLM 構造化出力（JSON Schema 準拠）＋検証ツール（repo/DB/spec/runtime）で Node/Edge/Evidence/Confidence を持つ依存グラフに正規化する。Structured Outputs はスキーマに厳密準拠するため、下流の自動処理に強い。

入出力：

- 入力：コード断片、AST/semgrep JSON、OpenAPI/GraphQL/Proto diff、DB 統計、grep ヒット等
- 出力：Pydantic モデル準拠の Graph JSON（strict schema）

公開形態：

1. Python ライブラリ API
2. CLI（yuragi）
3. FastMCP サーバ（stdio）：interfases/mcp 下に実装（MCP tools として呼び出し可能）。STDIO が既定。

型検査：pyright（strict） を標準。

外部ツール：ripgrep 等は外部から注入可能（アダプタ設計）。

---

## 2. アーキテクチャ

```
yuragi/
  core/
    models.py         # Pydantic: Node/Edge/Evidence/CRUDAction/Graph/Enums
    schema.py         # JSON Schema 出力・検証・schema_version
    scoring.py        # confidence ルール
    errors.py
  llm/
    client.py         # OpenAI SDK ラッパ（Responses/ChatCompletions）
    prompts.py        # few-shot/system 指示
    structured.py     # Pydantic schema を使う構造化出力ヘルパ
  tools/
    repo.py           # 検索アダプタ: CLI/HTTP/Callable をプラガブルに
    db.py             # introspect/explain（抽象 IF、PG 実装）
    specs.py          # oasdiff / buf / graphql-inspector 取込
    runtime.py        # OTel / pg_stat_statements 等の実行時根拠
    gateway_iac.py    # Kong / Terraform plan JSON 等
  agents/
    normalize_agent.py  # 揺らぎ吸収（構造化候補）
    verify_agent.py     # 裏取り（tool 呼出）
    orchestrator.py     # handoff/リトライ/ガードレール（Agents Runner）
  pipelines/
    crud_normalize.py   # 代表: CRUD→検証→グラフ合成
  io/
    sinks.py            # JSON/NDJSON/Neo4j など
  interfases/
    factory.py          # expose 切替（CLI/MCP）Factory（← 注：綴りは要件どおり）
    cli/
      app.py            # CLI 実装（argparse/typer は任意）
    mcp/
      server_fastmcp.py # FastMCP(stdio) サーバ（tools 定義）
```

Agents SDK：Agent(output_type=...) と Runner.run(...) を用い、型付き最終出力・ツール実行ループ・handoff を実装。トレーシングは SDK が自動収集し、各種ダッシュボードへ拡張可能。

Structured Outputs：Pydantic model_json_schema() を response_format（json_schema） に渡して厳格に制約（“JSON モード”より強い保証）。

FastMCP：FastMCP の @mcp.tool() でツール登録し、run() で STDIO サーバ起動。

---

## 3. データモデル（Pydantic v2）

列挙

- NodeType: Service | APIEndpoint | DBTable | DBColumn | Topic | CacheKeyPattern | GatewayRoute | IaCResource | BuildTarget | ...
- EdgeType: READS | WRITES | CALLS | PUBLISHES | CONSUMES | ROUTES_TO | DEPENDS_ON | ...

Evidence

- type: Literal["code","spec","log","trace","config"]
- locator: str                # 例: "src/x/y.py:L120-140" / "trace:abcd..." / "resource:id"
- snippet: str | None
- source_tool: str | None     # "rg" / "pg.explain" / "oasdiff" 等

Node / Edge / Graph（抜粋）

- Node = { id: str, type: NodeType, name: str, attrs: dict[str, Any] }
- Edge = { from_id: str, to_id: str, type: EdgeType, evidence: list[Evidence], confidence: float }  # 0..1
- Graph = { nodes: list[Node], edges: list[Edge], schema_version: str }

CRUDAction（Normalize 用）

```
CRUDAction = {
  service: str, table: str, action: Literal["INSERT","UPDATE","DELETE","SELECT"],
  columns: list[str], where_keys: list[str],
  code_locations: list[{path:str, span:str}], confidence: float
}
```

> 注：Pydantic スキーマをそのまま Structured Outputs に渡す（JSON Schema 厳格準拠）。

---

## 4. LLM & ツール呼び出し方針

LLM の役割：

1. ノーマライザー（曖昧痕跡 → 構造化候補）
2. オーケストレーター（不足情報をツールで裏取り）
3. サマライザー（差分の影響を根拠付きで要約）

実行モデル（Agents SDK）：Runner.run(start_agent, input) が最終出力までループ実行。途中で handoff／tool call を繰り返す。

構造化出力：Agent(output_type=MyPydanticModel) で型付き終端を強制。SDK 側が JSON Schema を生成・検証する。

ツール：Agents SDK の function tools（独自関数ラップ）で検証用アダプタを呼ぶ設計が基本。

---

## 5. 外部ツール取り込み（ripgrep 等）

`tools/repo.py` にアダプタ層を用意。

- CLIAdapter：command, args, env, timeout を受けて stdout を JSON 正規化（例：rg -n --json）。
- HTTPAdapter：社内検索 API 等に対応。
- CallableAdapter：Python 関数を注入（テストダブル／独自実装）。
  - → すべて 共通ヒット型（path, line, context_before/after, confidence_hint?）で返却。

セキュリティ：CLI 実行は allowlist／引数検証／timeout を必須化。

---

## 6. 代表パイプライン（CRUD 正規化）

1. 入力：コード断片／AST/semgrep JSON
2. Normalize Agent：CRUDAction[] を Structured Outputs で生成
3. Verify Agent：repo.search, db.introspect/explain 等を直列で呼び、Evidence を蓄積
4. scoring：静的＋実行時の異種根拠が揃えば confidence 加点
5. Graph 合成：APIEndpoint → Service → DBTable の WRITES/READS などを生成
6. 出力：strict 準拠の Graph JSON

> Agents SDK のトレーシングで実行経路を可視化（SDK が自動サポート）。

---

## 7. interfases（expose 切替）＋ Factory パターン

### 7.1 目的

ライブラリ本体は I/O 非依存。Expose（CLI / MCP）は interfases/ で提供し、Factory で選択起動。

### 7.2 構成

```
interfases/
  factory.py
  cli/app.py
  mcp/server_fastmcp.py
```

### 7.3 共通プロトコル

```
# interfases/types.py
from typing import Protocol, Mapping, Any

class Exposure(Protocol):
    def serve(self, *, config: Mapping[str, Any] | None = None) -> None: ...
```

### 7.4 Factory

```
# interfases/factory.py
from .cli.app import CLIExposure
from .mcp.server_fastmcp import MCPExposure

def make_exposure(kind: str) -> "Exposure":
    if kind == "cli":
        return CLIExposure()
    if kind == "mcp":
        return MCPExposure()  # FastMCP(stdio)
    raise ValueError(f"unknown exposure kind: {kind}")
```

既定選択：YURAGI_EXPOSE 環境変数（cli/mcp）。指定なければ cli。

### 7.5 CLI 側（interfases/cli/app.py）

yuragi normalize ... / yuragi schema export / yuragi run-crud-pipeline

Exposure.serve() は 引数解析→ pipelines 呼び出し → JSON 出力に限定（ロジックは pipelines 側）。

### 7.6 FastMCP（interfases/mcp/server_fastmcp.py）

実装：

```
from fastmcp import FastMCP
mcp = FastMCP("yuragi")

@mcp.tool()
def yuragi_normalize_crud(code_snippets: list[CodeSnippet], hints: dict|None=None) -> CRUDActionList: ...

@mcp.tool()
def yuragi_verify_crud(crud: CRUDActionList, repo_opts: dict|None=None, db_opts: dict|None=None) -> Graph: ...

@mcp.tool()
def yuragi_run_crud_pipeline(code_snippets: list[CodeSnippet], repo_opts: dict|None=None, db_opts: dict|None=None) -> Graph: ...

# ほか spec_impact / merge_graphs も同様に
mcp.run()  # STDIO 既定
```

FastMCP は STDIO がデフォルト。MCP Host（例：Claude Desktop 等）から tools/list → tools/call で利用可能。

ツールの入出力スキーマは **型ヒント（Pydantic / TypedDict）** から FastMCP が組み立て。

---

## 8. 設定・環境変数

- OPENAI_API_KEY, OPENAI_BASE_URL?（必要に応じ）
- YURAGI_EXPOSE=cli|mcp（Factory 選択）
- YURAGI_REPO_ALLOW_CMDS="rg,git,..."（CLI allowlist）
- YURAGI_DB_DSN, YURAGI_TOOL_TIMEOUT_MS, YURAGI_CONFIDENCE_THRESHOLD など

---

## 9. 非機能要件

- Python 3.10+
- 型検査：pyright（strict） で 0 エラー
- 再現性：LLM 出力は Structured Outputs でスキーマ厳格。逸脱時は自動リトライして明示エラーで返却。
- 監査性：すべての Edge に Evidence ≥1。
- 観測性：Agents SDK のトレースが可視（ダッシュボード対応）。
- セキュリティ：外部 CLI は allowlist＋引数正規化＋timeout＋作業ディレクトリ sandbox

---

## 10. 代表 API（Python / CLI）

Python

```
from yuragi.pipelines.crud_normalize import normalize_crud_from_code

graph = normalize_crud_from_code(
  code_snippets=[{"language":"java","content":"...", "path":"src/.../Repo.java"}],
  repo_search_opts={"adapter":"cli","command":"rg","args":["-n","--json"]},
  db_verify_opts={"dsn":"postgresql://...","verify_explain":True},
)
```

CLI

```
yuragi normalize \
  --in code_snippets.json \
  --repo-adapter cli --repo-cmd rg --repo-args "-n --json" \
  --db-dsn "postgresql://user:pass@localhost:5432/app" \
  --out graph.json
```

MCP（FastMCP）

Host 側から tools/list → tools/call("yuragi_run_crud_pipeline", {...})

STDIO で起動（mcp.run() 既定）。

---

## 11. タスク一覧（Definition of Done / Acceptance Criteria）

> 既存の pyproject.toml／noxfile.py／GitHub Actions 雛形は 流用します。ここでは本プロジェクト固有のタスクのみ。

T0. 依存・pyright 準備

- 内容：openai, openai-agents, pydantic>=2, httpx, tenacity, rich, pyright, pytest, jsonschema を追加。pyrightconfig.json(typeCheckingMode:"strict") を配置。
- DoD：ローカルで pyright & pytest -q が成功。
- Acceptance：CI（雛形）で pyright 0 エラー・テスト緑。

T1. Pydantic ドメインモデル（core/models.py）

- 内容：Node/Edge/Evidence/CRUDAction/Graph と NodeType/EdgeType を定義。
- DoD：model_json_schema() の出力が JSON Schema 検証を通過。
- Acceptance：サンプル Graph（10 ノード/20 エッジ）が round-trip 復元可。

T2. スキーマユーティリティ（core/schema.py）

- 内容：Schema エクスポート、schema_version 埋め込み、破壊変更差分の検知。
- DoD：フィールド追加/削除/型変更の検知テスト。
- Acceptance：yuragi schema export > schema.json がそのまま Structured Outputs に渡せる（OpenAI SDK で使用可）。

T3. LLM クライアント & 構造化出力（llm/client.py, llm/structured.py）

- 内容：OpenAI SDK ラッパ（再試行・タイムアウト・ログ）。Pydantic スキーマを response_format=json_schema で指定し、strictな構造化出力を取得。
- DoD：録画テストで 3 ケース（文字列結合 SQL／ORM／テンプレ）を安定パース。
- Acceptance：すべての応答がスキーマ完全準拠し、LLM 逸脱時は自動リトライ後に明示エラー。

T4. 信頼度スコア（core/scoring.py）

- 内容：静的(+0.3)/実行時(+0.3)/複数ツール合意(+0.2)/名称衝突(-0.2) などのルール。
- DoD：5 パターンのユニットテスト。
- Acceptance：confidence>=0.7 のみ “確証済み” フラグが立つ。

T5. リポジトリアダプタ（tools/repo.py）

- 内容：CLIAdapter（例：ripgrep）、HTTPAdapter、CallableAdapter。共通ヒット型に正規化。
- DoD：モックで 3 アダプタの I/O を検証。
- Acceptance：外部 rg を注入でき、曖昧候補からの再検索で実際のヒットを返す。

T6. DB 検証（tools/db.py）

- 内容：introspect_table/columns, explain(sql) の抽象 I/F（PostgreSQL 実装）。
- DoD：ローカル PG（docker）で e2e テスト。
- Acceptance：存在しないテーブルでは negative result を返し、confidence が下がる。

T7. 仕様差分取り込み（tools/specs.py）

- 内容：oasdiff/buf/graphql-inspector 出力のパースと正規化。
- DoD：3 形式をパースできる。
- Acceptance：破壊変更で CALLS 影響エッジが生成される。

T8. 実行時根拠（tools/runtime.py）

- 内容：pg_stat_statements／OTel ライク JSON → READS/WRITES/CALLS の実在フラグ。
- DoD：変換テスト。
- Acceptance：静的+実行時の合流で confidence が上がる。

T9. Normalize Agent（agents/normalize_agent.py）

- 内容：Agent(output_type=CRUDActionList) を定義。few-shot と用語辞書で曖昧入力を吸収。
- DoD：3 種の曖昧入力で CRUDAction を最低 1 件以上生成。
- Acceptance：Agents のトレースで手順が可視（SDK のトレーシング確認）。

T10. Verify Agent（agents/verify_agent.py）

- 内容：repo/db を直列で呼び、Evidence を集約し confidence 更新。
- DoD：偽陽性候補が検証で除外される。
- Acceptance：重要エッジが confidence>=0.7 に到達。

T11. オーケストレータ（agents/orchestrator.py）

- 内容：Normalize→Verify の handoff、再試行、Evidence 必須、閾値適用。
- DoD：例外時のフォールバックと再試行。
- Acceptance：Runner.run(...) で end-to-end 完走（CLI/MCP からも同経路）。

T12. パイプライン（pipelines/crud_normalize.py）

- 内容：入力→LLM→検証→グラフ→出力（JSON/NDJSON）。
- DoD：ゴールデンテスト（固定入力→固定出力）。
- Acceptance：出力 Graph がスキーマ検証 OK、全 Edge に Evidence あり。

T13. CLI（interfases/cli/app.py）

- 内容：yuragi normalize / yuragi schema export / yuragi run-crud-pipeline。
- DoD：ヘルプ・終了コード・失敗時 JSON エラー出力。
- Acceptance：サンプル入力から 1 コマンドで graph.json を生成。

T14. Factory（interfases/factory.py）

- 内容：make_exposure("cli"|"mcp") 実装。YURAGI_EXPOSE で切替。
- DoD：ユニットテスト（未知値で例外／既知値で所定の型インスタンス）。
- Acceptance：python -m yuragi で Factory が起動し、cli/mcp のいずれかが動作。

T15. FastMCP サーバ（stdio）（interfases/mcp/server_fastmcp.py）

- 内容：FastMCP("yuragi") で MCP ツールを公開：
  - yuragi_normalize_crud(code_snippets, hints?) -> CRUDActionList
  - yuragi_verify_crud(crud, repo_opts?, db_opts?) -> Graph
  - yuragi_run_crud_pipeline(code_snippets, repo_opts?, db_opts?) -> Graph
  - yuragi_spec_impact(...) -> Graph
  - yuragi_merge_graphs(graphs) -> Graph
- DoD：MCP Host から tools/list に列挙され、tools/call が成功。
- Acceptance：mcp.run()（既定 STDIO）で双方向 I/O 安定、Graph を返す。

T16. ドキュメント & サンプル

- 内容：README（セットアップ／Python API／CLI／FastMCP の使い方）、サンプル入力、用語辞書の例。
- DoD：README の手順がローカルで再現。
- Acceptance：新規環境で README 手順通りにPython/CLI/MCP の 3 経路が動作。

T17. 品質・安全性

- 内容：PII マスキング、プロンプトに “自由文禁止＋JSON 限定” ガード、CLI allowlist、timeout、ログの秘匿。
- DoD：長文応答／拒否応答／スキーマ未充足時もクラッシュしない。
- Acceptance：全 LLM 応答が Structured Outputs に準拠（検証ロジックで保証）。

---

## 12. 補足設計メモ

Structured Outputs は JSON Schema をAPI 呼び出し時に指定して強制（“JSON モード”と違い、スキーマ遵守が目的）。

Agents SDK は Agent(output_type=...)／Runner.run(...)／tools／handoff／トレーシングを中核に実装（公式リファレンス準拠）。

FastMCP は Pythonic に MCP サーバを実装でき、STDIO が既定。まずローカルでの tools/list→tools/call を最小構成で成立させる。

---

## 参考（一次情報）

- OpenAI Structured Outputs（ガイド／発表）
- OpenAI Agents SDK（Runner/Agents/Tools/Tracing）
- FastMCP（GitHub／公式サイト／Quickstart／STDIO）
- MCP 一般（サーバ quickstart・stdio 実行例）

---

この仕様に沿って実装すれば、ライブラリ本体は純粋に“揺らぎ吸収＋検証＋正規化”に集中しつつ、expose は interfases 階層で Factory による CLI/MCP の切替ができます。FastMCP 側は 工具の型ヒントからスキーマを自動提示するため、Pydantic モデルを中核にSDK/エージェント/MCP の三者を同じ型で貫通できます。
