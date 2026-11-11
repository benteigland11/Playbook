"""File-backed operations over playbook procedure documents in the global store."""

from __future__ import annotations

import re

import os
from pathlib import Path
from typing import Any, Mapping

from playbook import library, store
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
WEAK_SEARCH_LIMIT = 3


def search_procedures(query: str, limit: int | None = None, include_retired: bool = False,
                      target_dir: str | Path = ".") -> dict[str, Any]:
    """Search the global store. Empty query lists procedures (summaries only).

    Local first: walks already open under the working tree and not finished come back ahead of the
    hits, as `open_here`, each with its next step — a worker whose window rolled over searches again
    from nothing, and what it had already started is the first thing it should see.
    Retired procedures (use it or lose it: see playbook.library) are left out unless asked
    for; every procedure that appears in the result is touched as searched."""
    catalog = _load_catalog()
    retired = set() if include_retired else library.retired_ids() & set(catalog)
    if retired:
        result = _search_decayed(catalog, retired, query, limit)
    else:
        result = _search(catalog, query, limit)
    # Each hit's gravity: a leaf (0) found by its words is usually walked from the method above it, which
    # `upstream <id>` names — the controller climbs from what matched to what the library leans on.
    links = parent_links() if result.get("hits") else {}
    for hit in result.get("hits", []):
        hit["gravity"] = gravity(hit["id"], links)
    library.touch([h["id"] for h in result.get("hits", [])], "search")
    here = open_walks(None, target_dir)
    if here:
        result = {"ok": result["ok"], "open_here": here,
                  "note": "these walks are already open in this workspace and not finished: continue them "
                          "before opening anything from the hits",
                  **{k: v for k, v in result.items() if k != "ok"}}
    return result


# A decayed procedure comes back for a query it answers clearly better than anything still active: its score at
# least this many times the best active hit's (or any strict match when nothing active matches). Decay molds the
# surface a worker sees for the work it does now; a domain that returns finds its procedures again, and walking
# one revives it (library.touch).
DECAYED_STRONG_RATIO = 1.5


def _search_decayed(catalog: dict[str, dict[str, Any]], retired: set[str], query: str,
                    limit: int | None) -> dict[str, Any]:
    """Search with decayed procedures hidden, except those that are a strong match (DECAYED_STRONG_RATIO).
    An empty query lists the active ones only; a weak (fallback) search never brings one back."""
    active = {k: v for k, v in catalog.items() if k not in retired}
    result = _search(active, query, limit)
    if not query.strip():
        return result
    whole = _search(catalog, query, limit)
    if whole.get("weak"):
        return result
    active_scores = [h["score"] for h in whole.get("hits", []) if h["id"] not in retired and h.get("score") is not None]
    floor = DECAYED_STRONG_RATIO*max(active_scores) if active_scores else 0.0
    strong = [h for h in whole.get("hits", []) if h["id"] in retired and (h.get("score") or 0) >= floor]
    if not strong:
        return result
    for h in strong:
        h["decayed"] = True
    if result.get("weak"):
        result = {"ok": True, "query": query, "hits": [], "limit": result.get("limit"), "total": 0}
    hits = sorted(strong+result.get("hits", []), key=lambda h: -(h.get("score") or 0))[:_clamp_limit(limit)]
    return {**result, "hits": hits, "total": result.get("total", 0)+len(strong),
            "decayed_note": "a hit marked decayed has not been used for a long while and is shown only because it "
                            "matches this query strongly; walking it brings it back"}


def _search(catalog: dict[str, dict[str, Any]], query: str, limit: int | None) -> dict[str, Any]:
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
    if ranked:
        hits = [_hit_payload(hit.item_id, catalog[hit.item_id], score=hit.score, snippet=None) for hit in ranked[:capped]]
        return {"ok": True, "query": query, "hits": hits, "limit": capped, "total": len(ranked)}
    return _weak_search(catalog, query, capped)


def _weak_search(catalog: dict[str, dict[str, Any]], query: str, capped: int) -> dict[str, Any]:
    """Strict search found nothing. Retry without the word-hit and ratio floors.

    A natural-language ask often shares no literal word with a procedure that
    still covers it. Returning nothing reads as "no such procedure" and the
    caller improvises, so offer the nearest candidates and label them weak.

    The tail is still trimmed by ratio. There is deliberately no absolute score
    floor: BM25 scores move with corpus size and field weights, so any constant
    would quietly start dropping good matches as the store grows. A query that
    matches nothing real can still surface a low-scoring procedure here, which
    is why the payload says weak and the note tells the caller to check the
    description before trusting it.
    """
    ranked = rank_search(
        catalog,
        query,
        _SEARCH_FIELDS,
        ngram=3,
        ngram_weight=0.4,
        stopwords=_STOPWORDS,
        require_word_hit=False,
        min_ratio=0.5,
    )
    shown = min(capped, WEAK_SEARCH_LIMIT)
    hits = [_hit_payload(hit.item_id, catalog[hit.item_id], score=hit.score, snippet=None) for hit in ranked[:shown]]
    payload: dict[str, Any] = {
        "ok": True,
        "query": query,
        "hits": hits,
        "limit": capped,
        "total": len(ranked),
        "weak": True,
    }
    if hits:
        payload["note"] = (
            "No strong match. These are the nearest procedures by loose similarity — "
            "read the descriptions and only use one if it genuinely covers the job."
        )
    else:
        payload["note"] = "No procedure resembles this query. The store may not cover it yet."
    return payload


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
    # The titles are what `start --title` takes; a hit that hides them costs the
    # caller a `load` (or several guesses) before it can start.
    titles = record.get("titles")
    if isinstance(titles, list) and titles:
        payload["steps"] = list(titles)
        payload["start"] = f"playbook start {item_id} --title {titles[0]!r}"
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


_GENERIC = frozenset("a an the and or of for to in on with by from as is are be this that make build write create new run use using get set".split())


def _skill_words(*texts: str) -> set[str]:
    words: set[str] = set()
    for text in texts:
        for w in re.findall(r"[a-z0-9]+", str(text or "").lower().replace("-", " ").replace("_", " ")):
            if len(w) >= 4 and w not in _GENERIC:
                words.add(w[:6])   # a rough stem: compose/composition, pedal/pedalling
    return words


def _twin_of(procedure_id: str, title: str, tags: list[str]) -> str | None:
    """A procedure in the store whose id and title share the new one's skill words: three or more stems in
    common (two when the id's own words all match). `compose-nine-bar-block-chord-midi` against
    `compose-eight-bar-piano-midi`: compose, midi, bar — a twin."""
    mine = _skill_words(procedure_id, title)
    mine_tags = {t.lower() for t in tags or []}
    if not mine:
        return None
    best: tuple[int, str] | None = None
    for path in store.procedures_dir().glob("*.json"):
        if path.name.startswith("."):
            continue
        try:
            document = read_document(path)
        except (OSError, ValueError):
            continue
        other_id = str(document.get("id") or path.stem)
        theirs = _skill_words(other_id, str(document.get("title") or ""))
        shared = len(mine & theirs)
        tag_shared = len(mine_tags & {str(t).lower() for t in (document.get("tags") or [])})
        score = shared + (1 if tag_shared >= 2 else 0)
        if score >= 3 and (best is None or score > best[0]):
            best = (score, other_id)
    return best[1] if best else None


def create_procedure(
    procedure_id: str,
    title: str,
    description: str,
    tags: list[str],
    steps: list[Mapping[str, Any]] | None = None,
) -> Path:
    """Write a new procedure. Pass steps to author the whole thing in one call.

    A twin of a procedure already in the store — same skill words in the id, title or tags — is refused with
    the way out: improve that one, or create something narrower and link it from there. Four gyms each minted
    their own "compose … piano … midi" leaf (gravity 0, one gym's piece as method) beside one another instead of
    growing one composition method that links pedal, voicing and rubato under it."""
    target = store.procedure_path(procedure_id)
    if target.exists():
        raise FileExistsError(f"already exists: {target}")
    twin = _twin_of(procedure_id, title, tags)
    if twin:
        raise ValueError(f"`{twin}` already covers this skill: improve it (`playbook add-step {twin} ...`, `edit-step`), or create "
                         f"something narrower — one decision, one instrument — and link it from `{twin}` with a step; a "
                         "second top-level method for the same skill is a sibling nobody walks")
    document = widget.new_procedure(procedure_id, title, description, tags)
    if steps:
        document = _append_steps(document, steps)
    write_document(target, document)
    library.touch([procedure_id], "edit")
    return target


def _append_steps(
    document: Mapping[str, Any],
    steps: list[Mapping[str, Any]],
    after: str | None = None,
) -> dict[str, Any]:
    """Insert steps in order, in memory. Each entry needs a title and a do."""
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty array")
    updated = dict(document)
    cursor = after
    for position, entry in enumerate(steps):
        if not isinstance(entry, Mapping):
            raise ValueError(f"steps[{position}] must be an object with title and do")
        title = entry.get("title")
        do = entry.get("do")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"steps[{position}].title must be a non-empty string")
        if not isinstance(do, str) or not do.strip():
            raise ValueError(f"steps[{position}].do must be a non-empty string")
        linked = entry.get("procedure")
        if linked is not None:
            _require_procedure(str(linked), owner=str(updated.get("id") or ""))
        updated = widget.add_step(updated, title, do, after=cursor, procedure=str(linked) if linked else None)
        cursor = title.strip()
    return updated


def _link_path(start: str, target: str) -> list[str] | None:
    """The chain of links from `start` that reaches `target` (start first, target last), or None. Links are the
    `procedure` fields of steps, followed through the store."""
    seen: set[str] = set()
    stack: list[list[str]] = [[start]]
    while stack:
        path = stack.pop()
        current = path[-1]
        if current in seen:
            continue
        seen.add(current)
        try:
            document = read_document(store.procedure_path(current))
        except (OSError, ValueError):
            continue
        for step in document.get("steps") or []:
            linked = str(step.get("procedure") or "") if isinstance(step, dict) else ""
            if not linked:
                continue
            if linked == target:
                return path + [linked]
            if linked not in seen:
                stack.append(path + [linked])
    return None


def _require_procedure(procedure_id: str, owner: str = "") -> None:
    """A linked step must point at a procedure that exists, is another one, and does not lead back here: a
    dangling link is a step nobody can walk; a self-link is a loop (drone-pack-sizing-probe linked six of its own
    steps to itself, 2026-09-19); a link whose procedure links back — through any number of others — is a walk
    that can never close (the music library grew 266 such cycles: validation linked voicing linked validation, and
    a worker holding both walks open could tick neither). "See step N" is prose in `do`, not a link."""
    if owner and procedure_id == owner:
        raise ValueError(f"a step may not link to its own procedure {procedure_id!r}; a link runs another procedure "
                         "as this step — refer to another step of this one in the step's text instead")
    if not store.procedure_path(procedure_id).is_file():
        raise ValueError(f"linked procedure {procedure_id!r} does not exist; search for its id or create it first")
    if owner:
        back = _link_path(procedure_id, owner)
        if back:
            raise ValueError(f"linking {procedure_id!r} would make a circle: " + " -> ".join([owner] + back)
                             + " — a step links a narrower procedure it runs, never one that runs this one; "
                             "say what to do in the step's text instead")


def load_procedure(procedure_id: str, full: bool = True) -> dict[str, Any]:
    """Return the whole procedure in one call. full=False gives step titles only."""
    document = read_document(store.procedure_path(procedure_id))
    _require_valid(document, procedure_id)
    library.touch([procedure_id], "use")
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


def start_procedure(procedure_id: str, title: str | None = None, step: int | None = None) -> dict[str, Any]:
    """Re-read one step by unique title: its do, its position, and the neighbouring titles.

    Without a title, start at the first step: the caller has just found the procedure
    and cannot know its step titles yet.
    """
    document = read_document(store.procedure_path(procedure_id))
    _require_valid(document, procedure_id)
    library.touch([procedure_id], "use")
    steps = document.get("steps") if isinstance(document.get("steps"), list) else []
    if step is not None:
        if not isinstance(step, int) or not 1 <= step <= len(steps) or not isinstance(steps[step-1], dict):
            raise ValueError(f"procedure {procedure_id!r} has steps 1..{len(steps)}; no step {step}")
        title = steps[step-1].get("title")
    if title is None or not str(title).strip():
        if not steps or not isinstance(steps[0], dict) or not isinstance(steps[0].get("title"), str):
            raise ValueError(f"procedure {procedure_id!r} has no steps to start at")
        title = steps[0]["title"]
    marker = str(title).strip()
    index = next(
        (i for i, step in enumerate(steps) if isinstance(step, dict) and step.get("title") == marker),
        None,
    )
    if index is None:
        titles = [step.get("title") for step in steps if isinstance(step, dict)]
        raise ValueError(f"no step titled {marker!r}; steps are: {titles!r}")
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
        # The exact command for the next step, to copy rather than recall.
        "then": (f"playbook start {procedure_id} --step {index + 2}" if isinstance(following, dict)
                 else "(last step)"),
    }


def _require_valid(document: Mapping[str, Any], procedure_id: str) -> None:
    """Raise with every schema error. Reads carry their own validation, so
    callers never need a separate validate step to find out what is wrong."""
    result = widget.validate_procedure(document)
    if result.valid:
        return
    detail = "; ".join(f"{item.path}: {item.message}" for item in result.errors)
    raise ValueError(f"procedure {procedure_id!r} is invalid: {detail}")


def validate_procedure(procedure_id: str) -> dict[str, Any]:
    document = read_document(store.procedure_path(procedure_id))
    result = widget.validate_procedure(document)
    steps = document.get("steps")
    step_summaries = []
    dangling = []
    self_links = []
    circles: list[tuple[str, str]] = []
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                step_summaries.append({"id": step.get("id"), "title": step.get("title"),
                                       **({"procedure": step["procedure"]} if step.get("procedure") else {})})
                if step.get("procedure") and str(step["procedure"]) == str(document.get("id")):
                    self_links.append(str(step.get("title")))
                elif step.get("procedure") and not store.procedure_path(str(step["procedure"])).is_file():
                    dangling.append(str(step["procedure"]))
                elif step.get("procedure"):
                    back = _link_path(str(step["procedure"]), str(document.get("id")))
                    if back:
                        circles.append((str(step.get("title")), " -> ".join([str(document.get("id"))] + back)))
    return {
        "valid": result.valid and not dangling and not self_links and not circles,
        "errors": [{"path": err.path, "message": err.message} for err in result.errors]
        + [{"path": "$.steps", "message": f"linked procedure {d!r} does not exist"} for d in dangling]
        + [{"path": "$.steps", "message": f"step {t!r} links to its own procedure; a link runs another procedure"} for t in self_links]
        + [{"path": "$.steps", "message": f"step {t!r} links a circle: {c}; a link runs a narrower procedure, never one that runs this one"} for t, c in circles],
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
    widgets: list[str] | None = None,
) -> dict[str, Any]:
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    updated = widget.edit_procedure(document, title=title, description=description, tags=tags, widgets=widgets)
    write_document(target, updated)
    library.touch([procedure_id], "edit")
    return {
        "ok": True,
        "id": updated["id"],
        "title": updated["title"],
        "description": updated["description"],
        "tags": updated["tags"],
        "widgets": updated.get("widgets", []),
    }


def walk_widgets(procedure_id: str) -> list[str]:
    """The widgets a method calls, across the procedures its walk inlines: what is installed before the walk."""
    out: list[str] = []
    for pid in [procedure_id] + sorted({e["source"] for e in expand(procedure_id)} - {procedure_id}):
        try:
            for w in read_document(store.procedure_path(pid)).get("widgets") or []:
                if w not in out:
                    out.append(str(w))
        except (OSError, ValueError):
            continue
    return out


def add_step(procedure_id: str, title: str, do: str, after: str | None = None,
             procedure: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"title": title, "do": do}
    if procedure:
        entry["procedure"] = procedure
    return add_steps(procedure_id, [entry], after=after)


def add_steps(
    procedure_id: str,
    steps: list[Mapping[str, Any]],
    after: str | None = None,
) -> dict[str, Any]:
    """Append or insert several steps in one read/write. Order is preserved."""
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    updated = _append_steps(document, steps, after=after)
    write_document(target, updated)
    library.touch([procedure_id], "edit")
    added = [str(entry["title"]).strip() for entry in steps]
    titles = [item.get("title") for item in updated["steps"]]
    return {"ok": True, "id": updated["id"], "added": added, "steps": titles}


def edit_step(
    procedure_id: str,
    title: str,
    new_title: str | None = None,
    do: str | None = None,
    procedure: str | None = None,
) -> dict[str, Any]:
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    if procedure:
        _require_procedure(procedure, owner=str(document.get("id") or procedure_id))
    updated = widget.edit_step(document, title, new_title=new_title, do=do, procedure=procedure)
    write_document(target, updated)
    library.touch([procedure_id], "edit")
    marker = (new_title or title).strip()
    step = next(item for item in updated["steps"] if item.get("title") == marker)
    # The step as it now reads, so the caller sees the edit landed: a worker that got only the title back
    # re-edited the same step thirteen times answering a correction it could not see it had met.
    return {"ok": True, "id": updated["id"], "step_id": step["id"], "title": step["title"], "do": step.get("do", ""),
            **({"procedure": step["procedure"]} if step.get("procedure") else {})}


def remove_step(procedure_id: str, title: str) -> dict[str, Any]:
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    updated = widget.remove_step(document, title)
    write_document(target, updated)
    library.touch([procedure_id], "edit")
    titles = [step.get("title") for step in updated["steps"]]
    return {"ok": True, "id": updated["id"], "removed": title, "steps": titles}


def move_step(procedure_id: str, title: str, position: int) -> dict[str, Any]:
    """Move a step (by its unique title) to a 1-based position; the others keep their order."""
    target = store.procedure_path(procedure_id)
    document = read_document(target)
    steps = document.get("steps")
    if not isinstance(steps, list):
        raise ValueError("document steps must be an array")
    wanted = title.strip()
    indices = [i for i, step in enumerate(steps) if isinstance(step, Mapping) and step.get("title") == wanted]
    if len(indices) != 1:
        raise ValueError(f"step title must match exactly one step, matched {len(indices)}: {wanted!r}")
    if not 1 <= position <= len(steps):
        raise ValueError(f"position must be 1..{len(steps)}, got {position}")
    step = steps.pop(indices[0])
    steps.insert(position - 1, step)
    result = widget.validate_procedure(document)
    if not result.valid:
        raise ValueError("; ".join(str(e) for e in result.errors))
    write_document(target, document)
    library.touch([procedure_id], "edit")
    titles = [s.get("title") for s in steps]
    return {"ok": True, "id": document["id"], "moved": wanted, "position": position, "steps": titles}


def delete_procedure(procedure_id: str) -> dict[str, Any]:
    """Remove a procedure from the store. Refused while another procedure's step still links it:
    the link would dangle, and the next worker following that step would find nothing."""
    target = store.procedure_path(procedure_id)
    if not target.exists():
        raise ValueError(f"no procedure {procedure_id!r}")
    linkers: list[str] = []
    for path in sorted(store.procedures_dir().glob("*.json")):
        if path == target:
            continue
        try:
            other = read_document(path)
        except Exception:  # noqa: BLE001 — a bad neighbour is not this one's problem
            continue
        if any(isinstance(step, Mapping) and step.get("procedure") == procedure_id for step in other.get("steps") or []):
            linkers.append(str(other.get("id") or path.stem))
    if linkers:
        raise ValueError(f"{procedure_id!r} is linked by a step of: {', '.join(linkers)} — unlink those steps first")
    target.unlink()
    library.forget(procedure_id)
    return {"ok": True, "deleted": procedure_id}


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


OPEN_DIR = ".playbook/open"


_STEP_LINE = re.compile(r"^- \[( |x|-)\] \*\*(\d+)\. (.*?)\*\*\s*$")


def walk_steps(path: Path) -> list[tuple[str, int, str]]:
    """(mark, number, title) for every step line of an open walk."""
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _STEP_LINE.match(line)
        if m:
            found.append((m.group(1), int(m.group(2)), m.group(3)))
    return found


def open_walks(procedure_id: str | None, target_dir: str | Path = ".") -> list[dict[str, Any]]:
    """Walks under the working tree that still have unticked steps, oldest first — of one procedure,
    or of every procedure when `procedure_id` is None."""
    out_dir = Path(target_dir) / OPEN_DIR
    if not out_dir.is_dir():
        return []
    result = []
    pattern = f"{procedure_id}--*.md" if procedure_id else "*--*.md"
    for path in sorted(out_dir.glob(pattern), key=lambda p: p.stat().st_mtime):
        steps = walk_steps(path)
        left = [(n, t) for mark, n, t in steps if mark == " "]
        if left:
            result.append(dict(id=path.name.split("--", 1)[0], path=str(path), steps=len(steps), unticked=len(left),
                               next=f"{left[0][0]}. {left[0][1]}"))
    return result


def _walk_file(target: str, target_dir: str | Path) -> Path:
    path = Path(target)
    if path.is_file():
        return path
    walks = open_walks(target, target_dir)
    if not walks:
        raise ValueError(f"no open walk of '{target}' with unticked steps under {Path(target_dir) / OPEN_DIR}")
    if len(walks) > 1:
        raise ValueError(f"'{target}' has {len(walks)} unfinished walks; name the file: " + ", ".join(w["path"] for w in walks))
    return Path(walks[0]["path"])


_LINK_LINE = re.compile(r"^\s*→ this step is another procedure: `playbook open ([A-Za-z0-9._-]+) --for")


def _linked_steps(lines: list[str]) -> dict[int, str]:
    """step number -> the procedure id that step links, from a walk file's own text."""
    out: dict[int, str] = {}
    current = None
    for line in lines:
        m = _STEP_LINE.match(line)
        if m:
            current = int(m.group(2)); continue
        k = _LINK_LINE.match(line)
        if k and current is not None:
            out[current] = k.group(1)
    return out


def _walk_exists(procedure_id: str, target_dir: str | Path) -> bool:
    out_dir = Path(target_dir) / OPEN_DIR
    return out_dir.is_dir() and any(out_dir.glob(f"{procedure_id}--*.md"))


def _walk_closed(procedure_id: str, target_dir: str | Path) -> bool:
    """A walk of the procedure exists under the working tree with no unticked box."""
    out_dir = Path(target_dir) / OPEN_DIR
    if not out_dir.is_dir():
        return False
    for path in out_dir.glob(f"{procedure_id}--*.md"):
        steps = walk_steps(path)
        if steps and all(mark != " " for mark, _, _ in steps):
            return True
    return False


_PLAN_RANGE = re.compile(r"\bsteps?\s+(\d+)(?:\s*[-–]\s*(\d+))?((?:\s*,\s*(?:and\s+)?\d+(?:\s*[-–]\s*\d+)?)*)", re.I)


def _plan_path(walk: Path) -> Path:
    """`x.plan.md` beside the walk `x.md`; `x.md.plan.md` is taken too (a worker read "<this file's name>.plan.md"
    as append and wrote a good plan under the wrong name)."""
    canonical = walk.with_name(walk.name[:-3] + ".plan.md") if walk.name.endswith(".md") else walk.with_name(walk.name + ".plan.md")
    appended = walk.with_name(walk.name + ".plan.md")
    return appended if (appended.is_file() and not canonical.is_file()) else canonical


def _plan_coverage(plan: Path) -> set[int]:
    """Every step number the plan places: 'step 7', 'steps 12-15', 'steps 3, 5 and 9-11'."""
    out: set[int] = set()
    try:
        text = plan.read_text(encoding="utf-8")
    except OSError:
        return out
    for m in _PLAN_RANGE.finditer(text):
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        out.update(range(min(a, b), max(a, b) + 1))
        for part in re.findall(r"\d+(?:\s*[-–]\s*\d+)?", m.group(3) or ""):
            lo, _, hi = part.partition("-") if "-" in part else part.partition("–")
            lo_i, hi_i = int(lo), int(hi or lo)
            out.update(range(min(lo_i, hi_i), max(lo_i, hi_i) + 1))
    return out


def tick_walk(target: str, done: list[int] | None = None, not_needed: list[int] | None = None,
              target_dir: str | Path = ".", because: str = "", note: str = "") -> dict[str, Any]:
    """Mark steps of an open walk in one call: `done` get `[x]`, `not_needed` get `[-]`. A worker ticking by
    hand spent thirteen turns of grep, read and edit on seven boxes.

    The walk is read before it closes: its plan (<walk>.plan.md) places every step under an action or under the
    steps that do not apply, with why. With the plan written, any number of steps close in one call — several
    skipped under one reason, a stretch done under one note: that is the model's judgement, made after the read
    (2026-09-22; one at a time trapped a worker that had finished the work behind 53 unticked boxes of a general
    procedure). Without a plan, a call closes a few steps and skips one."""
    done = list(done or []); not_needed = list(not_needed or [])
    if not done and not not_needed:
        raise ValueError("tick needs --done and/or --skip with step numbers, e.g. --done 1,3 --skip 2 --because ...")
    if set(done) & set(not_needed):
        raise ValueError("a step is done or not needed, not both: " + ", ".join(str(n) for n in sorted(set(done) & set(not_needed))))
    because = str(because or "").strip()
    note = str(note or "").strip()
    if not_needed and not because:
        raise ValueError("--skip needs --because: why this step does not apply to what is in front of you (one line; it goes under the step)")
    if not_needed and _NOT_A_REASON.search(because):
        raise ValueError("that is not a reason to skip, it is the step you did not do: a skip says what about this artifact makes "
                         "the step not apply (\"solo piano, no SATB\"; \"no pedal part in this piece\"). A step that is already done is "
                         "ticked --done with what it found; a step the probe covers is done by reading the probe's value into the note")
    if done and not note:
        raise ValueError("--done needs --note: what the step found or changed, one line with the value or the file (it goes under "
                         "the step, and the reviewer reads it against the artifact) — a box without what it found is a box")
    # A flat walk is ticked against a plan: before any box closes, the worker has read every step and written
    # <walk>.plan.md — the actions this artifact needs, each naming the steps it covers and what it will do here,
    # and the steps that do not apply with why. The idea "I can do all of this in one go" gets written out and
    # read (by the reviewer, against the artifact) instead of happening silently in the first ten turns. The plan
    # is also the read that lets a call close many steps at once, on any walk.
    path0 = _walk_file(target, target_dir)
    blocks0 = _walk_blocks(path0)
    bulk = len(not_needed) > 1 or len(done) > TICK_AT_ONCE
    if bulk or (blocks0 and all(b["source"] for b in blocks0)):
        plan = _plan_path(path0)
        asked = sorted(set(done) | set(not_needed))
        if not plan.is_file():
            raise ValueError(f"no plan yet: read every step of the walk, then write {plan.name} beside it — a numbered list of the actions "
                             "this artifact needs, each line naming the step numbers it covers (\"steps 12-15: voice each harmony ...\") "
                             "and what it will do here, and a line for the steps that do not apply with why. Then do the actions and tick "
                             "their steps with what they found"
                             + (f"; with the plan, a call may close any number of steps (without it, at most {TICK_AT_ONCE} done "
                                "and one skipped)." if bulk else ". Nothing ticks before the plan."))
        covered = _plan_coverage(plan)
        missing = [n for n in asked if n not in covered]
        if missing:
            raise ValueError(f"step(s) {missing} are not in {plan.name}: every step the walk has is placed in the plan — under an action "
                             "(\"steps 12-15: ...\") or under the steps that do not apply — before it is ticked")
    path = _walk_file(target, target_dir)
    lines = path.read_text(encoding="utf-8").splitlines()
    # A step that is another procedure closes only once that procedure's walk is closed: opened, and every
    # box ticked or skipped with a reason. Ticking the parent box on the strength of "the widget did it" left
    # three linked procedures unwalked and unrecorded in one task (attempt 4, 2026-09-21); the worker has to try.
    linked = _linked_steps(lines)
    unwalked = [n for n in sorted(done) if n in linked and not _walk_closed(linked[n], target_dir)]
    if unwalked:
        raise ValueError("step(s) " + ", ".join(str(n) for n in unwalked) + " are other procedures; open and finish each first "
                         "(every box [x], or [-] with its reason), then tick this box: "
                         + "; ".join(f"{n} -> `playbook open {linked[n]} --for ...`" for n in unwalked))
    # Skipping a linked step needs the walk opened, not closed: the library's links run in circles (a validation
    # procedure links the voicing procedure that links it), and a closed-walk requirement on both sides deadlocked
    # a worker with "stale circular linked-step metadata". Opened means looked at; the reason says the rest.
    unopened = [n for n in not_needed if n in linked and not _walk_exists(linked[n], target_dir)]
    if unopened:
        raise ValueError("step " + str(unopened[0]) + " is another procedure; open it before deciding it does not apply: "
                         f"`playbook open {linked[unopened[0]]} --for ...`")
    seen: set[int] = set(); marked = []
    for i, line in enumerate(lines):
        m = _STEP_LINE.match(line)
        if not m:
            continue
        n = int(m.group(2)); seen.add(n)
        mark = "x" if n in done else "-" if n in not_needed else None
        if mark is not None:
            lines[i] = re.sub(r"^- \[( |x|-)\]", f"- [{mark}]", line, count=1)
            if mark == "-":
                lines[i] += f"\n\n  not needed here: {because}"
            elif mark == "x":
                lines[i] += f"\n\n  done: {note}"
            marked.append(n)
    missing = sorted((set(done) | set(not_needed)) - seen)
    if missing:
        raise ValueError(f"no such step(s) {missing}; the walk has steps {sorted(seen)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    nxt = _render_walk(path)
    left = [n for mark, n, _ in walk_steps(path) if mark == " "]
    result = {"ok": True, "path": str(path), "marked": marked, "unticked": left}
    if nxt:
        result["next"] = nxt   # the step now open: its text, so the walk goes on without a read
    return result


# Skip reasons that say the step was done elsewhere: a worker skipped fifty steps of a walk with these, one per
# turn, on a piece whose every pedal hold crossed a harmony — the "covered" step was the check it never made.
_NOT_A_REASON = re.compile(r"\b(already|covered|superseded|redundant|measured (separately|by|earlier|during)|recorded (for|by|separately)|"
                           r"performed (separately|earlier)|handled (by|elsewhere)|probe (already|covers|measured)|done (earlier|already|separately)|"
                           r"inspected (earlier|during|separately)|validated (earlier|separately|during))\b", re.I)
TICK_AT_ONCE = 6   # steps one tick call may close before the walk's plan is written; after it, any number
REVEAL = 10**6     # every step's text is open: the worker reads the whole walk and plans from it (see the plan rule in tick)
FLAT_LIMIT = 50   # steps in one walk: past this the method is several work orders, and the route carries the rest
# The loop's own procedures: a step that links one runs the framework, and its steps are not part of a domain
# method (a voicing walk with the generic resolve-an-unknown checklist inlined in the middle of it is not voicing).
FRAMEWORK = frozenset({"mizpah-resolve-unknown", "mizpah-build-environment", "cartograph-widget", "author-procedure"})
_SOURCE_LINE = re.compile(r"^\s*source: `([A-Za-z0-9._-]+)` · step (\d+)\s*$")


def expand(procedure_id: str, _ancestors: tuple[str, ...] = (), _seen: set[str] | None = None) -> list[dict[str, Any]]:
    """The procedure as one straight list of steps: a step that links another procedure is followed, in place,
    by that procedure's steps (depth first), each entry remembering its source procedure and step number. Each
    procedure is inlined once per walk — a later link to it says so instead (the music library reaches
    midi-artifact-validation by four paths; inlining it four times made an eight-step procedure 258 steps long).
    A link back into the chain being expanded is noted, not followed (the library from before the circle rule)."""
    document = read_document(store.procedure_path(procedure_id))
    out: list[dict[str, Any]] = []
    chain = _ancestors + (procedure_id,)
    seen = _seen if _seen is not None else {procedure_id}
    for index, step in enumerate([st for st in (document.get("steps") or []) if isinstance(st, dict)], 1):
        linked = str(step.get("procedure") or "")
        out.append(dict(source=procedure_id, step=index, title=str(step.get("title") or ""),
                        do=str(step.get("do") or "").strip(), depth=len(_ancestors), linked=linked or None,
                        circular=bool(linked and linked in chain), inlined_above=bool(linked and linked in seen and linked not in chain)))
        if linked in FRAMEWORK:
            out[-1]["framework"] = True
        elif linked and linked not in seen and store.procedure_path(linked).is_file():
            seen.add(linked)
            out.extend(expand(linked, chain, seen))
    return out


def gravity(procedure_id: str, links: dict[str, set[str]] | None = None) -> int:
    """How much of the library sinks into this procedure: the procedures that link to it, transitively. A
    validation or pedal method that six others run has gravity six; a tune one gym wrote for itself has none.
    The controller reads it beside reach: a gravity-0 leaf picked by title for a general task is one gym's own."""
    links = parent_links() if links is None else links
    seen: set[str] = set()
    frontier = [procedure_id]
    while frontier:
        current = frontier.pop()
        for parent in links.get(current, ()):
            if parent not in seen and parent != procedure_id:
                seen.add(parent)
                frontier.append(parent)
    return len(seen)


def parent_links() -> dict[str, set[str]]:
    """Each procedure id to the procedures whose steps link to it."""
    links: dict[str, set[str]] = {}
    for path in store.procedures_dir().glob("*.json"):
        if path.name.startswith("."):
            continue
        try:
            document = read_document(path)
        except (OSError, ValueError):
            continue
        for step in document.get("steps") or []:
            if isinstance(step, dict) and step.get("procedure"):
                links.setdefault(str(step["procedure"]), set()).add(str(document.get("id") or path.stem))
    return links


def upstream(procedure_id: str) -> dict[str, Any]:
    """The chains above a procedure: every procedure that links to it, and theirs, up to the roots nobody links
    — how far up the library's waterfall this one sits. A leaf with no chain is an orphan: nothing general
    leads to it, so no worker following the general method will find it. `chains` are root-first paths ending
    at this procedure; `roots` the tops; `orphan` when there is no chain at all."""
    links: dict[str, set[str]] = {}
    titles: dict[str, str] = {}
    for path in store.procedures_dir().glob("*.json"):
        if path.name.startswith("."):
            continue
        try:
            document = read_document(path)
        except (OSError, ValueError):
            continue
        pid = str(document.get("id") or path.stem)
        titles[pid] = str(document.get("title") or "")
        for step in document.get("steps") or []:
            if isinstance(step, dict) and step.get("procedure"):
                links.setdefault(str(step["procedure"]), set()).add(pid)
    chains: list[list[str]] = []

    def climb(node: str, path: list[str]) -> None:
        parents = [p for p in sorted(links.get(node, ())) if p not in path]
        if not parents:
            if len(path) > 1:
                chains.append(list(reversed(path)))
            return
        for parent in parents:
            climb(parent, path+[parent])

    climb(procedure_id, [procedure_id])
    roots = sorted({c[0] for c in chains})
    return dict(id=procedure_id, title=titles.get(procedure_id, ""), orphan=not chains, roots=roots,
                chains=[[dict(id=n, title=titles.get(n, "")) for n in c] for c in chains][:12], depth=max((len(c)-1 for c in chains), default=0))


def reach(procedure_id: str) -> dict[str, Any]:
    """How long the method really is: steps through links, the procedures it runs, and how many walks that is."""
    entries = expand(procedure_id)
    by_source: dict[str, int] = {}
    for e in entries:
        by_source[e["source"]] = by_source.get(e["source"], 0) + 1
    walks = 0
    start = 0
    while start < len(entries):
        start = _cut(entries, start, FLAT_LIMIT)
        walks += 1
    return {"ok": True, "id": procedure_id, "steps": len(entries), "procedures": by_source,
            "walks": max(1, walks), "limit": FLAT_LIMIT, "widgets": walk_widgets(procedure_id), "gravity": gravity(procedure_id)}


def _cut(entries: list[dict[str, Any]], start: int, limit: int) -> int:
    """Where a walk from `start` ends: at the limit, or a little past it (a third at most) where the method comes
    back up to its own steps, so an inlined procedure is not split across walks."""
    limit = max(1, int(limit))
    end = min(len(entries), start + limit)
    if end < len(entries) and entries[end]["depth"] > 0:
        for k in range(end, min(len(entries), start + limit + limit // 3) + 1):
            if k == len(entries) or entries[k]["depth"] == 0:
                end = k
                break
    if 0 < len(entries) - end <= limit // 5:   # a tail of a few steps is this walk's, not a walk of its own
        end = len(entries)
    return end


_GUIDANCE = ("This procedure is the validation of your work: each step is a check or a change someone found necessary, "
             "written down so the next piece gets it too. Read every step first. Then, before anything else, write the plan "
             "at the path named as `plan:` at the top of this file: a numbered list of the "
             "actions this artifact needs, each line naming the steps it covers and what it will do here (\"steps 12-15: voice "
             "each harmony as LH root+fifth under a RH melody in sustained thirds\"), and a line for the steps that do not apply "
             "with why (\"steps 9-11: that gym's 12/8 probe; this piece is 4/4\"). Every step number lands somewhere; nothing "
             "ticks before the plan exists. Then do the actions, in the order the plan says, and tick their steps in the command "
             "that does them: `playbook tick <this file> --done N,M --note \"...\"` with what the action found or changed (the value, "
             "the file). Steps that do not apply are `--skip N,M --because \"...\"` — one reason may cover the steps it is true of, "
             "and \"already done\" and \"covered by the probe\" are not reasons (those are --done with what was found). With the "
             "plan written you may close as many steps in one call as your judgement says belong together. The steps are knowledge earned on another "
             "task: adapt their specifics (names, keys, counts, the probe they mention) to what is in front of you. Improve a step "
             "that fell short where you stand: `playbook edit-step --walk <this file> --step N --do ...` (`add-step --walk ... --after N`, "
             "`remove-step --walk ... --step N`) changes the procedure the step came from.")


def _step_block(number: int, entry: Mapping[str, Any]) -> list[str]:
    lines = [f"- [ ] **{number}. {entry['title']}**", "", f"  source: `{entry['source']}` · step {entry['step']}", "", f"  {entry['do']}", ""]
    if entry.get("framework"):
        lines += [f"  (this step runs `{entry['linked']}`, the loop's own procedure — you know it; `playbook open {entry['linked']} --nested` if you need its steps)", ""]
    elif entry.get("circular"):
        lines += [f"  (links `{entry['linked']}`, which is already in this chain — not inlined)", ""]
    elif entry.get("inlined_above"):
        lines += [f"  (links `{entry['linked']}`, inlined once above — do this step with those steps in mind)", ""]
    return lines


def open_procedure(procedure_id: str, purpose: str, target_dir: str | Path = ".", again: bool = False,
                   nested: bool = False, limit: int = FLAT_LIMIT, start: int = 0) -> dict[str, Any]:
    """Materialise a procedure as a checklist file in the working tree, to be read once and followed.

    Flat by default: the procedure and everything its steps link, as one straight list of at most `limit`
    steps, each step naming the procedure it came from; what falls past the limit is the next walk (`start`)
    and the route's to carry. `nested` keeps the older shape — the procedure alone, linked steps as links to
    open. Every open is its own copy, named and headed by what it is for; a walk still in progress is handed
    back with its next step unless `again`.
    """
    purpose = str(purpose or "").strip()
    if not purpose:
        raise ValueError("open needs --for: what this walk of the procedure is for (an unknown, an artifact, a source)")
    if not again and not start:   # a continuation (--from) is the next walk by definition
        unfinished = open_walks(procedure_id, target_dir)
        if unfinished:
            walk = unfinished[-1]
            return {"ok": True, "id": procedure_id, "already_open": True, "path": walk["path"], "next": walk["next"],
                    "unticked": walk["unticked"],
                    "note": ("this procedure is already open here; continue from its next step, tick `[x]` done or `[-]` "
                             "not needed (with --because). `--again` opens a "
                             "second walk for a second thing.")}
    document = read_document(store.procedure_path(procedure_id))
    _require_valid(document, procedure_id)
    out_dir = Path(target_dir) / OPEN_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", purpose.lower()).strip("-")[:48] or "walk"
    path = out_dir / f"{procedure_id}--{slug}.md"
    n = 2
    while path.exists():
        path = out_dir / f"{procedure_id}--{slug}-{n}.md"
        n += 1
    plan_path = path.with_name(path.name[:-3] + ".plan.md")
    lines = [f"# {document.get('title') or procedure_id}", "", f"procedure: `{procedure_id}`", f"for: {purpose}",
             f"plan: `{plan_path}` — write it before the first tick (see below)", ""]
    if document.get("description"):
        lines += [str(document["description"]).strip(), ""]
    if document.get("tags"):
        lines += ["tags: " + ", ".join(str(t) for t in document["tags"]), ""]
    remaining: list[dict[str, Any]] = []
    if nested:
        library.touch([procedure_id], "use")
        steps = [st for st in (document.get("steps") or []) if isinstance(st, dict)]
        lines += [_GUIDANCE + " A step that is another procedure is walked as well — open it, finish it, then tick here; "
                  "`tick` refuses a linked step whose walk is not closed.", ""]
        for i, step in enumerate(steps, 1):
            lines += [f"- [ ] **{i}. {step.get('title', '')}**", "", f"  {str(step.get('do', '')).strip()}", ""]
            if step.get("procedure"):
                linked = str(step["procedure"])
                lines += [f"  → this step is another procedure: `playbook open {linked} --for \"{purpose}: {step.get('title', '')}\"` "
                          f"— walk that checklist, then tick this box.", ""]
        count = len(steps)
    else:
        entries = expand(procedure_id)
        if start < 0 or start >= len(entries):
            raise ValueError(f"--from {start}: the flattened method has {len(entries)} steps (0-based start)")
        # The cut falls where the method comes back up to its own steps (depth 0), so an inlined procedure is
        # not split across walks; the walk runs over the limit by at most a third for that.
        end = _cut(entries, start, limit)
        chosen = entries[start:end]
        remaining = entries[end:]
        library.touch(sorted({e["source"] for e in chosen}), "use")
        used = walk_widgets(procedure_id)
        if used:
            lines += ["widgets: " + ", ".join(f"`{w}`" for w in used) + " — the instruments this method calls, installed under cg/ "
                      "before the walk; a step that says to install one is done when `cartograph validate cg/<dir>` passes.", ""]
        lines += [_GUIDANCE, "", f"flat: steps {start + 1}–{start + len(chosen)} of {len(entries)} through "
                  f"{len({e['source'] for e in entries})} procedure(s); a step's source is named under it.", ""]
        for i, e in enumerate(chosen, 1):
            lines += _step_block(i, e)
        if remaining:
            by_source: dict[str, int] = {}
            for e in remaining:
                by_source[e["source"]] = by_source.get(e["source"], 0) + 1
            lines += [f"continues: {len(remaining)} more step(s) — " + "; ".join(f"{k} ({v})" for k, v in by_source.items())
                      + f" — next walk: `playbook open {procedure_id} --from {start + len(chosen)} --for \"...\"`; "
                      "finish this walk's steps in this work order; the route carries the rest as the next work order", ""]
        count = len(chosen)
    path.write_text("\n".join(lines))
    nxt = _render_walk(path) if not nested else None
    result = {"ok": True, "id": procedure_id, "title": document.get("title"), "for": purpose, "steps": count, "path": str(path)}
    if nxt:
        result["next"] = nxt
    if remaining:
        result["continues"] = len(remaining)
        result["next_walk"] = f"playbook open {procedure_id} --from {start + count} --for ..."
    return result


def _walk_blocks(path: Path) -> list[dict[str, Any]]:
    """The step blocks of a flat walk: (number, mark, title, source, step, first line index, last line index)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        m = _STEP_LINE.match(line)
        if m:
            if blocks:
                blocks[-1]["end"] = i
            blocks.append(dict(number=int(m.group(2)), mark=m.group(1), title=m.group(3), start=i, end=len(lines), source=None, step=None))
            continue
        k = _SOURCE_LINE.match(line)
        if k and blocks and blocks[-1]["source"] is None:
            blocks[-1]["source"], blocks[-1]["step"] = k.group(1), int(k.group(2))
    for b in blocks:   # the footer (continues:) is not part of the last block
        text = lines[b["start"]:b["end"]]
        for j, ln in enumerate(text):
            if ln.startswith("continues:"):
                b["end"] = b["start"] + j
                break
    return blocks


def _walk_step(walk: str, number: int) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    path = Path(walk)
    if not path.is_file():
        raise ValueError(f"no such walk file: {walk}")
    blocks = _walk_blocks(path)
    block = next((b for b in blocks if b["number"] == number), None)
    if block is None:
        raise ValueError(f"the walk has no step {number}; it has {[b['number'] for b in blocks]}")
    if not block["source"]:
        raise ValueError(f"step {number} names no source procedure (a nested walk?); edit the procedure by id instead")
    return path, block, blocks


def _source_title(procedure_id: str, step: int) -> str:
    document = read_document(store.procedure_path(procedure_id))
    steps = [st for st in (document.get("steps") or []) if isinstance(st, dict)]
    if not 1 <= step <= len(steps):
        raise ValueError(f"{procedure_id} has {len(steps)} steps; the walk names step {step} — the procedure changed under the walk")
    return str(steps[step - 1].get("title") or "")


def _renumber(lines: list[str]) -> list[str]:
    n = 0
    out = []
    for line in lines:
        m = _STEP_LINE.match(line)
        if m:
            n += 1
            line = re.sub(r"^- \[( |x|-)\] \*\*\d+\.", lambda mm: f"- [{mm.group(1)}] **{n}.", line, count=1)
        out.append(line)
    return out


def _source_entry(procedure_id: str, step: int) -> dict[str, Any]:
    document = read_document(store.procedure_path(procedure_id))
    steps = [st for st in (document.get("steps") or []) if isinstance(st, dict)]
    if not 1 <= step <= len(steps):
        raise ValueError(f"{procedure_id} has {len(steps)} steps; the walk names step {step} — the procedure changed under the walk")
    st = steps[step - 1]
    linked = str(st.get("procedure") or "")
    return dict(source=procedure_id, step=step, title=str(st.get("title") or ""), do=str(st.get("do") or "").strip(),
                linked=linked or None, framework=linked in FRAMEWORK, inlined=bool(linked and linked not in FRAMEWORK))


def _render_walk(path: Path) -> dict[str, Any] | None:
    """Re-render a flat walk from its source procedures: every step's title and source, the text of the step
    the worker is on and the REVEAL-1 after it, and a closed line for the rest. A worker shown fifty steps at
    once read them as a spec and wrote one script for all of them; a step it cannot see yet, it cannot fold in.
    Returns the next open step (number, title, do), or None when every box is closed."""
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks = _walk_blocks(path)
    if not blocks or any(b["source"] is None for b in blocks):
        return None   # a nested walk keeps its shape
    first_open = next((i for i, b in enumerate(blocks) if b["mark"] == " "), len(blocks))
    body: list[str] = []
    nxt = None
    for i, b in enumerate(blocks):
        entry = _source_entry(b["source"], b["step"])
        head = f"- [{b['mark']}] **{b['number']}. {entry['title']}**"
        if i < first_open + REVEAL:
            block = _step_block(b["number"], entry)
            block[0] = head
            # a skipped step keeps the reason written under it
            reason = next((ln for ln in lines[b["start"]:b["end"]] if ln.strip().startswith(("not needed here:", "done:"))), None)
            if reason:
                block.insert(len(block) - 1, reason)
                block.insert(len(block) - 1, "")
            if entry.get("inlined"):
                block.insert(len(block) - 1, f"  (links `{entry['linked']}`: its steps are inlined where it first appears in this walk)")
                block.insert(len(block) - 1, "")
            if i == first_open:
                nxt = dict(number=b["number"], title=entry["title"], do=entry["do"])
        elif b["mark"] != " ":
            kept = next((ln for ln in lines[b["start"]:b["end"]] if ln.strip().startswith(("not needed here:", "done:"))), None)
            block = [head, "", f"  source: `{entry['source']}` · step {entry['step']}", ""] + ([kept, ""] if kept else [])
        else:
            block = [head, "", f"  source: `{entry['source']}` · step {entry['step']}", "", "  (opens when the steps before it are ticked)", ""]
        body += block
    footer = lines[blocks[-1]["end"]:]
    head_lines = lines[:blocks[0]["start"]]
    path.write_text("\n".join(head_lines + body + footer).rstrip("\n") + "\n", encoding="utf-8")
    return nxt


def walk_edit_step(walk: str, number: int, new_title: str | None = None, do: str | None = None,
                   procedure: str | None = None) -> dict[str, Any]:
    """Edit the step where the worker stands: the change writes through to the procedure the step came from,
    and the walk's block re-renders. The walk is a view; the procedure is the truth."""
    path, block, _ = _walk_step(walk, number)
    result = edit_step(block["source"], _source_title(block["source"], block["step"]), new_title=new_title, do=do, procedure=procedure)
    lines = path.read_text(encoding="utf-8").splitlines()
    entry = dict(source=block["source"], step=block["step"], title=result["title"], do=result.get("do", ""))
    fresh = _step_block(number, entry)
    fresh[0] = re.sub(r"^- \[ \]", f"- [{block['mark']}]", fresh[0], count=1)
    lines[block["start"]:block["end"]] = fresh
    path.write_text("\n".join(lines) + ("\n" if not "\n".join(lines).endswith("\n") else ""), encoding="utf-8")
    _render_walk(path)
    return {**result, "walk": str(path), "walk_step": number}


def walk_add_step(walk: str, after: int, title: str, do: str, procedure: str | None = None) -> dict[str, Any]:
    """Add a step after the one the worker stands on: it goes into that step's procedure right after it, and
    appears in the walk as the next number (later steps renumber)."""
    path, block, _ = _walk_step(walk, after)
    result = add_step(block["source"], title, do, after=_source_title(block["source"], block["step"]), procedure=procedure)
    lines = path.read_text(encoding="utf-8").splitlines()
    # Later steps of the same procedure moved down one in the source; then the new block goes in after this one.
    for i, line in enumerate(lines):
        k = _SOURCE_LINE.match(line)
        if k and k.group(1) == block["source"] and int(k.group(2)) > block["step"]:
            lines[i] = f"  source: `{block['source']}` · step {int(k.group(2)) + 1}"
    entry = dict(source=block["source"], step=block["step"] + 1, title=title, do=do)
    lines[block["end"]:block["end"]] = _step_block(after + 1, entry)
    lines = _renumber(lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _render_walk(path)
    return {**result, "walk": str(path), "walk_step": after + 1}


def walk_remove_step(walk: str, number: int) -> dict[str, Any]:
    """Remove the step where the worker stands from the procedure it came from; the walk renumbers."""
    path, block, _ = _walk_step(walk, number)
    result = remove_step(block["source"], _source_title(block["source"], block["step"]))
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[block["start"]:block["end"]]
    for i, line in enumerate(lines):
        k = _SOURCE_LINE.match(line)
        if k and k.group(1) == block["source"] and int(k.group(2)) > block["step"]:
            lines[i] = f"  source: `{block['source']}` · step {int(k.group(2)) - 1}"
    lines = _renumber(lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _render_walk(path)
    return {**result, "walk": str(path), "removed_walk_step": number}
