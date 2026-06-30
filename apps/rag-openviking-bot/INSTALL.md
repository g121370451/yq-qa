# OpenViking Bot Worker

这个子项目不是重新实现 OpenViking Bot，而是把官方 `openviking[bot]` 包装成 YQ-RAG 标准 worker。

推荐链路：

```text
rag-manager / yq-qa
  -> rag-openviking-bot
      -> 官方 openviking-server --with-bot
          -> 官方 vikingbot gateway
```

`rag-openviking-bot` 对外暴露统一接口，内部只负责启动/连接官方 OpenViking Bot 服务。

## 安装

服务器上安装：

```bash
cd /opt/yq-qa/apps/rag-openviking-bot
uv sync
```

依赖来自官方 Python 包声明：

```toml
dependencies = [
  "openviking[bot]",
]
```

如果要使用 OpenViking 的未发布开发版本，可以在部署环境里额外配置 `tool.uv.sources`，但不应该写死开发机路径。

## 配置

准备 OpenViking 配置文件。worker 默认按下面顺序查找：

```text
1. OPENVIKING_CONFIG_FILE 环境变量
2. 当前启动目录下的 ov.conf
3. rag-openviking-bot 项目目录下的 ov.conf
4. ~/.openviking/ov.conf
```

配置文件里需要包含 OpenViking server、storage、embedding、bot/LLM/VLM 等实际运行所需配置。

如果不传 `YQ_RAG_METHOD_CONFIG`，默认等价于：

```json
{
  "method_id": "openviking-bot",
  "server_mode": "managed",
  "server_host": "127.0.0.1",
  "server_port": 1933,
  "server_url": "http://127.0.0.1:1933",
  "server_with_bot": true,
  "bot_route": "server",
  "logs_dir": "/opt/yq-qa/logs/openviking-bot"
}
```

含义：

- `server_mode=managed`：由 worker 启动官方 `openviking-server`。
- `server_with_bot=true`：启动命令会带上官方 `--with-bot`，由 OpenViking 自己启动 `vikingbot gateway`。
- `bot_route=server`：`/chat` 调用 OpenViking server 代理出来的官方 `/bot/v1/chat`。

`openviking_root` 只用于本地源码调试，服务器部署不需要配置。

## 启动

如果 `ov.conf` 放在 `apps/rag-openviking-bot/ov.conf` 或当前启动目录下，直接启动：

```bash
uv run openviking-bot-worker --host 0.0.0.0 --port 18101
```

也可以用环境变量指定配置文件：

```bash
export OPENVIKING_CONFIG_FILE="/opt/yq-qa/config/ov.conf"

uv run openviking-bot-worker --host 0.0.0.0 --port 18101
```

worker 对外地址：

```text
http://服务器IP:18101
```

官方 OpenViking server 在本机：

```text
http://127.0.0.1:1933
```

## 只用检索

如果暂时只用 `/documents` 和 `/retrieve`，不使用 `/chat`：

```bash
export YQ_RAG_METHOD_CONFIG='{"method_id":"openviking-bot","ov_conf":"/opt/yq-qa/config/ov.conf","server_mode":"managed","server_host":"127.0.0.1","server_port":1933,"server_url":"http://127.0.0.1:1933","gateway_mode":"disabled","logs_dir":"/opt/yq-qa/logs/openviking-bot"}'

uv run openviking-bot-worker --host 0.0.0.0 --port 18101
```

## 接口

```text
GET  /health
GET  /stats
POST /documents
GET  /documents
POST /retrieve
POST /chat
POST /chat/stream
```

`/documents`、`/retrieve` 依赖官方 OpenViking Server。

`/chat`、`/chat/stream` 依赖官方 OpenViking Bot API。
