# yq-qa 后端部署说明

`rag-server` 现在是桌面端问答后端。它的主职责是管理异步 QA 任务，
并通过 `rag-manager` 调用一个或多个 RAG method。

## 职责边界

`src/yq_qa_rag` 负责：

- 接收前端问题，创建异步问答任务。
- 调用 `rag-manager` 中已注册并运行的 RAG method。
- 支持一个 method 回答，也支持多个 method 并行回答。
- 持久化任务状态、事件日志、每个 method 的回答。
- 可选调用 OpenAI-compatible 模型合并多个 RAG 答案。
- 提供 Swagger：`http://127.0.0.1:18082/docs`。

不负责：

- 不直接启动 OpenViking Bot 或 DeepRead worker。
- 不负责文档入库、删除、更新。
- 不维护 VLM / embedding 配置。
- 不做 benchmark eval。

这些由其他服务负责：

```text
apps/rag-manager          RAG method 注册、启动、停止、入库代理、查询代理
apps/rag-openviking-bot   OpenViking Bot 标准 RAG worker
apps/rag-deepread         DeepRead 标准 RAG worker
apps/rag-manager-eval     benchmark/eval runner
```

## 安装

在项目根目录执行：

```powershell
cd D:\project\mine\yq-qa
uv sync
```

## 配置

QA 运行配置由前端通过接口写入后端，并持久化到 SQLite。
`rag-server` 默认会启用认证；如果当前目录存在 `.env.yq-qa`，
启动时会自动读取它并打印配置文件路径。

默认配置接口：

```http
GET /v1/config
PUT /v1/config
```

前端需要保存的核心配置：

```env
YQ_RAG_MANAGER_BASE_URL=http://127.0.0.1:18081
YQ_QA_DB=data/yq-qa.sqlite3
YQ_QA_DEFAULT_METHOD_IDS=openviking-bot-versionrag-chat,deepread-versionrag
YQ_QA_MAX_CONCURRENT_TASKS=4

YQ_QA_MERGE_ENABLED=true
YQ_QA_MERGE_BASE_URL=https://api.openai.com/v1
YQ_QA_MERGE_API_KEY=replace-me
YQ_QA_MERGE_MODEL=replace-me
```

如果暂时不需要两个答案合并，前端保存：

```env
YQ_QA_MERGE_ENABLED=false
```

认证默认开启。首次启动前需要至少配置 token secret 和管理员初始密码：

```env
YQ_QA_AUTH_ENABLED=true
YQ_QA_JWT_SECRET=replace-with-a-long-random-secret
YQ_QA_TOKEN_EXPIRE_MINUTES=720
YQ_QA_ADMIN_USERNAME=admin
YQ_QA_ADMIN_PASSWORD=replace-me
```

首次启动且 `users` 表为空时，后端会用上面的管理员账号初始化用户。
如果只做本机调试，可以显式设置 `YQ_QA_AUTH_ENABLED=false`。

`.env.yq-qa.example` 只作为开发期“初始化默认值”示例，不是必需文件。

## 启动顺序

先启动 `rag-manager`，并确认需要的 method 已经注册并 running。

```powershell
cd D:\project\mine\yq-qa\apps\rag-manager
uv run rag-manager --host 127.0.0.1 --port 18081
```

然后启动 QA 后端：

```powershell
cd D:\project\mine\yq-qa
uv run rag-server --host 127.0.0.1 --port 18082
```

也可以显式指定环境文件：

```powershell
uv run rag-server --env-file .env.yq-qa --host 127.0.0.1 --port 18082
```

启动时会打印：

```text
service_url
swagger_url
db_path
rag_manager_base_url
default_method_ids
merge enabled/model/base_url
```

API key 只会 mask 打印。

## 管理 18082 进程

查看 18082 端口对应的进程：

```powershell
$conn = Get-NetTCPConnection -LocalPort 18082 -State Listen
$conn | Select-Object LocalAddress,LocalPort,OwningProcess
Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)" |
  Select-Object ProcessId,CommandLine
```

停止当前 QA 后端：

```powershell
$pid = (Get-NetTCPConnection -LocalPort 18082 -State Listen).OwningProcess
Stop-Process -Id $pid
```

重启 QA 后端：

```powershell
cd D:\project\mine\yq-qa
$conn = Get-NetTCPConnection -LocalPort 18082 -State Listen -ErrorAction SilentlyContinue
if ($conn) { Stop-Process -Id $conn.OwningProcess }
uv run rag-server --env-file .env.yq-qa --host 127.0.0.1 --port 18082
```

## 主接口

### 健康检查

```http
GET /health
```

### 查看当前配置

```http
GET /v1/config
```

### 查看 rag-manager 的 methods

```http
GET /v1/rag-methods
```

### 创建异步问答任务

```http
POST /v1/qa/tasks
```

示例：

```json
{
  "question": "Spark 3.5.5 修复了哪些问题？",
  "method_ids": [
    "openviking-bot-versionrag-chat",
    "deepread-versionrag"
  ],
  "merge_strategy": "auto",
  "options": {
    "target_uri": "viking://resources/"
  }
}
```

`merge_strategy`：

```text
auto  多 method 且合并模型可用时自动合并，否则分段拼接
llm   强制调用合并模型
none  不调用合并模型，保留并分段拼接各 method 回答
```

### 查看任务列表

```http
GET /v1/qa/tasks
GET /v1/qa/tasks?status=running
```

### 查看任务详情

```http
GET /v1/qa/tasks/{task_id}
```

返回内容包含：

- 任务状态。
- 每个 method 的回答。
- 每个 method 的 sources。
- 合并后的 `merged_answer`。
- 错误信息。

### 查看任务事件

```http
GET /v1/qa/tasks/{task_id}/events
GET /v1/qa/tasks/{task_id}/events?after_id=10
```

### SSE 监听任务事件

```http
GET /v1/qa/tasks/{task_id}/stream
```

### 取消任务

```http
POST /v1/qa/tasks/{task_id}/cancel
```

取消是 best-effort：如果请求已经发送给 RAG worker，当前版本只能记录取消信号，
等 worker 返回后再把任务标记为取消。

## 文档上传和入库

前端可以通过 QA 后端上传文件。上传请求只负责保存文件并创建后台入库 job，
不会阻塞主进程等待 semantic/embedding 完成。

上传接口：

```http
POST /v1/documents/upload
Content-Type: multipart/form-data
```

字段：

```text
method_id       目标 RAG method
files           一个或多个文件
metadata_json   可选，JSON object
options_json    可选，JSON object，传给 rag-manager ingestion job
```

返回：

```json
{
  "job_id": "...",
  "method_id": "...",
  "status": "queued",
  "documents": []
}
```

后台 job 会调用：

```text
rag-manager /v1/rag-methods/{method_id}/ingestion-jobs
```

查看入库队列：

```http
GET /v1/documents/ingestion-jobs
GET /v1/documents/ingestion-jobs?status=running
```

查看某个入库 job：

```http
GET /v1/documents/ingestion-jobs/{job_id}
```

返回里有前端进度条需要的字段：

```json
{
  "progress": {
    "total_documents": 10,
    "completed_documents": 4,
    "failed_documents": 0,
    "running_documents": 2,
    "pending_documents": 4,
    "progress_percent": 40.0,
    "message": "Ingestion running"
  }
}
```

查看文字事件：

```http
GET /v1/documents/ingestion-jobs/{job_id}/events
```

SSE 监听进度：

```http
GET /v1/documents/ingestion-jobs/{job_id}/stream
```

取消入库 job：

```http
POST /v1/documents/ingestion-jobs/{job_id}/cancel
```

取消也是 best-effort：如果任务已经提交给 `rag-manager`，当前版本会记录取消信号，
但不能强制中断已经在 RAG worker 内部执行的 semantic/embedding。

上传目录由前端配置：

```http
PUT /v1/config
```

示例：

```json
{
  "upload_dir": "data/uploads",
  "max_concurrent_ingestion_jobs": 2
}
```

## 兼容接口

旧的单 RAG wrapper 接口仍保留，主要用于调试：

```text
GET  /capabilities
POST /v1/chat
POST /v1/chat/stream
POST /v1/requests/{request_id}/cancel
```

它们仍然由 `RAG_BACKEND=openviking_rag/deepread/ovbot` 和旧 adapter 配置控制。
桌面端主流程建议使用 `/v1/qa/tasks`。
