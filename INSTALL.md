# yq-qa RAG 后端接口服务部署

这个项目提供一套统一 RAG HTTP 接口，可以分别包装：

- `ovbot`：转发到已有的 OV-Bot Gateway。
- `deepread`：直接调用本地 `D:\project\postgraduate\ruc-ov-eval\DeepRead` 实现。

两个服务用同一份代码启动，区别只是 `--backend` 和端口。

## 1. 安装依赖

在 `D:\project\mine\yq-qa` 下执行：

```powershell
uv sync
```

如果要启动 DeepRead 服务，额外安装 DeepRead 运行依赖：

```powershell
uv sync --extra deepread
```

可以复制示例环境文件后再改配置：

```powershell
Copy-Item .env.ovbot.example .env.ovbot
Copy-Item .env.deepread.example .env.deepread
```

## 2. 标准接口

两个 RAG 服务都提供同样的接口：

```text
GET  /health
GET  /capabilities
POST /v1/chat
POST /v1/chat/stream
POST /v1/requests/{request_id}/cancel
POST /v1/sessions
GET  /v1/sessions
GET  /v1/sessions/{session_id}
DELETE /v1/sessions/{session_id}
```

Swagger 地址：

```text
http://127.0.0.1:<port>/docs
```

## 3. 启动 OV-Bot RAG 服务

先确保 OV-Bot Gateway 已启动。通常在 OpenViking bot 项目里：

```powershell
cd D:\project\mine\OpenViking\bot
uv run vikingbot gateway --port 18790
```

注意：当前 OV-Bot 的 `/bot/v1/chat` 和 `/bot/v1/chat/stream` 路由要求 OpenAPI channel 配置 API Key；如果没配，接口会返回 `503 OpenAPI channel API key is not configured`。你需要在 `ov.conf` 的 `bot.channels` 里配置 `openapi` channel，例如：

```json
{
  "bot": {
    "channels": [
      {
        "type": "openapi",
        "enabled": true,
        "api_key": "change-me"
      }
    ]
  }
}
```

然后在 `yq-qa` 启动 OV-Bot 标准接口包装服务：

```powershell
cd D:\project\mine\yq-qa
$env:OVBOT_BASE_URL="http://127.0.0.1:18790"
$env:OVBOT_CHAT_PATH="/bot/v1/chat"
$env:OVBOT_CHAT_STREAM_PATH="/bot/v1/chat/stream"
$env:OVBOT_API_KEY="change-me"

uv run rag-server --backend ovbot --host 127.0.0.1 --port 18791
```

也可以使用 env 文件启动：

```powershell
uv run rag-server --backend ovbot --env-file .env.ovbot --host 127.0.0.1 --port 18791
```

Swagger：

```text
http://127.0.0.1:18791/docs
```

如果你通过 `openviking-server --with-bot` 的代理访问，则把路径改成：

```powershell
$env:OVBOT_BASE_URL="http://127.0.0.1:1933"
$env:OVBOT_CHAT_PATH="/api/v1/bot/chat"
$env:OVBOT_CHAT_STREAM_PATH="/api/v1/bot/chat/stream"
```

## 4. 启动 DeepRead RAG 服务

DeepRead 服务需要至少一个 `*_corpus.json`。

如果还没有 corpus，先在 DeepRead 项目里生成：

```powershell
cd D:\project\postgraduate\ruc-ov-eval
uv run python -m DeepRead.deepread parse D:\path\to\doc.md --build-embeddings
```

然后启动服务：

```powershell
cd D:\project\mine\yq-qa

$env:DEEPREAD_PROJECT_PATH="D:\project\postgraduate\ruc-ov-eval"
$env:DEEPREAD_CORPUS_PATHS="D:\path\to\doc_corpus.json"

$env:DEEPREAD_MODEL="你的 Codex 或 OpenAI 兼容模型名"
$env:DEEPREAD_BASE_URL="https://api.openai.com/v1"
$env:DEEPREAD_API_KEY="你的模型 API Key"

$env:DEEPREAD_EMBEDDING_MODEL="你的豆包 embedding 模型名"
$env:DEEPREAD_EMBED_BASE_URL="你的豆包 embedding OpenAI 兼容 base url"
$env:DEEPREAD_EMBED_API_KEY="你的豆包 embedding API Key"

uv run rag-server --backend deepread --host 127.0.0.1 --port 18800
```

也可以使用 env 文件启动：

```powershell
uv run rag-server --backend deepread --env-file .env.deepread --host 127.0.0.1 --port 18800
```

Swagger：

```text
http://127.0.0.1:18800/docs
```

多个 corpus 用逗号或分号分隔：

```powershell
$env:DEEPREAD_CORPUS_PATHS="D:\a\a_corpus.json;D:\b\b_corpus.json"
```

## 5. 流式问答请求示例

OV-Bot 服务：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:18791/v1/chat" `
  -ContentType "application/json" `
  -Body '{
    "request_id": "q-001",
    "session_id": "s-001",
    "user_id": "default",
    "question": "请总结知识库里的核心内容",
    "options": {
      "return_sources": true
    }
  }'
```

DeepRead 服务：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:18800/v1/chat" `
  -ContentType "application/json" `
  -Body '{
    "request_id": "q-002",
    "session_id": "s-002",
    "user_id": "default",
    "question": "这篇文档的实验结论是什么？",
    "options": {
      "retrieval": "hybrid",
      "top_k": 3,
      "return_sources": true
    }
  }'
```

## 6. 服务端口规划

建议本地开发阶段：

```text
OV-Bot Gateway          18790
OV-Bot 标准包装服务      18791
DeepRead 标准服务        18800
后续 yq-qa 协调后端      18080
```

桌面端后续只需要访问 `yq-qa` 协调后端；协调后端再调用 `18791` 和 `18800`。

## 7. 注意事项

- `ovbot` 包装服务本身不做 RAG，它只把统一接口转换成 OV-Bot Gateway 请求。
- `deepread` 服务当前使用本地 DeepRead 的同步 agent 调用，流式接口会先发 `start/status`，完成后再发完整答案。
- DeepRead 的取消是 best-effort：接口会停止当前 SSE 输出，但 DeepRead 内部已经发出的阻塞模型请求可能要等本轮返回。
- 并发问题必须使用不同的 `request_id`；如果不想共享上下文，也使用不同的 `session_id`。
