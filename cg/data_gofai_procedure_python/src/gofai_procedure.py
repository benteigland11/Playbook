"""Build and validate serial playbook procedure documents."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from json import JSONDecodeError, dumps, loads
from typing import Any, Mapping
from uuid import uuid4

DOCUMENT_KEYS = frozenset({"id", "title", "description", "tags", "steps"})
STEP_KEYS = frozenset({"id", "title", "do"})


@dataclass(frozen=True)
class Issue:
    path: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[Issue, ...] = ()


def new_procedure(
    procedure_id: str,
    title: str,
    description: str,
    tags: list[str],
) -> dict[str, Any]:
    """Return an empty valid procedure document."""
    if not isinstance(procedure_id, str) or not procedure_id.strip():
        raise ValueError("procedure id must be a non-empty string")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description must be a non-empty string")
    return {
        "id": procedure_id.strip(),
        "title": title.strip(),
        "description": description.strip(),
        "tags": _clean_tags(tags),
        "steps": [],
    }


def parse_procedure(source: str | bytes) -> dict[str, Any]:
    """Parse JSON text or bytes into a mapping. Does not validate semantics."""
    if isinstance(source, bytes):
        try:
            source = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("procedure JSON is not valid utf-8") from exc
    if not isinstance(source, str):
        raise TypeError("source must be str or bytes")
    try:
        payload = loads(source)
    except JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("procedure JSON must be an object")
    return payload


def dump_procedure(document: Mapping[str, Any]) -> str:
    """Serialize a procedure document to stable JSON text."""
    if not isinstance(document, Mapping):
        raise TypeError("document must be a mapping")
    return dumps(dict(document), indent=2, ensure_ascii=False) + "\n"


def validate_procedure(document: Mapping[str, Any]) -> ValidationResult:
    """Return every semantic issue; never mutates the document."""
    errors: list[Issue] = []
    if not isinstance(document, Mapping):
        return ValidationResult(False, (Issue("$", "expected object"),))

    extra = sorted(set(document.keys()) - DOCUMENT_KEYS)
    if extra:
        errors.append(Issue("$", f"unexpected keys {extra}"))

    procedure_id = document.get("id")
    if "id" not in document:
        errors.append(Issue("$.id", "missing required field"))
    elif not isinstance(procedure_id, str) or not procedure_id.strip():
        errors.append(Issue("$.id", "expected non-empty string"))

    title = document.get("title")
    if "title" not in document:
        errors.append(Issue("$.title", "missing required field"))
    elif not isinstance(title, str) or not title.strip():
        errors.append(Issue("$.title", "expected non-empty string"))

    description = document.get("description")
    if "description" not in document:
        errors.append(Issue("$.description", "missing required field"))
    elif not isinstance(description, str) or not description.strip():
        errors.append(Issue("$.description", "expected non-empty string"))

    if "tags" not in document:
        errors.append(Issue("$.tags", "missing required field"))
    else:
        errors.extend(_validate_tags(document.get("tags"), "$.tags"))

    steps = document.get("steps")
    if "steps" not in document:
        errors.append(Issue("$.steps", "missing required field"))
    elif not isinstance(steps, list):
        errors.append(Issue("$.steps", "expected array"))
    else:
        seen_titles: set[str] = set()
        seen_ids: set[str] = set()
        for index, step in enumerate(steps):
            errors.extend(_validate_step(step, f"$.steps[{index}]", seen_titles, seen_ids))

    return ValidationResult(valid=not errors, errors=tuple(errors))


def edit_procedure(
    document: Mapping[str, Any],
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Return a copy with title, description, and/or tags replaced. Id is unchanged."""
    if title is None and description is None and tags is None:
        raise ValueError("edit requires title, description, and/or tags")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise ValueError("title must be a non-empty string")
    if description is not None and (not isinstance(description, str) or not description.strip()):
        raise ValueError("description must be a non-empty string")
    updated = deepcopy(dict(document))
    if title is not None:
        updated["title"] = title.strip()
    if description is not None:
        updated["description"] = description.strip()
    if tags is not None:
        updated["tags"] = _clean_tags(tags)
    result = validate_procedure(updated)
    if not result.valid:
        raise ValueError(_format_errors(result))
    return updated


def add_step(
    document: Mapping[str, Any],
    title: str,
    do: str,
    step_id: str | None = None,
    after: str | None = None,
) -> dict[str, Any]:
    """Return a copy with one step inserted after `after`, or appended if `after` is omitted."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    if not isinstance(do, str) or not do.strip():
        raise ValueError("do must be a non-empty string")
    if after is not None and (not isinstance(after, str) or not after.strip()):
        raise ValueError("after must be a non-empty string")
    updated = deepcopy(dict(document))
    steps = updated.get("steps")
    if not isinstance(steps, list):
        raise ValueError("document steps must be an array")
    taken = {
        step["id"]
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("id"), str) and step["id"].strip()
    }
    assigned = _assign_step_id(step_id, taken)
    new_step = {"id": assigned, "title": title.strip(), "do": do.strip()}
    if after is None:
        steps.append(new_step)
    else:
        steps.insert(_find_step_index(steps, after.strip()) + 1, new_step)
    result = validate_procedure(updated)
    if not result.valid:
        raise ValueError(_format_errors(result))
    return updated


def edit_step(
    document: Mapping[str, Any],
    title: str,
    new_title: str | None = None,
    do: str | None = None,
) -> dict[str, Any]:
    """Return a copy with one step's title and/or do changed. Id is unchanged."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    if new_title is None and do is None:
        raise ValueError("edit requires new_title and/or do")
    if new_title is not None and (not isinstance(new_title, str) or not new_title.strip()):
        raise ValueError("new_title must be a non-empty string")
    if do is not None and (not isinstance(do, str) or not do.strip()):
        raise ValueError("do must be a non-empty string")
    updated = deepcopy(dict(document))
    steps = updated.get("steps")
    if not isinstance(steps, list):
        raise ValueError("document steps must be an array")
    marker = title.strip()
    index = _find_step_index(steps, marker)
    step = dict(steps[index])
    if new_title is not None:
        step["title"] = new_title.strip()
    if do is not None:
        step["do"] = do.strip()
    steps[index] = step
    result = validate_procedure(updated)
    if not result.valid:
        raise ValueError(_format_errors(result))
    return updated


def remove_step(document: Mapping[str, Any], title: str) -> dict[str, Any]:
    """Return a copy with one step removed. Remaining steps keep their order (the chain fuses)."""
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be a non-empty string")
    updated = deepcopy(dict(document))
    steps = updated.get("steps")
    if not isinstance(steps, list):
        raise ValueError("document steps must be an array")
    index = _find_step_index(steps, title.strip())
    del steps[index]
    result = validate_procedure(updated)
    if not result.valid:
        raise ValueError(_format_errors(result))
    return updated


def _find_step_index(steps: list[Any], title: str) -> int:
    index = next(
        (
            i
            for i, step in enumerate(steps)
            if isinstance(step, dict) and step.get("title") == title
        ),
        None,
    )
    if index is None:
        raise ValueError(f"no step titled {title!r}")
    return index


def _validate_step(step: Any, path: str, seen_titles: set[str], seen_ids: set[str]) -> list[Issue]:
    if not isinstance(step, dict):
        return [Issue(path, "expected object")]
    errors: list[Issue] = []
    extra = sorted(set(step.keys()) - STEP_KEYS)
    if extra:
        errors.append(Issue(path, f"unexpected keys {extra}"))

    step_id = step.get("id")
    if "id" not in step:
        errors.append(Issue(f"{path}.id", "missing required field"))
    elif not isinstance(step_id, str) or not step_id.strip():
        errors.append(Issue(f"{path}.id", "expected non-empty string"))
    elif step_id in seen_ids:
        errors.append(Issue(f"{path}.id", f"duplicate id {step_id!r}"))
    else:
        seen_ids.add(step_id)

    title = step.get("title")
    if "title" not in step:
        errors.append(Issue(f"{path}.title", "missing required field"))
    elif not isinstance(title, str) or not title.strip():
        errors.append(Issue(f"{path}.title", "expected non-empty string"))
    else:
        marker = title.strip()
        if marker in seen_titles:
            errors.append(Issue(f"{path}.title", f"duplicate title {marker!r}"))
        seen_titles.add(marker)

    body = step.get("do")
    if "do" not in step:
        errors.append(Issue(f"{path}.do", "missing required field"))
    elif not isinstance(body, str) or not body.strip():
        errors.append(Issue(f"{path}.do", "expected non-empty string"))
    return errors


def _validate_tags(value: Any, path: str) -> list[Issue]:
    if not isinstance(value, list):
        return [Issue(path, "expected array")]
    if not value:
        return [Issue(path, "expected non-empty array")]
    errors: list[Issue] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item.strip():
            errors.append(Issue(item_path, "expected non-empty string"))
            continue
        marker = item.strip()
        if marker in seen:
            errors.append(Issue(item_path, "duplicate tag"))
        seen.add(marker)
    return errors


def _clean_tags(tags: list[str]) -> list[str]:
    if not isinstance(tags, list) or not tags:
        raise ValueError("tags must be a non-empty array of strings")
    if any(not isinstance(item, str) or not item.strip() for item in tags):
        raise ValueError("tags must be an array of non-empty strings")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in tags:
        marker = item.strip()
        if marker in seen:
            raise ValueError(f"duplicate tag: {marker}")
        seen.add(marker)
        cleaned.append(marker)
    return cleaned


def _assign_step_id(step_id: str | None, taken: set[str]) -> str:
    if step_id is not None:
        if not isinstance(step_id, str) or not step_id.strip():
            raise ValueError("step id must be a non-empty string")
        assigned = step_id.strip()
        if assigned in taken:
            raise ValueError(f"duplicate step id: {assigned}")
        return assigned
    for _ in range(8):
        assigned = uuid4().hex
        if assigned not in taken:
            return assigned
    raise RuntimeError("could not allocate a unique step id")


def _format_errors(result: ValidationResult) -> str:
    return "; ".join(f"{item.path}: {item.message}" for item in result.errors)
