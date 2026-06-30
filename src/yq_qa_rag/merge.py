from __future__ import annotations

from typing import Any

import httpx

from yq_qa_rag.models import MethodAnswer, QaRuntimeConfig


class MergeClient:
    def __init__(self, config: QaRuntimeConfig) -> None:
        self.enabled = config.merge_enabled
        self.base_url = config.merge_base_url
        self.api_key = config.merge_api_key
        self.model = config.merge_model
        self.timeout = config.merge_timeout_seconds
        self.temperature = config.merge_temperature

    def available(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key and self.model)

    async def merge(self, question: str, results: list[MethodAnswer]) -> str:
        if not self.available():
            raise RuntimeError("merge model is not configured")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a careful QA answer synthesizer. Merge multiple RAG "
                        "answers into one concise final answer. Use only the provided "
                        "answers and cite uncertainty or conflicts explicitly."
                    ),
                },
                {"role": "user", "content": _build_merge_prompt(question, results)},
            ],
            "temperature": self.temperature,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"merge model {response.status_code}: {response.text}")
        return _extract_answer(response.json())


def _build_merge_prompt(question: str, results: list[MethodAnswer]) -> str:
    blocks = [f"Question:\n{question}", "RAG answers:"]
    for index, result in enumerate(results, start=1):
        if result.status == "failed":
            blocks.append(f"[{index}] method={result.method_id}\nFAILED: {result.error}")
            continue
        source_lines = []
        for source in result.sources[:5]:
            title = source.title or source.source_id or "source"
            snippet = (source.snippet or "").replace("\n", " ").strip()
            source_lines.append(f"- {title}: {snippet[:500]}")
        blocks.append(
            "\n".join(
                [
                    f"[{index}] method={result.method_id}",
                    f"answer:\n{result.answer}",
                    "sources:",
                    "\n".join(source_lines) if source_lines else "- none",
                ]
            )
        )
    blocks.append(
        "Return one final answer in Chinese if the user asked Chinese, otherwise keep the "
        "question language. Do not invent facts absent from the provided answers."
    )
    return "\n\n".join(blocks)


def _extract_answer(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
            if parts:
                return "".join(parts)
    raise RuntimeError("merge model response does not contain an answer")
