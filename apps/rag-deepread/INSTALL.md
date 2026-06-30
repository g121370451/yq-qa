# DeepRead Worker

这个子项目把 DeepRead 源码打包在 `src/DeepRead` 中，并包装成 YQ-RAG 标准 worker。

## 安装

```powershell
cd D:\project\mine\yq-qa\apps\rag-deepread
uv sync
```

## 配置

worker 通过 `rag-manager` 注入配置。关键字段：

```json
{
  "deepread_source_path": "D:/project/postgraduate/ruc-ov-eval",
  "corpus_paths": [],
  "model": "your-chat-model",
  "base_url": "https://your-openai-compatible-endpoint/v1",
  "api_key": "your-api-key",
  "embedding_model": "Qwen/Qwen3-Embedding-8B",
  "embed_base_url": "https://api.siliconflow.cn/v1",
  "embed_api_key": ""
}
```

`deepread_source_path` 只作为兼容路径，用于加载历史 `ov_test` 等辅助模块；DeepRead 主源码已经由本 uv 子项目直接打包，不再依赖运行时 `sys.path` 指向外部 `DeepRead` 目录。

## 能力

- `POST /documents`: PDF/Markdown 解析为 DeepRead corpus。
- `POST /retrieve`: 调用 bm25/vector/hybrid/semantic 检索。
- `POST /chat`: 调用 `DeepRead.agent.runner.run_agent`。
