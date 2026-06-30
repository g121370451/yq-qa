# yq-qa-web

YQ-QA 的 Web 前端。当前只面向浏览器运行，后续桌面端可以复用这套页面作为壳内页面。

## 功能

- 配置 YQ-QA backend 地址。
- 读取和保存后端运行配置。
- 查看 RAG Manager 已注册 method。
- 上传多个文件并创建异步入库任务。
- 查看入库队列、入库进度、成功/失败状态。
- 创建异步 QA 任务，选择一个或多个 method 回答。
- 通过 SSE 实时更新 QA 任务和入库任务状态。
- 查看问答历史和入库历史。

## 启动

先启动后端：

```powershell
uv run rag-server --host 127.0.0.1 --port 18082
```

再启动前端：

```powershell
cd apps/yq-qa-web
npm install
npm run dev -- --port 5173
```

浏览器打开：

```text
http://127.0.0.1:5173
```

默认后端地址是：

```text
http://127.0.0.1:18082
```

这个地址可以在页面顶部修改，前端会保存到浏览器 localStorage。
