"""File-backed operations over playbook procedure documents in the global store."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from playbook import store
from playbook.bm25_ngram import Field, search as rank_search
from playbook.widget_api import widget

_SEARCH_FIELDS = (
    Field("id", 4.0),
    Field("title", 4.0),
    Field("tags", 3.0),
    Field("description", 2.5),
    Field("titles", 2.0),
)
_STOPWORDS = (
    "a",
    "an",
    "the",
    "to",
    "of",
    "and",
    "or",
    "for",
    "in",
    "on",
    "is",
    "it",
    "this",
    "that",
    "i",
    "we",
    "you",
    "my",
    "our",
    "want",
    "need",
    "please",
    "how",
    "do",
    "does",
    "into",
    "as",
    "with",
    "from",
)
DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 50


def search_procedures(query: str, limit: int | None = None) -> dict[str, Any]:
    """Search the global store. Empty query lists procedures (summaries only)."""
    catalog = _load_catalog()
    capped = _clamp_limit(limit)
    if not query.strip():
        ordered = sorted(catalog)
        hits = [_hit_payload(item_id, catalog[item_id], score=None, snippet=None) for item_id in ordered[:capped]]
        return {"ok": True, "query": "", "hits": hits, "limit": capped, "total": len(ordered)}
    ranked = rank_search(
        catalog,
        query,
        _SEARCH_FIELDS,
        ngram=3,
        ngram_weight=0.4,
        stopwords=_STOPWORDS,
        require_word_hit=True,
        min_ratio=0.5,
    )
    hits = [_hit_payload(hit.item_id, catalog[hit.item_id], score=hit.score, snippet=None) for hit in ranked[:capped]]
    return {"ok": True, "query": query, "hits": hits, "limit": capped, "total": len(ranked)}


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_SEARCH_LIMIT
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be an integer >= 1")
    return min(limit, MAX_SEARCH_LIMIT)


def _hit_payload(
    item_id: str,
    record: dict[str, Any],
    score: float | None,
    snippet: dict[str, str] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": item_id,
        "title": record.get("title") or item_id,
        "description": record.get("description", ""),
    }
    if score is not None:
        payload["score"] = score
    if snippet is not None:
        payload["snippet"] = snippet
    return payload


def _load_catalog() -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    root = store.procedures_dir()
    if not root.is_dir():
        return catalog
    for path in sorted(root.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            document = read_document(path)
        except (OSError, ValueError):
            continue
        procedure_id = document.get("id")
        if not isinstance(procedure_id, str) or not procedure_id.strip():
            procedure_id = path.stem
        steps = document.get("steps") if isinstance(document.get("steps"), list) else []
        titles = [
            step.get("title")
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("title"), str)
        ]
        title = document.get("title")
        if not isinstance(title, str) or not title.strip():
            title = procedure_id
        catalog[procedure_id] = {
            "id": procedure_id,
            "title": title.strip(),
            "description": document.get("description", ""),
            "tags": document.get("tags") if isinstance(document.get("tags"), list) else [],
            "titles": titles,
        }
    return catalog


def create_procedure(procedure_id: str, title: str, description: str, tags: list[str]) -> Path:
    target = store.procedure_path(procedure_id)
    if target.exists():
        raise FileExistsError(f"already exists: {target}")
    document = widget.new_procedure(procedure_id, title, description, tags)
    write_document(target, document)
    return target


def load_procedure(procedure_id: str, full: bool = True) -> dict[str, Any]:
    """Return the whole procedure in one call. full=False gives step titles only."""
    document = read_document(store.procedure_path(procedure_id))
    result = widget.validate_procedure(document)
    if not result.valid:
        raise ValueError("; ".join(f"{item.path}: {item.message}" for item in result.errors))
    steps = document.get("steps") if isinstance(document.get("steps"), list) else []
    listing = []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("title"), str):
            continue
        entry: dict[str, Any] = {"title": step["title"]}
        if full:
            entry["do"] = step.get("do")
        listing.append(entry)
    return {
        "ok": True,
        "id": document.get("id"),
        "title": document.get("title"),
        "description": document.get("description"),
        "full": full,
        "steps": listing,
    }


def start_procedure(procedure_id: str, title: str) -> dict[str, Any]:
    """Re-read one step by unique title: its do, its position, and the neighbouring titles."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    document = read_document(store.procedure_path(procedure_id))
    result = widget.validate_procedure(document)
    if not result.valid:
        raise ValueError("invalid procedure")
    steps = document.get("steps") if isinstance(document.get("steps"), list) else []
    marker = title.strip()
    index = next(
        (i for i, step in enumerate(steps) if isinstance(step, dict) and step.get("title") == marker),
        None,
    )
    if index is None:
        raise ValueError(f"no step titled {marker!r}")
    current = steps[index]
    previous = steps[index - 1] if index > 0 else None
    following = steps[index + 1] if index + 1 < len(steps) else None
    return {
        "ok": True,
        "id": document.get("id"),
        "title": document.get("title"),
        "at": current.get("title"),
        "position": f"{index + 1}/{len(steps)}",
        "do": current.get("do"),
        "prev": previous.get("title") if isinstance(previous, dict) else None,
        "next": following.get("title") if isinstance(following, dict) else None,
    }


def validate_procedure(procedure_id: str) -> dict[str, Any]:
    document = read_document(store.procedure_path(procedure_id))
    result = widget.validate_procedure(document)
    steps = document.get("steps")
    step_summaries = []
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                step_summaries.append({"id": step.get("id"), "title": step.get("title")})
    return {
        "valid": result.valid,
        "errors": [{"path": err.path, "message": err.message} for err in result.errors],
        "id": document.get("id"),
        "description": document.get("description"),
        "tags": document.get("tags", []),
        "steps": step_summaries,
    }


def edit_meta(
    procedure_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    updated = widget.edit_procedure(document, title=title, description=description, tags=tags)
    write_document(target, updated)
    return {
        "ok": True,
        "id": updated["id"],
        "title": updated["title"],
        "description": updated["description"],
        "tags": updated["tags"],
    }


def add_step(procedure_id: str, title: str, do: str, after: str | None = None) -> dict[str, Any]:
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    updated = widget.add_step(document, title, do, after=after)
    write_document(target, updated)
    marker = title.strip()
    step = next(item for item in updated["steps"] if item.get("title") == marker)
    titles = [item.get("title") for item in updated["steps"]]
    return {"ok": True, "id": updated["id"], "step_id": step["id"], "title": step["title"], "steps": titles}


def edit_step(
    procedure_id: str,
    title: str,
    new_title: str | None = None,
    do: str | None = None,
) -> dict[str, Any]:
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    updated = widget.edit_step(document, title, new_title=new_title, do=do)
    write_document(target, updated)
    marker = (new_title or title).strip()
    step = next(item for item in updated["steps"] if item.get("title") == marker)
    return {"ok": True, "id": updated["id"], "step_id": step["id"], "title": step["title"]}


def remove_step(procedure_id: str, title: str) -> dict[str, Any]:
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    updated = widget.remove_step(document, title)
    write_document(target, updated)
    titles = [step.get("title") for step in updated["steps"]]
    return {"ok": True, "id": updated["id"], "removed": title, "steps": titles}


def read_document(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    return widget.parse_procedure(text)


def write_document(path: str | Path, document: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = widget.dump_procedure(document)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, target)
