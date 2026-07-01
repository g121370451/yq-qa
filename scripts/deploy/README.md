# YQ-QA Linux Service Scripts

这两个脚本用于 Linux 服务器部署：

- `start-yq-qa.sh`：清理占用端口，写入 systemd unit，启动并守护服务。
- `stop-yq-qa.sh`：停止并默认禁用 systemd unit，清理占用端口。

默认路径假设仓库位于：

```bash
/www/wwwroot/default/yq-qa
```

如果仓库在其他目录，启动时设置：

```bash
export YQ_QA_ROOT=/www/wwwroot/default/yq-qa
export YQ_OV_CONF=/www/wwwroot/default/yq-qa/config/ov.conf
```

## 启动

```bash
cd /www/wwwroot/default/yq-qa
sudo bash scripts/deploy/start-yq-qa.sh
```

默认启动并守护：

- `yq-openviking.service`: `127.0.0.1:20100`
- `yq-vikingbot-gateway.service`: `127.0.0.1:21100`
- `yq-rag-manager.service`: `127.0.0.1:18081`
- `yq-qa-backend.service`: `127.0.0.1:18082`

启动脚本默认会在 rag-manager 里注册并启动：

```text
openviking-bot-default
```

method 配置使用外部 HTTP 模式：

```text
server_url=http://127.0.0.1:20100
gateway_url=http://127.0.0.1:21100
```

如果不希望脚本修改 rag-manager 数据库：

```bash
sudo YQ_REGISTER_OPENVIKING_METHOD=0 bash scripts/deploy/start-yq-qa.sh
```

## 停止

```bash
cd /www/wwwroot/default/yq-qa
sudo bash scripts/deploy/stop-yq-qa.sh
```

停止脚本默认会 `disable --now`。如果只停止、不禁用开机自启：

```bash
sudo YQ_DISABLE_UNITS=0 bash scripts/deploy/stop-yq-qa.sh
```

## 常用变量

```bash
YQ_QA_ROOT=/www/wwwroot/default/yq-qa
YQ_OV_CONF=/www/wwwroot/default/yq-qa/config/ov.conf
YQ_OPENVIKING_PORT=20100
YQ_VIKINGBOT_GATEWAY_PORT=21100
YQ_RAG_MANAGER_PORT=18081
YQ_QA_BACKEND_PORT=18082
YQ_RAG_WORKER_BASE_PORT=18100
YQ_RAG_WORKER_PORT_COUNT=100
YQ_UV_SYNC=auto
```

默认清理端口：

```text
20100, 21100, 18081, 18082, 18100-18199
```

不清理 worker 端口范围：

```bash
sudo YQ_CLEAN_WORKER_PORTS=0 bash scripts/deploy/start-yq-qa.sh
```

## 日志

```bash
sudo journalctl -u yq-openviking -f
sudo journalctl -u yq-vikingbot-gateway -f
sudo journalctl -u yq-rag-manager -f
sudo journalctl -u yq-qa-backend -f
```
