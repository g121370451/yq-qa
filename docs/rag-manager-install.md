# RAG Manager Install

## 1. 安装三个子项目

```powershell
cd D:\project\mine\yq-qa\apps\rag-manager
uv sync

cd D:\project\mine\yq-qa\apps\rag-openviking-bot
uv sync

cd D:\project\mine\yq-qa\apps\rag-deepread
uv sync
```

`rag-deepread` 已经把 DeepRead 源码打包在自己的 `src/DeepRead` 中，不依赖运行时从外部目录 import DeepRead。

## 2. 启动 manager

```powershell
cd D:\project\mine\yq-qa\apps\rag-manager
uv run rag-manager --host 127.0.0.1 --port 18081
```

Swagger:

```text
http://127.0.0.1:18081/docs
```

## 3. 注册 worker

使用 Swagger 调用 `POST /v1/rag-methods` 注册 OpenViking Bot 和 DeepRead。

然后调用：

```text
POST /v1/rag-methods/{method_id}/start
```

manager 会用对应子项目的 `uv run ...-worker` 启动子进程。
