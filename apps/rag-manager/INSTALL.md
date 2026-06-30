# RAG Manager

`rag-manager` 是 YQ-QA 的 RAG 方法管理服务。它负责注册、启动、停止、健康检查和转发不同 RAG worker。

## 安装

```bash
cd /opt/yq-qa/apps/rag-manager
uv sync
```

## 启动

```bash
uv run rag-manager --host 127.0.0.1 --port 18081
```

默认 DB 固定在当前项目的 `data/rag-manager.sqlite3`，日志固定在 `logs/`。也可以显式指定：

```bash
uv run rag-manager --host 127.0.0.1 --port 18081 --db /opt/yq-qa/apps/rag-manager/data/rag-manager.sqlite3 --logs-dir /opt/yq-qa/apps/rag-manager/logs
```

Swagger:

```text
http://127.0.0.1:18081/docs
```

## 端口

- `18081`: rag-manager
- `18100+`: manager 自动分配给 worker

## 生命周期

- method 注册信息保存在 sqlite DB 中，manager 重启后会从 DB 恢复 method 列表。
- manager 启动时会检查 DB 中处于 `running`/`starting` 的 method，并恢复其运行状态或标记为 `crashed`。
- manager 正常关闭时会停止它管理的 worker 进程树。
- 如果 worker 是历史残留进程，但 DB 中没有对应 method 记录，manager 不会盲目清理它。

## 注册 OpenViking Bot

```json
{
  "method_id": "openviking-bot-default",
  "backend_type": "openviking_bot",
  "display_name": "OpenViking Bot",
  "enabled": true,
  "config": {
    "project_path": "/opt/yq-qa/apps/rag-openviking-bot",
    "ov_conf": "/opt/yq-qa/config/ov.conf",
    "server_mode": "managed",
    "server_with_bot": true,
    "bot_route": "server",
    "logs_dir": "/opt/yq-qa/logs/openviking-bot"
  }
}
```

## 注册 DeepRead

```json
{
  "method_id": "deepread-default",
  "backend_type": "deepread",
  "display_name": "DeepRead",
  "enabled": true,
  "config": {
    "project_path": "/opt/yq-qa/apps/rag-deepread",
    "corpus_paths": []
  }
}
```
