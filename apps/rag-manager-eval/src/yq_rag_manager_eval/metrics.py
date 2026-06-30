from __future__ import annotations

import re
from collections import Counter


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\u4e00-\u9fff\s]", "", text)
    return text.strip()


def token_f1(prediction: str, gold: str) -> float:
    pred_tokens = normalize_text(prediction).split()
    gold_tokens = normalize_text(gold).split()
    if not pred_tokens or not gold_tokens:
        return 1.0 if pred_tokens == gold_tokens else 0.0
    common = Counter(pred_tokens) & Counter(gold_tokens)
    same = sum(common.values())
    if same == 0:
        return 0.0
    precision = same / len(pred_tokens)
    recall = same / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def best_f1(prediction: str, gold_answers: list[str]) -> float:
    return max((token_f1(prediction, gold) for gold in gold_answers), default=0.0)


def evidence_recall(source_texts: list[str], evidence: list[str]) -> float:
    if not evidence:
        return 0.0
    haystack = normalize_text("\n".join(source_texts))
    hits = 0
    for item in evidence:
        needle = normalize_text(item)
        if needle and needle in haystack:
            hits += 1
    return hits / len(evidence)
