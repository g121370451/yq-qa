# YQ-QA Apps

- `rag-openviking-bot`: OpenViking Bot 标准 RAG worker。
- `rag-deepread`: DeepRead 标准 RAG worker。
- `rag-manager`: RAG method 管理服务，负责 method 注册、启动、停止、入库代理和查询代理。
- `rag-manager-eval`: benchmark/eval runner。
- `yq-qa-web`: YQ-QA Web 前端，用于配置后端、上传文档、查看入库队列和提交异步问答任务。

YQ-QA 的主后端在仓库根项目 `src/yq_qa_rag` 中，通过 `rag-server` 启动；它负责异步 QA 任务、多 method 并行回答、答案合并、文档上传和入库任务队列。

当前先实现 RAG 管理层相关的三个 uv 子项目：

- `rag-manager`: 管理 RAG 方法注册、生命周期、健康检查、统计和请求转发。
- `rag-openviking-bot`: 包装 OpenViking Bot / Vikingbot OpenAPIChannel。
- `rag-deepread`: 包装本地 DeepRead 实现。
- `rag-manager-eval`: 参考 ov_test 的数据集格式，测试数据集在 rag-manager 上的效果。
- `src/yq_qa_rag`: 面向 Web/桌面端的问答任务编排后端。
- `yq-qa-web`: 浏览器前端。

安装和启动说明见：

- `docs/rag-manager-install.md`
- `docs/rag-api-contract.md`
- `apps/yq-qa-web/README.md`
