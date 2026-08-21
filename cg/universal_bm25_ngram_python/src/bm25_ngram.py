"""Okapi BM25 over word tokens plus character n-grams, in memory."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field as dataclass_field
from math import log
from re import compile as re_compile
from typing import Any, Callable, Iterable, Mapping, Sequence

_WORD = re_compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Field:
    """One searchable field and how much it counts."""

    name: str
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("a field needs a name")
        if self.weight <= 0:
            raise ValueError(f"weight must be positive, got {self.weight}")


@dataclass(frozen=True)
class Hit:
    """One matching record."""

    item_id: str
    score: float
    fields: tuple[str, ...] = dataclass_field(default_factory=tuple)


def word_tokens(text: str, stopwords: Iterable[str] | None = None) -> list[str]:
    """Lower-case alphanumeric tokens, stopwords removed."""
    banned = {item.casefold() for item in stopwords} if stopwords is not None else set()
    tokens: list[str] = []
    for match in _WORD.finditer(text.casefold()):
        token = match.group(0)
        if token in banned:
            continue
        tokens.append(token)
    return tokens


def char_ngrams(text: str, n: int = 3, stopwords: Iterable[str] | None = None) -> list[str]:
    """Character n-grams of each word token. Short words are kept whole."""
    if n < 1:
        raise ValueError("n must be >= 1")
    grams: list[str] = []
    for token in word_tokens(text, stopwords):
        if len(token) <= n:
            grams.append(token)
            continue
        for index in range(len(token) - n + 1):
            grams.append(token[index : index + n])
    return grams


def search(
    records: Mapping[str, Any],
    query: Any,
    fields: Sequence[Field | str],
    *,
    ngram: int = 3,
    ngram_weight: float = 0.4,
    k1: float = 1.5,
    b: float = 0.75,
    stopwords: Iterable[str] | None = None,
    read: Callable[[Any, str], Any] | None = None,
    limit: int | None = None,
    ids: Iterable[str] | None = None,
    min_score: float = 0.0,
    min_ratio: float = 0.0,
    require_word_hit: bool = False,
) -> list[Hit]:
    """Rank records with word BM25 plus character n-gram BM25.

    Query terms need not all match. An empty query returns no hits.
    Weak hits can be dropped with min_score, min_ratio (fraction of the top
    score), and require_word_hit (n-gram-only matches are junk).
    """
    if ngram < 1:
        raise ValueError("n must be >= 1")
    if ngram_weight < 0:
        raise ValueError("ngram_weight must be >= 0")
    if k1 < 0:
        raise ValueError("k1 must be >= 0")
    if not 0 <= b <= 1:
        raise ValueError("b must be in [0, 1]")
    if min_score < 0:
        raise ValueError("min_score must be >= 0")
    if not 0 <= min_ratio <= 1:
        raise ValueError("min_ratio must be in [0, 1]")
    resolved = [Field(name=entry) if isinstance(entry, str) else entry for entry in fields]
    if not resolved:
        raise ValueError("searching needs at least one field")
    if not isinstance(query, str) or not query.strip():
        return []

    reader = read or _default_reader
    pool = list(ids) if ids is not None else list(records)
    query_words = word_tokens(query, stopwords)
    query_grams = char_ngrams(query, ngram, stopwords)
    if not query_words and not query_grams:
        return []

    texts: dict[str, dict[str, str]] = {}
    for item_id in pool:
        record = records.get(item_id)
        texts[item_id] = {entry.name: _as_text(reader(record, entry.name)) for entry in resolved}

    word_index = _build_index(pool, texts, resolved, lambda text: word_tokens(text, stopwords))
    gram_index = _build_index(pool, texts, resolved, lambda text: char_ngrams(text, ngram, stopwords))

    hits: list[Hit] = []
    for item_id in pool:
        word_score = 0.0
        gram_score = 0.0
        matched: list[str] = []
        for entry in resolved:
            word_part = _bm25_score(query_words, item_id, entry.name, word_index, k1, b)
            gram_part = _bm25_score(query_grams, item_id, entry.name, gram_index, k1, b)
            if word_part > 0 or gram_part > 0:
                matched.append(entry.name)
            word_score += entry.weight * word_part
            gram_score += entry.weight * ngram_weight * gram_part
        if require_word_hit and word_score <= 0:
            continue
        score = word_score + gram_score
        if score > 0 and score >= min_score:
            hits.append(Hit(item_id=item_id, score=round(score, 4), fields=tuple(matched)))

    hits.sort(key=lambda hit: (-hit.score, hit.item_id))
    if hits and min_ratio > 0:
        floor = hits[0].score * min_ratio
        hits = [hit for hit in hits if hit.score >= floor]
    return hits[:limit] if limit is not None else hits


def _build_index(
    pool: Sequence[str],
    texts: Mapping[str, Mapping[str, str]],
    fields: Sequence[Field],
    tokenizer: Callable[[str], list[str]],
) -> dict[str, Any]:
    tf: dict[str, dict[str, Counter[str]]] = {entry.name: {} for entry in fields}
    length: dict[str, dict[str, int]] = {entry.name: {} for entry in fields}
    df: dict[str, Counter[str]] = {entry.name: Counter() for entry in fields}
    for entry in fields:
        for item_id in pool:
            tokens = tokenizer(texts[item_id][entry.name])
            counts = Counter(tokens)
            tf[entry.name][item_id] = counts
            length[entry.name][item_id] = len(tokens)
            for term in counts:
                df[entry.name][term] += 1
    n_docs = max(len(pool), 1)
    avgdl: dict[str, float] = {}
    for entry in fields:
        total = sum(length[entry.name].values())
        avgdl[entry.name] = total / n_docs if n_docs else 1.0
        if avgdl[entry.name] <= 0:
            avgdl[entry.name] = 1.0
    return {"tf": tf, "df": df, "length": length, "avgdl": avgdl, "n_docs": n_docs}


def _bm25_score(
    query_terms: Sequence[str],
    item_id: str,
    field_name: str,
    index: Mapping[str, Any],
    k1: float,
    b: float,
) -> float:
    if not query_terms:
        return 0.0
    tf_map: Counter[str] = index["tf"][field_name][item_id]
    df_map: Counter[str] = index["df"][field_name]
    doc_len = index["length"][field_name][item_id]
    avgdl = index["avgdl"][field_name]
    n_docs = index["n_docs"]
    score = 0.0
    seen: set[str] = set()
    for term in query_terms:
        if term in seen:
            continue
        seen.add(term)
        tf = tf_map.get(term, 0)
        if tf <= 0:
            continue
        doc_freq = df_map[term]
        idf = log(1.0 + (n_docs - doc_freq + 0.5) / (doc_freq + 0.5))
        denom = tf + k1 * (1.0 - b + b * doc_len / avgdl)
        score += idf * (tf * (k1 + 1.0) / denom)
    return score


def _default_reader(record: Any, name: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return " ".join(_as_text(item) for item in value)
    return str(value)
