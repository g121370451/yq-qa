from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tqdm import tqdm

from yq_rag_manager_eval.client import RagManagerClient
from yq_rag_manager_eval.datasets import load_items, prepare_documents
from yq_rag_manager_eval.judge import judge_answer, judge_enabled
from yq_rag_manager_eval.metrics import best_f1, evidence_recall
from yq_rag_manager_eval.models import EvalItem


STAGES = {"import", "gen", "eval", "gen+eval", "del", "all"}


def run_eval(config: dict[str, Any], stage: str | None = None) -> dict[str, Any]:
    selected_stage = _selected_stage(config, stage)
    output_dir = _output_dir(config)
    client, method_id = _prepare_client(config, start=selected_stage != "eval")

    summary: dict[str, Any] = {
        "stage": selected_stage,
        "dataset": (config.get("dataset") or {}).get("name", "unknown"),
        "method_id": method_id,
        "output_dir": str(output_dir),
    }

    if selected_stage in {"all", "import"}:
        summary["import"] = import_documents(config, client, method_id, output_dir)

    if selected_stage in {"all", "gen", "gen+eval"}:
        summary["gen"] = generate_answers(config, client, method_id, output_dir)

    if selected_stage in {"all", "eval", "gen+eval"}:
        summary["eval"] = evaluate_answers(config, output_dir)

    if selected_stage == "del":
        summary["del"] = delete_imported_documents(config, client, method_id, output_dir)

    _write_report(output_dir, summary)
    return summary


def import_documents(
    config: dict[str, Any],
    client: RagManagerClient,
    method_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    ingestion = config.get("ingestion") or {}
    if ingestion.get("enabled", True) is False:
        summary = {"enabled": False, "ingested_documents": 0, "failed_ingestions": 0}
        _write_json(output_dir / "ingested_documents.json", [])
        return summary

    started = time.perf_counter()
    options = dict(ingestion.get("options", {}))
    documents = prepare_documents(config)
    submit_documents = documents
    max_concurrency = int(ingestion.get("max_concurrency") or ingestion.get("max_workers") or 1)
    poll_interval = float(ingestion.get("poll_interval_sec") or 2.0)
    wait_timeout = _float_or_none(ingestion.get("wait_timeout")) or 3600.0

    job = client.create_ingestion_job(
        method_id,
        [
            {
                "document_id": document.document_id,
                "path": document.path,
                "title": document.title,
                "metadata": document.metadata,
                "options": {},
            }
            for document in submit_documents
        ],
        options=options,
        max_concurrency=max_concurrency,
        poll_interval_sec=poll_interval,
    )
    _write_json(output_dir / "ingestion_job.json", job)

    deadline = time.perf_counter() + wait_timeout
    while job.get("status") not in {"completed", "failed"}:
        if time.perf_counter() > deadline:
            raise TimeoutError(f"ingestion job did not finish within {wait_timeout}s")
        time.sleep(poll_interval)
        job = client.get_ingestion_job(method_id, job["job_id"])
        _write_json(output_dir / "ingestion_job.json", job)

    results = [
        {
            "document_id": item.get("document_id"),
            "path": item.get("path"),
            "status": item.get("status"),
            "response": item.get("response"),
            "error": item.get("error"),
            "task_id": item.get("task_id"),
            "root_uri": item.get("root_uri"),
        }
        for item in job.get("items") or []
    ]
    failed = [item for item in results if item.get("error") or item.get("status") == "failed"]
    if failed and bool(ingestion.get("fail_fast", True)):
        raise RuntimeError(f"document ingestion failed: {failed[0]['error']}")

    summary = {
        "prepared_documents": len(documents),
        "submitted_documents": len(submit_documents),
        "ingested_documents": int((job.get("counts") or {}).get("completed") or 0),
        "failed_ingestions": int((job.get("counts") or {}).get("failed") or 0),
        "elapsed_sec": time.perf_counter() - started,
        "folder_import": bool(ingestion.get("folder_import", False)),
        "job_id": job.get("job_id"),
        "job_status": job.get("status"),
        "job_counts": job.get("counts"),
    }
    _write_json(output_dir / "ingested_documents.json", results)
    return summary


def generate_answers(
    config: dict[str, Any],
    client: RagManagerClient,
    method_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    items = _load_limited_items(config)
    max_workers = int((config.get("execution") or {}).get("max_workers", 1))
    request_cfg = config.get("request") or {}
    options = dict(request_cfg.get("options", {}))
    mode = str(request_cfg.get("mode", "chat")).lower()
    top_k = int(request_cfg.get("top_k", options.pop("top_k", 5)))

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    generated_file = output_dir / "generated_answers.json"
    progress_file = output_dir / "generation_progress.json"
    _write_generation_progress(
        progress_file,
        config,
        method_id,
        mode,
        total=len(items),
        completed=0,
        failed=0,
        started=started,
        status="running",
    )
    _log(
        f"gen started: dataset={(config.get('dataset') or {}).get('name', 'unknown')} "
        f"method={method_id} mode={mode} total={len(items)} workers={max_workers}"
    )
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_run_one, client, method_id, item, options, mode, top_k)
            for item in items
        ]
        with tqdm(total=len(futures), desc="gen", unit="query") as progress:
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                status = "failed" if result.get("error") else "ok"
                _log(
                    f"gen item {status}: index={result.get('_global_index')} "
                    f"sample={result.get('sample_id')}"
                )
                if result.get("error"):
                    _log(f"gen error: {result.get('error')}")
                _write_generated(generated_file, config, results)
                failed = sum(1 for item in results if item.get("error"))
                _write_generation_progress(
                    progress_file,
                    config,
                    method_id,
                    mode,
                    total=len(items),
                    completed=len(results),
                    failed=failed,
                    started=started,
                    status="running",
                )
                avg_latency = _avg_latency_sec(results)
                progress.set_postfix(failed=failed, avg_latency=f"{avg_latency:.1f}s")
                progress.update(1)

    results.sort(key=lambda item: item["_global_index"])
    summary = _generation_summary(config, results, time.perf_counter() - started, mode)
    _write_generated(generated_file, config, results, summary)
    _write_generation_progress(
        progress_file,
        config,
        method_id,
        mode,
        total=len(items),
        completed=len(results),
        failed=sum(1 for item in results if item.get("error")),
        started=started,
        status="completed",
    )
    _log(
        f"gen completed: total={summary['total_queries']} failed={summary['failed_queries']} "
        f"elapsed={summary['elapsed_sec']:.1f}s"
    )
    return summary


def evaluate_answers(config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    generated_file = output_dir / "generated_answers.json"
    if not generated_file.exists():
        raise FileNotFoundError(f"generated answers not found: {generated_file}")

    payload = json.loads(generated_file.read_text(encoding="utf-8"))
    results = payload.get("results") or []
    evaluated: list[dict[str, Any]] = []
    eval_workers = int(
        (config.get("execution") or {}).get(
            "eval_workers",
            (config.get("execution") or {}).get("max_workers", 1),
        )
    )
    _log(
        f"eval started: dataset={(config.get('dataset') or {}).get('name', 'unknown')} "
        f"items={len(results)} workers={eval_workers} judge={judge_enabled(config)}"
    )
    with ThreadPoolExecutor(max_workers=eval_workers) as executor:
        futures = [executor.submit(_evaluate_one, config, item) for item in results]
        with tqdm(total=len(futures), desc="eval", unit="query") as progress:
            for future in as_completed(futures):
                result = future.result()
                evaluated.append(result)
                metrics = result.get("metrics") or {}
                status = "failed" if result.get("error") else "ok"
                _log(
                    f"eval item {status}: index={result.get('_global_index')} "
                    f"sample={result.get('sample_id')} f1={metrics.get('F1', 0):.3f} "
                    f"recall={metrics.get('Recall', 0):.3f}"
                )
                _write_json(output_dir / "qa_eval_detailed_results.json", {"results": evaluated})
                progress.set_postfix(
                    failed=sum(1 for item in evaluated if item.get("error")),
                    f1=f"{_avg_metric(evaluated, 'F1'):.3f}",
                    judge=f"{_avg_metric(evaluated, 'AccuracyNormalized'):.3f}",
                )
                progress.update(1)
    evaluated.sort(key=lambda item: item["_global_index"])

    summary = _eval_summary(config, evaluated)
    _write_json(output_dir / "qa_eval_detailed_results.json", {"results": evaluated})
    generated_summary = dict(payload.get("summary") or {})
    generated_summary["evaluation"] = summary
    _write_generated(generated_file, config, evaluated, generated_summary)
    _log(
        f"eval completed: total={summary['total_queries']} failed={summary['failed_queries']} "
        f"avg_f1={summary['avg_f1']:.4f} avg_recall={summary['avg_recall']:.4f}"
    )
    return summary


def _evaluate_one(config: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    answer = str((item.get("llm") or {}).get("final_answer") or "")
    gold_answers = [str(value) for value in item.get("gold_answers") or []]
    recall_texts = [
        str(value) for value in (item.get("retrieval") or {}).get("recall_texts") or []
    ]
    evidence = [str(value) for value in item.get("evidence") or []]
    metrics = dict(item.get("metrics") or {})
    metrics.update(
        {
            "F1": best_f1(answer, gold_answers),
            "Recall": evidence_recall(recall_texts, evidence),
        }
    )
    if judge_enabled(config):
        try:
            judge_result = judge_answer(
                config,
                question=str(item.get("question") or ""),
                gold_answers=gold_answers,
                answer=answer,
            )
            metrics["Accuracy"] = judge_result["score"]
            metrics["AccuracyNormalized"] = judge_result["score"] / 4
            item["llm_evaluation"] = {
                "prompt_used": judge_result["prompt_type"],
                "reasoning": judge_result["reasoning"],
                "normalized_score": judge_result["score"],
            }
        except Exception as exc:
            metrics["Accuracy"] = 0
            metrics["AccuracyNormalized"] = 0.0
            item["llm_evaluation"] = {
                "prompt_used": "Generic_0-4",
                "reasoning": f"Judge failed: {exc}",
                "normalized_score": 0,
            }
    item["metrics"] = metrics
    return item


def delete_imported_documents(
    config: dict[str, Any],
    client: RagManagerClient,
    method_id: str,
    output_dir: Path,
) -> dict[str, Any]:
    ingested_file = output_dir / "ingested_documents.json"
    if not ingested_file.exists():
        return {"deleted": 0, "failed": 0, "message": f"not found: {ingested_file}"}

    records = json.loads(ingested_file.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    for record in records:
        document_id = str(record.get("document_id") or "")
        if not document_id:
            continue
        try:
            response = client.delete_document(method_id, document_id)
            results.append(
                {
                    "document_id": document_id,
                    "status": "deleted",
                    "response": response,
                    "error": None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "document_id": document_id,
                    "status": "failed",
                    "response": None,
                    "error": str(exc),
                }
            )
    _write_json(output_dir / "deleted_documents.json", results)
    return {
        "deleted": sum(1 for item in results if not item.get("error")),
        "failed": sum(1 for item in results if item.get("error")),
    }


def _selected_stage(config: dict[str, Any], stage: str | None) -> str:
    value = stage or (config.get("execution") or {}).get("stage") or "all"
    value = str(value).lower()
    if value == "geneval":
        value = "gen+eval"
    if value == "ingest":
        value = "import"
    if value not in STAGES:
        raise ValueError(f"unsupported stage: {value}")
    return value


def _prepare_client(
    config: dict[str, Any],
    *,
    start: bool,
) -> tuple[RagManagerClient, str]:
    manager_cfg = config["rag_manager"]
    method_id = manager_cfg["method_id"]
    client = RagManagerClient(
        base_url=manager_cfg.get("base_url", "http://127.0.0.1:18081"),
        timeout=float(manager_cfg.get("timeout_seconds", 600)),
    )
    client.ensure_method(method_id, manager_cfg)
    if start and manager_cfg.get("auto_start", True):
        client.ensure_started(method_id)
    return client, method_id


def _output_dir(config: dict[str, Any]) -> Path:
    output_dir = Path(str((config.get("output") or {}).get("output_dir", "outputs"))).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _load_limited_items(config: dict[str, Any]) -> list[EvalItem]:
    items = load_items(config)
    max_queries = (config.get("execution") or {}).get("max_queries")
    if max_queries is not None:
        return items[: int(max_queries)]
    return items


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _run_one(
    client: RagManagerClient,
    method_id: str,
    item: EvalItem,
    options: dict[str, Any],
    mode: str,
    top_k: int,
) -> dict[str, Any]:
    try:
        if mode == "retrieve":
            response = client.retrieve(method_id, item, top_k, options)
            answer = _answer_from_sources(response.get("sources") or [])
        elif mode == "chat":
            response = client.chat(method_id, item, options)
            answer = str(response.get("answer", ""))
        else:
            raise ValueError(f"unsupported request.mode: {mode}")
        sources = response.get("sources") or []
        source_texts = [
            str(source.get("snippet") or source.get("metadata") or "")
            for source in sources
            if isinstance(source, dict)
        ]
        return {
            "_global_index": item.id,
            "sample_id": item.sample_id,
            "question": item.question,
            "gold_answers": item.gold_answers,
            "category": item.category,
            "evidence": item.evidence,
            "retrieval": {
                "latency_sec": float(response.get("latency_ms") or 0) / 1000,
                "sources": sources,
                "recall_texts": source_texts,
            },
            "llm": {"final_answer": answer},
            "metrics": {},
            "backend_metadata": response.get("backend_metadata", {}),
            "error": None,
        }
    except Exception as exc:
        return {
            "_global_index": item.id,
            "sample_id": item.sample_id,
            "question": item.question,
            "gold_answers": item.gold_answers,
            "category": item.category,
            "evidence": item.evidence,
            "retrieval": {"latency_sec": 0, "sources": [], "recall_texts": []},
            "llm": {"final_answer": ""},
            "metrics": {},
            "backend_metadata": {},
            "error": str(exc),
        }


def _answer_from_sources(sources: list[Any]) -> str:
    snippets = [
        str(source.get("snippet") or "")
        for source in sources
        if isinstance(source, dict) and source.get("snippet")
    ]
    return "\n\n".join(snippets)


def _generation_summary(
    config: dict[str, Any],
    results: list[dict[str, Any]],
    elapsed_sec: float,
    mode: str,
) -> dict[str, Any]:
    total = len(results)
    failed = sum(1 for item in results if item.get("error"))
    return {
        "dataset": (config.get("dataset") or {}).get("name", "unknown"),
        "method_id": config["rag_manager"]["method_id"],
        "mode": mode,
        "total_queries": total,
        "failed_queries": failed,
        "elapsed_sec": elapsed_sec,
        "avg_latency_sec": (
            sum(item["retrieval"]["latency_sec"] for item in results) / total if total else 0
        ),
    }


def _eval_summary(config: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    failed = sum(1 for item in results if item.get("error"))
    return {
        "dataset": (config.get("dataset") or {}).get("name", "unknown"),
        "method_id": config["rag_manager"]["method_id"],
        "total_queries": total,
        "failed_queries": failed,
        "avg_f1": sum(item["metrics"]["F1"] for item in results) / total if total else 0,
        "avg_recall": sum(item["metrics"]["Recall"] for item in results) / total if total else 0,
        "judge_enabled": judge_enabled(config),
        "avg_accuracy": (
            sum(item["metrics"].get("Accuracy", 0) for item in results) / total
            if total and judge_enabled(config)
            else None
        ),
        "avg_accuracy_normalized": (
            sum(item["metrics"].get("AccuracyNormalized", 0) for item in results) / total
            if total and judge_enabled(config)
            else None
        ),
    }


def _write_generated(
    path: Path,
    config: dict[str, Any],
    results: list[dict[str, Any]],
    summary: dict[str, Any] | None = None,
) -> None:
    sorted_results = sorted(results, key=lambda item: item["_global_index"])
    payload = {
        "summary": summary
        or {
            "dataset": (config.get("dataset") or {}).get("name", "unknown"),
            "total_queries": len(sorted_results),
        },
        "results": sorted_results,
    }
    _write_json(path, payload)


def _write_report(output_dir: Path, summary: dict[str, Any]) -> None:
    _write_json(output_dir / "rag_manager_eval_report.json", summary)


def _log(message: str) -> None:
    tqdm.write(f"[rag-manager-eval] {message}")


def _avg_latency_sec(results: list[dict[str, Any]]) -> float:
    if not results:
        return 0.0
    return sum(float((item.get("retrieval") or {}).get("latency_sec") or 0) for item in results) / len(
        results
    )


def _avg_metric(results: list[dict[str, Any]], metric: str) -> float:
    values = [
        float((item.get("metrics") or {}).get(metric) or 0)
        for item in results
    ]
    return sum(values) / len(values) if values else 0.0


def _write_generation_progress(
    path: Path,
    config: dict[str, Any],
    method_id: str,
    mode: str,
    *,
    total: int,
    completed: int,
    failed: int,
    started: float,
    status: str,
) -> None:
    _write_json(
        path,
        {
            "stage": "gen",
            "status": status,
            "dataset": (config.get("dataset") or {}).get("name", "unknown"),
            "method_id": method_id,
            "mode": mode,
            "total_queries": total,
            "completed_queries": completed,
            "pending_queries": max(0, total - completed),
            "failed_queries": failed,
            "elapsed_sec": time.perf_counter() - started,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        },
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
