from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx


def judge_enabled(config: dict[str, Any]) -> bool:
    judge = config.get("judge") or {}
    if "enabled" in judge:
        return _bool_value(judge.get("enabled"), default=True)
    return _bool_value(os.getenv("YQ_RAG_EVAL_JUDGE_ENABLED"), default=True)


def judge_answer(
    config: dict[str, Any],
    *,
    question: str,
    gold_answers: list[str],
    answer: str,
) -> dict[str, Any]:
    judge = _judge_config(config)
    model = _required(judge, "model")
    base_url = _required(judge, "base_url")
    api_key = _required(judge, "api_key")
    timeout = float(judge.get("timeout_seconds", 120))

    prompt = _judge_prompt(question, gold_answers, answer)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are an expert evaluator scoring how well an answer matches a gold answer.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(_chat_completions_url(base_url), json=payload, headers=headers)
    response.raise_for_status()
    data = response.json()
    content = str(data["choices"][0]["message"]["content"])
    parsed = _parse_score(content)
    return {
        "score": parsed["score"],
        "reasoning": parsed["reasoning"],
        "prompt_type": "Generic_0-4",
        "raw": content,
    }


def _judge_prompt(question: str, gold_answers: list[str], answer: str) -> str:
    gold = json.dumps(gold_answers, ensure_ascii=False)
    return f"""
Score Generated Answer vs Gold Answer from 0 to 4.
The Gold Answer is a JSON array; treat the array as the complete set of acceptable answers.

Rubric:
4: Fully captures the gold answer. No factual errors. Extra valid info is allowed.
3: Accurate but incomplete. No core factual errors.
2: Relevant but misses important facts, or has minor secondary errors.
1: Contains core factual errors.
0: Completely wrong, unrelated, or contradicts the gold answer.

Question: {question}
Gold Answer: {gold}
Generated Answer: {answer}

Respond only with JSON: {{"score": 0 to 4, "reasoning": "one short sentence"}}
""".strip()


def _parse_score(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
        score = int(data.get("score", 0))
        return {
            "score": max(0, min(4, score)),
            "reasoning": str(data.get("reasoning") or ""),
        }
    except Exception:
        match = re.search(r'"score"\s*:\s*([0-4])', content)
        if not match:
            match = re.search(r"\b([0-4])\b", content)
        score = int(match.group(1)) if match else 0
        return {
            "score": max(0, min(4, score)),
            "reasoning": f"Parse fallback from raw output: {content[:500]}",
        }


def _chat_completions_url(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return f"{url}/chat/completions"


def _judge_config(config: dict[str, Any]) -> dict[str, Any]:
    judge = config.get("judge") or {}
    return {
        "model": _first_nonempty(
            judge.get("model"),
            os.getenv("YQ_RAG_EVAL_JUDGE_MODEL"),
            os.getenv("OPENAI_MODEL"),
            "gpt-4o-mini",
        ),
        "base_url": _first_nonempty(
            judge.get("base_url"),
            os.getenv("YQ_RAG_EVAL_JUDGE_BASE_URL"),
            os.getenv("OPENAI_BASE_URL"),
            "https://api.openai.com/v1",
        ),
        "api_key": _first_nonempty(
            judge.get("api_key"),
            os.getenv("YQ_RAG_EVAL_JUDGE_API_KEY"),
            os.getenv("OPENAI_API_KEY"),
        ),
        "timeout_seconds": _first_nonempty(
            judge.get("timeout_seconds"),
            os.getenv("YQ_RAG_EVAL_JUDGE_TIMEOUT_SECONDS"),
            120,
        ),
    }


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return default
        if normalized in {"0", "false", "no", "n", "off", "disabled"}:
            return False
        if normalized in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
    return bool(value)


def _required(config: dict[str, Any], key: str) -> str:
    value = str(config.get(key) or "").strip()
    if not value:
        raise ValueError(
            f"judge.{key} is required; set judge.{key} or YQ_RAG_EVAL_JUDGE_{key.upper()}"
        )
    return value
