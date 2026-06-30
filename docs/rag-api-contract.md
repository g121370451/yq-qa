# RAG API Contract

`rag-manager` 对上层只暴露统一 API；具体 OpenViking Bot 和 DeepRead 通过 worker 子项目接入。

## Manager

```text
GET    /health
GET    /v1/stats
GET    /v1/rag-methods
POST   /v1/rag-methods
GET    /v1/rag-methods/{method_id}
PATCH  /v1/rag-methods/{method_id}
DELETE /v1/rag-methods/{method_id}
POST   /v1/rag-methods/{method_id}/start
POST   /v1/rag-methods/{method_id}/stop
POST   /v1/rag-methods/{method_id}/restart
GET    /v1/rag-methods/{method_id}/runtime
GET    /v1/rag-methods/{method_id}/health
GET    /v1/rag-methods/{method_id}/stats
POST   /v1/rag-methods/{method_id}/documents
GET    /v1/rag-methods/{method_id}/documents
PATCH  /v1/rag-methods/{method_id}/documents/{document_id}
DELETE /v1/rag-methods/{method_id}/documents/{document_id}
POST   /v1/rag-methods/{method_id}/retrieve
POST   /v1/rag-methods/{method_id}/chat
POST   /v1/rag-methods/{method_id}/chat/stream
```

## Worker

每个 worker 都实现：

```text
GET    /health
GET    /stats
POST   /documents
GET    /documents
PATCH  /documents/{document_id}
DELETE /documents/{document_id}
POST   /retrieve
POST   /chat
POST   /chat/stream
```
