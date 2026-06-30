from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


BackendName = Literal["openviking_rag", "deepread", "ovbot"]


def load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path).expanduser()
    if not env_path.exists():
        raise FileNotFoundError(f"env file not found: {env_path}")

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _split_paths(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(slots=True)
class AppConfig:
    backend: BackendName = "openviking_rag"
    service_name: str = "rag-service"

    qa_db_path: str = "data/yq-qa.sqlite3"
    qa_rag_manager_base_url: str = "http://127.0.0.1:18081"
    qa_rag_manager_timeout_seconds: float = 1200.0
    qa_default_method_ids: list[str] = field(default_factory=list)
    qa_max_concurrent_tasks: int = 4
    qa_method_timeout_seconds: float = 1200.0
    qa_merge_enabled: bool = False
    qa_merge_base_url: str | None = None
    qa_merge_api_key: str = ""
    qa_merge_model: str = ""
    qa_merge_timeout_seconds: float = 300.0
    qa_merge_temperature: float = 0.2

    openviking_rag_project_path: str = r"D:\project\mine\OpenViking\benchmark\RAG"
    openviking_root_path: str = r"D:\project\mine\OpenViking"
    openviking_rag_ov_conf: str = r"D:\project\mine\OpenViking\benchmark\RAG\ov.conf"
    openviking_rag_vector_store: str = ""
    openviking_rag_mode: str = "local"
    openviking_rag_server_url: str = "http://127.0.0.1:1933"
    openviking_rag_api_key: str = ""
    openviking_rag_retrieval_topk: int = 5
    openviking_rag_retrieval_instruction: str = ""
    openviking_rag_target_uri: str = "viking://resources"
    openviking_rag_use_relations: bool = False
    openviking_rag_relations_topk: int = 0
    openviking_rag_use_query_expansion: bool = False
    openviking_rag_link_strategy: str = "llm_review"
    openviking_rag_llm_model: str = ""
    openviking_rag_llm_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    openviking_rag_llm_api_key: str = ""
    openviking_rag_llm_temperature: float = 0.0
    openviking_rag_embedding_model: str = "doubao-embedding-vision-251215"
    openviking_rag_embedding_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    openviking_rag_embedding_api_key: str = ""

    ovbot_base_url: str = "http://127.0.0.1:18790"
    ovbot_chat_path: str = "/bot/v1/chat"
    ovbot_chat_stream_path: str = "/bot/v1/chat/stream"
    ovbot_api_key: str = ""
    ovbot_channel_id: str | None = None
    ovbot_timeout_seconds: float = 300.0

    deepread_project_path: str = r"D:\project\postgraduate\ruc-ov-eval"
    deepread_extra_pythonpath: list[str] = field(default_factory=list)
    deepread_corpus_paths: list[str] = field(default_factory=list)
    deepread_log_dir: str = "logs/deepread"
    deepread_model: str = ""
    deepread_base_url: str | None = None
    deepread_api_key: str = ""
    deepread_max_rounds: int = 50
    deepread_temperature: float = 0.0
    deepread_enable_multimodal: bool = False
    deepread_tool_fallback: bool = True
    deepread_enable_reasoning: bool = True

    deepread_embedding_model: str = "Qwen/Qwen3-Embedding-8B"
    deepread_embed_base_url: str = "https://api.siliconflow.cn/v1"
    deepread_embed_api_key: str = ""
    deepread_retrieval: str | None = None
    deepread_enable_vector: bool = False
    deepread_enable_hybrid: bool = False
    deepread_enable_semantic: bool = False
    deepread_disable_bm25: bool = False
    deepread_disable_regex: bool = False
    deepread_disable_read: bool = False
    deepread_bm25_topk: int = 1
    deepread_regex_topk: int = 1
    deepread_vector_topk: int = 1
    deepread_hybrid_topk: int = 1
    deepread_hybrid_topk_bm25: int = 30
    deepread_hybrid_topk_vec: int = 30
    deepread_hybrid_bm25_weight: float = 0.5
    deepread_hybrid_vector_weight: float = 0.5
    deepread_semantic_stage1: str = "vector"
    deepread_semantic_topk1: int = 30
    deepread_semantic_topk2: int = 2
    deepread_semantic_stage1_hybrid_topk_bm25: int = 30
    deepread_semantic_stage1_hybrid_topk_vec: int = 30
    deepread_rerank_api_key: str = ""
    deepread_rerank_base_url: str = "https://api.siliconflow.cn/v1"
    deepread_rerank_model: str = "Qwen/Qwen3-Reranker-8B"
    deepread_neighbor_window: str = "1,-1"

    @classmethod
    def from_env(cls, backend: BackendName | None = None) -> "AppConfig":
        selected_backend = (backend or os.getenv("RAG_BACKEND", "openviking_rag")).replace(
            "-", "_"
        )
        if selected_backend not in {"openviking_rag", "deepread", "ovbot"}:
            raise ValueError("RAG_BACKEND must be 'openviking_rag', 'deepread', or 'ovbot'")

        return cls(
            backend=selected_backend,  # type: ignore[arg-type]
            service_name=os.getenv("SERVICE_NAME", "yq-qa"),
            qa_db_path=os.getenv("YQ_QA_DB", "data/yq-qa.sqlite3"),
            qa_rag_manager_base_url=os.getenv(
                "YQ_RAG_MANAGER_BASE_URL", "http://127.0.0.1:18081"
            ).rstrip("/"),
            qa_rag_manager_timeout_seconds=_env_float(
                "YQ_QA_RAG_MANAGER_TIMEOUT_SECONDS", 1200.0
            ),
            qa_default_method_ids=_split_paths(os.getenv("YQ_QA_DEFAULT_METHOD_IDS")),
            qa_max_concurrent_tasks=_env_int("YQ_QA_MAX_CONCURRENT_TASKS", 4),
            qa_method_timeout_seconds=_env_float("YQ_QA_METHOD_TIMEOUT_SECONDS", 1200.0),
            qa_merge_enabled=_env_bool("YQ_QA_MERGE_ENABLED", False),
            qa_merge_base_url=os.getenv("YQ_QA_MERGE_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or None,
            qa_merge_api_key=os.getenv("YQ_QA_MERGE_API_KEY")
            or os.getenv("OPENAI_API_KEY", ""),
            qa_merge_model=os.getenv("YQ_QA_MERGE_MODEL")
            or os.getenv("OPENAI_MODEL", ""),
            qa_merge_timeout_seconds=_env_float("YQ_QA_MERGE_TIMEOUT_SECONDS", 300.0),
            qa_merge_temperature=_env_float("YQ_QA_MERGE_TEMPERATURE", 0.2),
            openviking_rag_project_path=os.getenv(
                "OPENVIKING_RAG_PROJECT_PATH",
                r"D:\project\mine\OpenViking\benchmark\RAG",
            ),
            openviking_root_path=os.getenv(
                "OPENVIKING_ROOT_PATH",
                r"D:\project\mine\OpenViking",
            ),
            openviking_rag_ov_conf=os.getenv(
                "OPENVIKING_RAG_OV_CONF",
                r"D:\project\mine\OpenViking\benchmark\RAG\ov.conf",
            ),
            openviking_rag_vector_store=os.getenv("OPENVIKING_RAG_VECTOR_STORE", ""),
            openviking_rag_mode=os.getenv("OPENVIKING_RAG_MODE", "local"),
            openviking_rag_server_url=os.getenv(
                "OPENVIKING_RAG_SERVER_URL", "http://127.0.0.1:1933"
            ),
            openviking_rag_api_key=os.getenv("OPENVIKING_RAG_API_KEY", ""),
            openviking_rag_retrieval_topk=_env_int("OPENVIKING_RAG_RETRIEVAL_TOPK", 5),
            openviking_rag_retrieval_instruction=os.getenv(
                "OPENVIKING_RAG_RETRIEVAL_INSTRUCTION", ""
            ),
            openviking_rag_target_uri=os.getenv(
                "OPENVIKING_RAG_TARGET_URI", "viking://resources"
            ),
            openviking_rag_use_relations=_env_bool("OPENVIKING_RAG_USE_RELATIONS", False),
            openviking_rag_relations_topk=_env_int("OPENVIKING_RAG_RELATIONS_TOPK", 0),
            openviking_rag_use_query_expansion=_env_bool(
                "OPENVIKING_RAG_USE_QUERY_EXPANSION", False
            ),
            openviking_rag_link_strategy=os.getenv(
                "OPENVIKING_RAG_LINK_STRATEGY", "llm_review"
            ),
            openviking_rag_llm_model=os.getenv("OPENVIKING_RAG_LLM_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("OPENROUTER_MODEL", ""),
            openviking_rag_llm_base_url=os.getenv(
                "OPENVIKING_RAG_LLM_BASE_URL",
                os.getenv("OPENAI_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            ),
            openviking_rag_llm_api_key=os.getenv("OPENVIKING_RAG_LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENROUTER_API_KEY", ""),
            openviking_rag_llm_temperature=_env_float("OPENVIKING_RAG_LLM_TEMPERATURE", 0.0),
            openviking_rag_embedding_model=os.getenv(
                "OPENVIKING_RAG_EMBEDDING_MODEL", "doubao-embedding-vision-251215"
            ),
            openviking_rag_embedding_base_url=os.getenv(
                "OPENVIKING_RAG_EMBEDDING_BASE_URL",
                "https://ark.cn-beijing.volces.com/api/v3",
            ),
            openviking_rag_embedding_api_key=os.getenv(
                "OPENVIKING_RAG_EMBEDDING_API_KEY", ""
            ),
            ovbot_base_url=os.getenv("OVBOT_BASE_URL", "http://127.0.0.1:18790"),
            ovbot_chat_path=os.getenv("OVBOT_CHAT_PATH", "/bot/v1/chat"),
            ovbot_chat_stream_path=os.getenv(
                "OVBOT_CHAT_STREAM_PATH", "/bot/v1/chat/stream"
            ),
            ovbot_api_key=os.getenv("OVBOT_API_KEY", ""),
            ovbot_channel_id=os.getenv("OVBOT_CHANNEL_ID") or None,
            ovbot_timeout_seconds=_env_float("OVBOT_TIMEOUT_SECONDS", 300.0),
            deepread_project_path=os.getenv(
                "DEEPREAD_PROJECT_PATH", r"D:\project\postgraduate\ruc-ov-eval"
            ),
            deepread_extra_pythonpath=_split_paths(os.getenv("DEEPREAD_EXTRA_PYTHONPATH")),
            deepread_corpus_paths=_split_paths(os.getenv("DEEPREAD_CORPUS_PATHS")),
            deepread_log_dir=os.getenv("DEEPREAD_LOG_DIR", "logs/deepread"),
            deepread_model=os.getenv("DEEPREAD_MODEL")
            or os.getenv("OPENAI_MODEL")
            or os.getenv("OPENROUTER_MODEL", ""),
            deepread_base_url=os.getenv("DEEPREAD_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("OPENROUTER_BASE_URL"),
            deepread_api_key=os.getenv("DEEPREAD_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENROUTER_API_KEY", ""),
            deepread_max_rounds=_env_int("DEEPREAD_MAX_ROUNDS", 50),
            deepread_temperature=_env_float("DEEPREAD_TEMPERATURE", 0.0),
            deepread_enable_multimodal=_env_bool("DEEPREAD_ENABLE_MULTIMODAL", False),
            deepread_tool_fallback=_env_bool("DEEPREAD_TOOL_FALLBACK", True),
            deepread_enable_reasoning=_env_bool("DEEPREAD_ENABLE_REASONING", True),
            deepread_embedding_model=os.getenv(
                "DEEPREAD_EMBEDDING_MODEL",
                os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-8B"),
            ),
            deepread_embed_base_url=os.getenv(
                "DEEPREAD_EMBED_BASE_URL",
                os.getenv("EMBED_BASE_URL", "https://api.siliconflow.cn/v1"),
            ),
            deepread_embed_api_key=os.getenv(
                "DEEPREAD_EMBED_API_KEY", os.getenv("EMBED_API_KEY", "")
            ),
            deepread_retrieval=os.getenv("DEEPREAD_RETRIEVAL") or None,
            deepread_enable_vector=_env_bool("DEEPREAD_ENABLE_VECTOR", False),
            deepread_enable_hybrid=_env_bool("DEEPREAD_ENABLE_HYBRID", False),
            deepread_enable_semantic=_env_bool("DEEPREAD_ENABLE_SEMANTIC", False),
            deepread_disable_bm25=_env_bool("DEEPREAD_DISABLE_BM25", False),
            deepread_disable_regex=_env_bool("DEEPREAD_DISABLE_REGEX", False),
            deepread_disable_read=_env_bool("DEEPREAD_DISABLE_READ", False),
            deepread_bm25_topk=_env_int("DEEPREAD_BM25_TOPK", 1),
            deepread_regex_topk=_env_int("DEEPREAD_REGEX_TOPK", 1),
            deepread_vector_topk=_env_int("DEEPREAD_VECTOR_TOPK", 1),
            deepread_hybrid_topk=_env_int("DEEPREAD_HYBRID_TOPK", 1),
            deepread_hybrid_topk_bm25=_env_int("DEEPREAD_HYBRID_TOPK_BM25", 30),
            deepread_hybrid_topk_vec=_env_int("DEEPREAD_HYBRID_TOPK_VEC", 30),
            deepread_hybrid_bm25_weight=_env_float("DEEPREAD_HYBRID_BM25_WEIGHT", 0.5),
            deepread_hybrid_vector_weight=_env_float("DEEPREAD_HYBRID_VECTOR_WEIGHT", 0.5),
            deepread_semantic_stage1=os.getenv("DEEPREAD_SEMANTIC_STAGE1", "vector"),
            deepread_semantic_topk1=_env_int("DEEPREAD_SEMANTIC_TOPK1", 30),
            deepread_semantic_topk2=_env_int("DEEPREAD_SEMANTIC_TOPK2", 2),
            deepread_semantic_stage1_hybrid_topk_bm25=_env_int(
                "DEEPREAD_SEMANTIC_STAGE1_HYBRID_TOPK_BM25", 30
            ),
            deepread_semantic_stage1_hybrid_topk_vec=_env_int(
                "DEEPREAD_SEMANTIC_STAGE1_HYBRID_TOPK_VEC", 30
            ),
            deepread_rerank_api_key=os.getenv(
                "DEEPREAD_RERANK_API_KEY",
                os.getenv("RERANK_API_KEY", os.getenv("SILICONFLOW_API_KEY", "")),
            ),
            deepread_rerank_base_url=os.getenv(
                "DEEPREAD_RERANK_BASE_URL", os.getenv("RERANK_BASE_URL", "https://api.siliconflow.cn/v1")
            ),
            deepread_rerank_model=os.getenv(
                "DEEPREAD_RERANK_MODEL", os.getenv("RERANK_MODEL", "Qwen/Qwen3-Reranker-8B")
            ),
            deepread_neighbor_window=os.getenv("DEEPREAD_NEIGHBOR_WINDOW", "1,-1"),
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "service_name": self.service_name,
            "legacy_backend": self.backend,
            "qa": {
                "db_path": self.qa_db_path,
                "rag_manager_base_url": self.qa_rag_manager_base_url,
                "rag_manager_timeout_seconds": self.qa_rag_manager_timeout_seconds,
                "default_method_ids": self.qa_default_method_ids,
                "max_concurrent_tasks": self.qa_max_concurrent_tasks,
                "method_timeout_seconds": self.qa_method_timeout_seconds,
            },
            "merge": {
                "enabled": self.qa_merge_enabled,
                "base_url": self.qa_merge_base_url,
                "model": self.qa_merge_model,
                "timeout_seconds": self.qa_merge_timeout_seconds,
                "temperature": self.qa_merge_temperature,
                "api_key": _mask_secret(self.qa_merge_api_key),
            },
        }


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
