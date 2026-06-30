from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class EvalItem:
    id: int
    sample_id: str
    question: str
    gold_answers: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    category: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreparedDocument:
    document_id: str
    path: str
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
