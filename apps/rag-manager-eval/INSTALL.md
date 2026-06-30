# RAG Manager Eval

这个子项目用于通过 `rag-manager` 测试数据集在不同 RAG method 上的效果。

## 安装

```powershell
cd D:\project\mine\yq-qa\apps\rag-manager-eval
uv sync
```

## 启动 Manager

使用固定的 manager 端口和默认 DB：

```powershell
cd D:\project\mine\yq-qa\apps\rag-manager
uv run rag-manager --host 127.0.0.1 --port 18081
```

默认 DB 在：

```text
D:\project\mine\yq-qa\apps\rag-manager\data\rag-manager.sqlite3
```

Swagger:

```text
http://127.0.0.1:18081/docs
```

## 数据集

数据集放在：

```text
D:\project\mine\yq-qa\apps\rag-manager-eval\data
```

如果需要从原始数据目录同步：

```powershell
robocopy D:\project\postgraduate\Data D:\project\mine\yq-qa\apps\rag-manager-eval\data /E
```

## VersionRAG OpenViking

配置文件：

```text
configs\local-versionrag-openviking-bot-chat.yaml
configs\openviking-versionrag.ov.conf
```

先准备 `.env`：

```powershell
cd D:\project\mine\yq-qa\apps\rag-manager-eval
Copy-Item .env.example .env
```

然后编辑 `.env` 中的 manager 地址、judge 模型和 OpenViking 模型参数。`rag-manager-eval` 会先把 `.env` 加载到 `os.environ`，再解析 YAML 中的 `${...}` 占位符。

`.env` 中有三组模型配置：

```text
YQ_OPENVIKING_EMBEDDING_BASE_URL / YQ_OPENVIKING_EMBEDDING_API_KEY / YQ_OPENVIKING_EMBEDDING_MODEL
YQ_OPENVIKING_VLM_BASE_URL / YQ_OPENVIKING_VLM_API_KEY / YQ_OPENVIKING_VLM_MODEL
YQ_RAG_EVAL_JUDGE_BASE_URL / YQ_RAG_EVAL_JUDGE_API_KEY / YQ_RAG_EVAL_JUDGE_MODEL
```

执行入库：

```powershell
cd D:\project\mine\yq-qa\apps\rag-manager-eval
uv run rag-manager-eval --config configs\local-versionrag-openviking-bot-chat.yaml --stage import
```

执行生成：

```powershell
uv run rag-manager-eval --config configs\local-versionrag-openviking-bot-chat.yaml --stage gen
```

执行评价：

```powershell
uv run rag-manager-eval --config configs\local-versionrag-openviking-bot-chat.yaml --stage eval
```

也可以使用：

```powershell
uv run rag-manager-eval --config configs\local-versionrag-openviking-bot-chat.yaml --stage all
```

如果 `.env` 不在默认位置，可以显式指定：

```powershell
uv run rag-manager-eval --env-file D:\path\to\.env --config configs\local-versionrag-openviking-bot-chat.yaml --stage gen+eval
```

输出目录：

```text
outputs\openviking-versionrag
```

主要输出文件：

```text
generated_answers.json
qa_eval_detailed_results.json
rag_manager_eval_report.json
ingested_documents.json
```

## Stage

- `import`: 通过 adapter 准备文档，并调用 manager 的 `/documents` 入库。
- `gen`: 异步调用 manager 的 `/chat` 或 `/retrieve` 生成结果。
- `eval`: 读取已有 `generated_answers.json`，与 gold answer 做指标评价。
- `gen+eval`: 先生成再评价。
- `del`: 删除本次记录的入库文档。
- `all`: import、gen、eval 全流程。

## Judge

默认启用 LLM judge。judge 使用的大模型配置写在 eval YAML 里，例如：

```yaml
judge:
  enabled: true
  base_url: "${YQ_RAG_EVAL_JUDGE_BASE_URL}"
  model: "${YQ_RAG_EVAL_JUDGE_MODEL}"
  api_key: "${YQ_RAG_EVAL_JUDGE_API_KEY}"
  timeout_seconds: "${YQ_RAG_EVAL_JUDGE_TIMEOUT_SECONDS}"
```

占位符从 `.env` 或当前进程环境变量读取。当前进程里已经存在的环境变量优先级更高，`.env` 不会覆盖它。

如果 YAML 中没有写某个 judge 字段，代码仍保留兜底：

```text
YQ_RAG_EVAL_JUDGE_BASE_URL -> OPENAI_BASE_URL -> https://api.openai.com/v1
YQ_RAG_EVAL_JUDGE_MODEL -> OPENAI_MODEL -> gpt-4o-mini
YQ_RAG_EVAL_JUDGE_API_KEY -> OPENAI_API_KEY
```

临时关闭 judge：

```powershell
$env:YQ_RAG_EVAL_JUDGE_ENABLED="false"
```
