# yuragi タスク（DoD / Acceptance 付き）

以下は、最終仕様に基づく実装タスクの一覧です。進捗やアップデート内容は本ファイルで管理し、変更があれば追記してください。

> 既存の pyproject.toml／noxfile.py／GitHub Actions 雛形は 流用します。ここでは本プロジェクト固有のタスクのみ。

## T0. 依存・pyright 準備
- **内容**: `openai`, `openai-agents`, `pydantic>=2`, `httpx`, `tenacity`, `rich`, `pyright`, `pytest`, `jsonschema` を追加。型チェック設定は `pyproject.toml` の `[tool.pyright]` に集約。
- **DoD**: ローカルで `pyright` & `pytest -q` が成功。
- **Acceptance**: CI（雛形）で `pyright` 0 エラー・テスト緑。
- **ステータス**: ✅ 依存関係を整理し、`pyproject.toml` で Pyright strict を設定済み（pyright / pytest 実行済み）。

## T1. Pydantic ドメインモデル（`core/models.py`）
- **内容**: `Node`/`Edge`/`Evidence`/`CRUDAction`/`Graph` と列挙型 `NodeType`/`EdgeType` を定義。
- **DoD**: `model_json_schema()` の出力が JSON Schema 検証を通過。
- **Acceptance**: サンプル Graph（10 ノード/20 エッジ）が round-trip 復元可。
- **ステータス**: ✅ Pydantic モデル一式を実装し、ラウンドトリップテストを追加済み。

## T2. スキーマユーティリティ（`core/schema.py`）
- **内容**: Schema エクスポート、`schema_version` 埋め込み、破壊変更差分の検知。
- **DoD**: フィールド追加/削除/型変更の検知テスト。
- **Acceptance**: `yuragi schema export > schema.json` が Structured Outputs にそのまま渡せる（OpenAI SDK で使用可）。
- **ステータス**: ✅ スキーマエクスポートと差分検知を実装し、検知テストを整備済み。

## T3. LLM クライアント & 構造化出力（`llm/client.py`, `llm/structured.py`）
- **内容**: OpenAI SDK ラッパ（再試行・タイムアウト・ログ）。Pydantic スキーマを `response_format=json_schema` で指定し、strict な構造化出力を取得。
- **DoD**: 録画テストで 3 ケース（文字列結合 SQL／ORM／テンプレ）を安定パース。
- **Acceptance**: すべての応答がスキーマ完全準拠し、LLM 逸脱時は自動リトライ後に明示エラー。
- **ステータス**: ✅ OpenAI ラッパと構造化出力ジェネレーターを実装し、録画 3 ケース＋リトライ／エラー制御テストを追加済み。

## T4. 信頼度スコア（`core/scoring.py`）
- **内容**: 静的(+0.3)/実行時(+0.3)/複数ツール合意(+0.2)/名称衝突(-0.2) などのルール。
- **DoD**: 5 パターンのユニットテスト。
- **Acceptance**: `confidence >= 0.7` のみ “確証済み” フラグが立つ。
- **ステータス**: ✅ 信頼度スコアリングを実装し、ユニットテストを整備済み。

## T5. リポジトリアダプタ（`tools/repo.py`）
- **内容**: `CLIAdapter`（例: ripgrep）、`HTTPAdapter`、`CallableAdapter`。共通ヒット型に正規化。
- **DoD**: モックで 3 アダプタの I/O を検証。
- **Acceptance**: 外部 `rg` を注入でき、曖昧候補からの再検索で実際のヒットを返す。

## T6. DB 検証（`tools/db.py`）
- **内容**: `introspect_table/columns`, `explain(sql)` の抽象 I/F（PostgreSQL 実装）。
- **DoD**: ローカル PG（docker）で e2e テスト。
- **Acceptance**: 存在しないテーブルでは negative result を返し、confidence が下がる。
- **ステータス**: ✅ SQLite/Factory 経由での DB アダプタ実装とネガティブ結果のペナルティを追加し、SQLite 経路のテストを整備。

## T7. 仕様差分取り込み（`tools/specs.py`）
- **内容**: `oasdiff`/`buf`/`graphql-inspector` 出力のパースと正規化。
- **DoD**: 3 形式をパースできる。
- **Acceptance**: 破壊変更で `CALLS` 影響エッジが生成される。
- **ステータス**: ✅ 3 種のツール出力を正規化し、破壊変更を CALLS エッジへ変換する実装とテストを追加。

## T8. 実行時根拠（`tools/runtime.py`）
- **内容**: `pg_stat_statements`／OTel ライク JSON → READS/WRITES/CALLS の実在フラグ。
- **DoD**: 変換テスト。
- **Acceptance**: 静的+実行時の合流で confidence が上がる。
- **ステータス**: ✅ pg_stat_statements と OTel スパン JSON を READS/WRITES/CALLS フラグへ変換し、ユニットテストを追加済み。

## T9. Normalize Agent（`agents/normalize_agent.py`）
- **内容**: `Agent(output_type=CRUDActionList)` を定義。few-shot と用語辞書で曖昧入力を吸収。
- **DoD**: 3 種の曖昧入力で CRUDAction を最低 1 件以上生成。
- **Acceptance**: Agents のトレースで手順が可視（SDK のトレーシング確認）。
- **ステータス**: ✅ 用語辞書＋few-shot 指示で正規化 Agent を実装し、トレーシング経路をテスト済み。

## T10. Verify Agent（`agents/verify_agent.py`）
- **内容**: repo/db を直列で呼び、Evidence を集約し confidence 更新。
- **DoD**: 偽陽性候補が検証で除外される。
- **Acceptance**: 重要エッジが `confidence >= 0.7` に到達。
- **ステータス**: ✅ リポジトリ／DB 検証エージェントを実装し、エビデンス集約と信頼度更新テストを追加済み。

## T11. オーケストレータ（`agents/orchestrator.py`）
- **内容**: Normalize→Verify の handoff、再試行、Evidence 必須、閾値適用。
- **DoD**: 例外時のフォールバックと再試行。
- **Acceptance**: `Runner.run(...)` で end-to-end 完走（CLI/MCP からも同経路）。
- **ステータス**: ✅ Normalize/Verify の連結オーケストレータを実装し、閾値・Evidence 必須・リトライ／フォールバック挙動をテストで検証済み。

## T12. パイプライン（`pipelines/crud_normalize.py`）
- **内容**: 入力→LLM→検証→グラフ→出力（JSON/NDJSON）。
- **DoD**: ゴールデンテスト（固定入力→固定出力）。
- **Acceptance**: 出力 Graph がスキーマ検証 OK、全 Edge に Evidence あり。

## T13. CLI（`interfases/cli/app.py`）
- **内容**: `yuragi normalize` / `yuragi schema export` / `yuragi run-crud-pipeline`。
- **DoD**: ヘルプ・終了コード・失敗時 JSON エラー出力。
- **Acceptance**: サンプル入力から 1 コマンドで `graph.json` を生成。

## T14. Factory（`interfases/factory.py`）
- **内容**: `make_exposure("cli"|"mcp")` 実装。`YURAGI_EXPOSE` で切替。
- **DoD**: ユニットテスト（未知値で例外／既知値で所定の型インスタンス）。
- **Acceptance**: `python -m yuragi` で Factory が起動し、cli/mcp のいずれかが動作。

## T15. FastMCP サーバ（stdio）（`interfases/mcp/server_fastmcp.py`）
- **内容**: `FastMCP("yuragi")` で MCP ツールを公開：
  - `yuragi_normalize_crud(code_snippets, hints?) -> CRUDActionList`
  - `yuragi_verify_crud(crud, repo_opts?, db_opts?) -> Graph`
  - `yuragi_run_crud_pipeline(code_snippets, repo_opts?, db_opts?) -> Graph`
  - `yuragi_spec_impact(...) -> Graph`
  - `yuragi_merge_graphs(graphs) -> Graph`
- **DoD**: MCP Host から `tools/list` に列挙され、`tools/call` が成功。
- **Acceptance**: `mcp.run()`（既定 STDIO）で双方向 I/O 安定、Graph を返す。

## T16. ドキュメント & サンプル
- **内容**: README（セットアップ／Python API／CLI／FastMCP の使い方）、サンプル入力、用語辞書の例。
- **DoD**: README の手順がローカルで再現。
- **Acceptance**: 新規環境で README 手順通りに Python/CLI/MCP の 3 経路が動作。

## T17. 品質・安全性
- **内容**: PII マスキング、プロンプトに “自由文禁止＋JSON 限定” ガード、CLI allowlist、timeout、ログの秘匿。
- **DoD**: 長文応答／拒否応答／スキーマ未充足時もクラッシュしない。
- **Acceptance**: すべての LLM 応答が Structured Outputs に準拠（検証ロジックで保証）。
