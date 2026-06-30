from __future__ import annotations

import csv
import fnmatch
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from yq_rag_manager_eval.models import EvalItem, PreparedDocument


def load_items(config: dict[str, Any]) -> list[EvalItem]:
    dataset = config.get("dataset", {})
    loader = str(dataset.get("loader", "auto")).lower()
    if loader == "ov_test_config":
        return _load_ov_test_config(dataset, config)
    if loader == "ov_test_adapter":
        return _load_ov_test_adapter(dataset, config)
    path = Path(str(dataset.get("qa_path") or dataset.get("path") or "")).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"dataset path not found: {path}")
    if loader == "auto":
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            loader = "jsonl"
        elif suffix == ".csv":
            loader = "csv"
        else:
            loader = "json"
    if loader == "jsonl":
        raw_items = _read_jsonl(path)
    elif loader == "csv":
        raw_items = _read_csv(path)
    elif loader == "json":
        raw_items = _read_json(path, dataset.get("items_key"))
    else:
        raise ValueError(f"unsupported dataset loader: {loader}")
    return _records_to_items(raw_items, dataset)


def _load_ov_test_adapter(dataset: dict[str, Any], config: dict[str, Any]) -> list[EvalItem]:
    adapter = _make_ov_test_adapter(dataset)
    samples = adapter.load_and_transform()
    max_queries = _max_queries(config)
    items: list[EvalItem] = []
    index = 0
    for sample in samples:
        for qa in sample.qa_pairs:
            if max_queries is not None and index >= max_queries:
                return items
            items.append(
                EvalItem(
                    id=index,
                    sample_id=str(sample.sample_id),
                    question=str(qa.question),
                    gold_answers=[str(value) for value in qa.gold_answers],
                    evidence=[str(value) for value in qa.evidence],
                    category=str(qa.category) if qa.category is not None else None,
                    metadata=dict(qa.metadata or {}),
                )
            )
            index += 1
    return items


def _load_ov_test_config(dataset: dict[str, Any], config: dict[str, Any]) -> list[EvalItem]:
    merged_dataset = _dataset_from_ov_test_config(dataset)
    return _load_ov_test_adapter(merged_dataset, config)


def prepare_documents(config: dict[str, Any]) -> list[PreparedDocument]:
    dataset = config.get("dataset", {})
    loader = str(dataset.get("loader", "auto")).lower()
    if loader == "ov_test_config":
        dataset = _dataset_from_ov_test_config(dataset)
        loader = "ov_test_adapter"
    if loader != "ov_test_adapter":
        raise ValueError("document ingestion currently requires dataset.loader=ov_test_adapter")

    ingestion = config.get("ingestion") or {}
    output_cfg = config.get("output") or {}
    doc_dir = Path(
        str(
            ingestion.get("doc_output_dir")
            or output_cfg.get("doc_output_dir")
            or Path(str(output_cfg.get("output_dir", "outputs"))) / "prepared_docs"
        )
    ).expanduser()
    doc_dir.mkdir(parents=True, exist_ok=True)

    adapter = _make_ov_test_adapter(dataset)
    standard_docs = adapter.data_prepare(str(doc_dir))
    prepared: list[PreparedDocument] = []
    for standard_doc in standard_docs:
        sample_id = str(getattr(standard_doc, "sample_id", "") or len(prepared))
        metadata = dict(getattr(standard_doc, "metadata", {}) or {})
        for path in getattr(standard_doc, "doc_paths", []) or []:
            doc_path = str(Path(str(path)).expanduser().resolve())
            stem = Path(doc_path).stem
            prepared.append(
                PreparedDocument(
                    document_id=_document_id(sample_id, stem, len(prepared)),
                    path=doc_path,
                    title=stem,
                    metadata={
                        **metadata,
                        "sample_id": sample_id,
                        "dataset": dataset.get("name"),
                    },
                )
            )
    max_documents = ingestion.get("max_documents")
    include_glob = _listify(ingestion.get("include_glob"))
    exclude_glob = _listify(ingestion.get("exclude_glob"))
    if include_glob:
        prepared = [
            item
            for item in prepared
            if _matches_any(item.path, include_glob) or _matches_any(item.document_id, include_glob)
        ]
    if exclude_glob:
        prepared = [
            item
            for item in prepared
            if not _matches_any(item.path, exclude_glob)
            and not _matches_any(item.document_id, exclude_glob)
        ]
    for index, item in enumerate(ingestion.get("extra_documents") or []):
        if not isinstance(item, dict):
            raise ValueError("ingestion.extra_documents items must be objects")
        path = str(Path(str(item["path"])).expanduser().resolve())
        title = item.get("title") or Path(path).stem
        prepared.append(
            PreparedDocument(
                document_id=str(item.get("document_id") or _document_id("extra", title, index)),
                path=path,
                title=str(title),
                metadata=dict(item.get("metadata") or {}),
            )
        )
    if max_documents is not None:
        prepared = prepared[: int(max_documents)]
    return prepared


def _make_ov_test_adapter(dataset: dict[str, Any]):
    ov_test_path = Path(
        str(dataset.get("ov_test_path", r"D:\project\postgraduate\ruc-ov-eval\ov_test"))
    ).expanduser().resolve()
    if str(ov_test_path) not in sys.path:
        sys.path.insert(0, str(ov_test_path))

    module_name = dataset["adapter_module"]
    class_name = dataset["adapter_class"]
    raw_path = dataset["raw_data_path"]
    module = importlib.import_module(module_name)
    adapter_cls = getattr(module, class_name)
    return adapter_cls(raw_file_path=raw_path)


def _dataset_from_ov_test_config(dataset: dict[str, Any]) -> dict[str, Any]:
    config_path = Path(str(dataset["config_path"])).expanduser().resolve()
    import yaml

    ov_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    ov_test_path = Path(str(dataset.get("ov_test_path") or config_path.parents[0])).expanduser().resolve()
    workspace_root = Path(
        str(dataset.get("workspace_root") or dataset.get("data_workspace_root") or ov_test_path.parent)
    ).expanduser().resolve()
    dataset_name = ov_cfg.get("dataset_name", "UnknownDataset")
    raw_path = str(ov_cfg.get("paths", {}).get("raw_data", "")).format(dataset_name=dataset_name)
    raw_path = Path(raw_path).expanduser()
    if not raw_path.is_absolute():
        raw_path = (workspace_root / raw_path).resolve()
    merged_dataset = {
        **dataset,
        "ov_test_path": str(ov_test_path),
        "adapter_module": ov_cfg.get("adapter", {}).get("module"),
        "adapter_class": ov_cfg.get("adapter", {}).get("class_name"),
        "raw_data_path": str(raw_path),
    }
    if not merged_dataset["adapter_module"] or not merged_dataset["adapter_class"]:
        raise ValueError(f"ov_test config misses adapter module/class: {config_path}")
    return merged_dataset


def _document_id(sample_id: str, stem: str, index: int) -> str:
    import re

    base = f"{sample_id}-{stem}" if sample_id != stem else stem
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-")
    return slug or f"doc-{index}"


def _matches_any(value: str, patterns: list[Any]) -> bool:
    path = Path(value)
    candidates = {str(value), path.name, path.stem}
    return any(
        fnmatch.fnmatchcase(candidate.lower(), str(pattern).lower())
        for pattern in patterns
        for candidate in candidates
    )


def _records_to_items(records: list[dict[str, Any]], dataset: dict[str, Any]) -> list[EvalItem]:
    fields = dataset.get("fields", {})
    question_field = fields.get("question", "question")
    answer_field = fields.get("answer", "answer")
    evidence_field = fields.get("evidence", "evidence")
    sample_field = fields.get("sample_id", "sample_id")
    category_field = fields.get("category", "category")
    items: list[EvalItem] = []
    for index, row in enumerate(records):
        question = _get(row, question_field)
        if not question:
            continue
        gold = _listify(_get(row, answer_field))
        evidence = _extract_evidence(_get(row, evidence_field))
        sample_id = _get(row, sample_field) or str(index)
        category = _get(row, category_field)
        items.append(
            EvalItem(
                id=index,
                sample_id=str(sample_id),
                question=str(question),
                gold_answers=[str(value) for value in gold],
                evidence=[str(value) for value in evidence],
                category=str(category) if category is not None else None,
                metadata={key: value for key, value in row.items() if key not in fields.values()},
            )
        )
    return items


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_json(path: Path, items_key: str | None) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if items_key:
        data = _get(data, items_key)
    if isinstance(data, dict):
        for key in ("results", "items", "data", "questions"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError(f"json dataset must resolve to a list: {path}")
    return [item for item in data if isinstance(item, dict)]


def _get(data: Any, path: str | None) -> Any:
    if path is None or path == "":
        return None
    current = data
    for part in str(path).split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _extract_evidence(value: Any) -> list[Any]:
    if isinstance(value, list):
        output = []
        for item in value:
            if isinstance(item, dict):
                output.append(item.get("evidence_text") or item.get("text") or item)
            else:
                output.append(item)
        return output
    if value:
        return [value]
    return []


def _max_queries(config: dict[str, Any]) -> int | None:
    value = (config.get("execution") or {}).get("max_queries")
    return int(value) if value is not None else None
