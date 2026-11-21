from __future__ import annotations

import re
import unicodedata
from typing import Callable, Iterable, TypeVar

from rapidfuzz import fuzz

T = TypeVar("T")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s.+-]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def best_match(
    query: str,
    items: Iterable[T],
    key: Callable[[T], str],
    threshold: int = 80,
) -> tuple[T | None, list[tuple[T, float]]]:
    q = normalize(query)
    pool = [(item, normalize(key(item))) for item in items]
    if not q or not pool:
        return None, []

    exact = [item for item, name in pool if name == q]
    if exact:
        return exact[0], [(exact[0], 100.0)]

    starts = [(item, name) for item, name in pool if name.startswith(q)]
    if starts:
        best = min(starts, key=lambda p: len(p[1]))[0]
        return best, [(i, 95.0) for i, _ in starts]

    word = re.compile(rf"\b{re.escape(q)}\b")
    contains = [(item, name) for item, name in pool if word.search(name)]
    if contains:
        best = min(contains, key=lambda p: len(p[1]))[0]
        return best, [(i, 90.0) for i, _ in contains]

    scored = sorted(
        ((item, float(max(fuzz.WRatio(q, name), fuzz.token_set_ratio(q, name)))) for item, name in pool),
        key=lambda p: p[1],
        reverse=True,
    )
    best_item, best_score = scored[0]
    return (best_item if best_score >= threshold else None), scored[:5]
