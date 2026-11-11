"""Use it or lose it: the store's ledger of which procedures are still earning their place.

Beside the procedure files sits `.library.json`: a clock (green gates, ticked by the
host), every procedure's last touch by kind (edit, use, search), pins, and which ones
have been retired. Retired procedures are left out of search and out of the copy a
worker takes into its sandbox, so an agent cannot find them; a person can, and can
bring one back. The policy (on/off, how many gates a touch lasts) lives in the same
file so the host and the sandbox copy read the same rules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

from playbook import store
from playbook.usage_decay_ledger import ACTIVE, RETIRED, Ledger, Policy

LEDGER_NAME = ".library.json"
KINDS = ("edit", "use", "search")
# Walking a decayed procedure brings it back: its domain has returned. Seeing it in a search does not.
REVIVING_KIND = "use"


def ledger_path() -> Path:
    return store.procedures_dir() / LEDGER_NAME


# Grace by default: a library serves many unrelated tasks, so a procedure for one
# kind of work is only touched when that kind of work comes round. Fifty green
# gates between touches (a hundred for a use, a hundred and fifty for an edit)
# is the floor; the app's Experimental page tunes it.
DEFAULT_GRACE = 50


def load() -> Ledger:
    path = ledger_path()
    if not path.is_file():
        return Ledger(policy=Policy(grace=DEFAULT_GRACE))
    try:
        return Ledger(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Ledger(policy=Policy(grace=DEFAULT_GRACE))


def save(ledger: Ledger) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(ledger.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def store_ids() -> list[str]:
    root = store.procedures_dir()
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.json") if not p.name.startswith("."))


def touch(ids: Iterable[str], kind: str) -> None:
    """Record that these procedures were edited, used or surfaced by a search. Never raises:
    the ledger is bookkeeping, and a failure to note a touch must not fail the work."""
    ids = [i for i in ids if i]
    if not ids:
        return
    try:
        ledger = load()
        for item_id in ids:
            ledger.see(item_id)
            ledger.touch(item_id, kind)
            if kind == REVIVING_KIND and item_id in ledger.retired():
                ledger.revive(item_id)   # a decayed procedure a strong search brought back, and walked
        save(ledger)
    except (OSError, ValueError):
        pass


def forget(item_id: str) -> None:
    try:
        ledger = load()
        ledger.forget(item_id)
        save(ledger)
    except (OSError, ValueError):
        pass


def retired_ids() -> set[str]:
    return set(load().retired())


def standing(item_id: str) -> dict[str, Any]:
    return _standing(load(), item_id)


def _standing(ledger: Ledger, item_id: str) -> dict[str, Any]:
    s = ledger.standing(item_id)
    return {
        "id": s.id,
        "status": s.status,
        "pinned": s.pinned,
        "protected": s.protected,
        "touched": s.touched,
        "grace_left": s.grace_left,
        "retired_at": s.retired_at,
        "retired_reason": s.retired_reason,
    }


def status(item_id: str | None = None) -> dict[str, Any]:
    ledger = load()
    ids = [item_id] if item_id else sorted(set(store_ids()) | set(ledger.items))
    return {
        "ok": True,
        "clock": ledger.clock,
        "policy": ledger.policy.to_dict(),
        "procedures": [_standing(ledger, i) for i in ids],
    }


def set_policy(enabled: bool | None = None, grace: int | None = None, protect: Iterable[str] | None = None,
               weights: dict[str, int] | None = None) -> dict[str, Any]:
    ledger = load()
    current = ledger.policy
    ledger.policy = Policy(
        enabled=current.enabled if enabled is None else enabled,
        grace=current.grace if grace is None else max(1, grace),
        weights=dict(weights) if weights else dict(current.weights),
        protected=tuple(protect) if protect is not None else current.protected,
    )
    save(ledger)
    return {"ok": True, "clock": ledger.clock, "policy": ledger.policy.to_dict()}


def pin(item_id: str, on: bool = True) -> dict[str, Any]:
    ledger = load()
    ledger.see(item_id)
    ledger.pin(item_id, on)
    save(ledger)
    return {"ok": True, **_standing(ledger, item_id)}


def retire(item_id: str, reason: str = "manual") -> dict[str, Any]:
    ledger = load()
    ledger.see(item_id)
    ledger.retire(item_id, reason)
    save(ledger)
    return {"ok": True, **_standing(ledger, item_id)}


def revive(item_id: str) -> dict[str, Any]:
    ledger = load()
    ledger.revive(item_id)
    save(ledger)
    return {"ok": True, **_standing(ledger, item_id)}


def tick(protect: Iterable[str] | None = None) -> dict[str, Any]:
    """One more green gate. New procedures in the store are seen, deleted ones forgotten,
    and whatever has run out of grace is retired."""
    ledger = load()
    if protect is not None:
        ledger.policy = Policy.from_dict({**ledger.policy.to_dict(), "protected": list(protect)})
    retired = ledger.tick(known=store_ids())
    save(ledger)
    return {"ok": True, "clock": ledger.clock, "retired": retired, "due": ledger.due(),
            "enabled": ledger.policy.enabled}


def merge(other: dict[str, Any]) -> list[str]:
    """Fold in the touches a travelling copy recorded (a sandbox's `.library.json`)."""
    ledger = load()
    touched = ledger.merge(other)
    for item_id in set(touched) & set(ledger.retired()):
        # Walked in the sandbox after it decayed here (a strong search match brought it back).
        used = ((other.get("items") or {}).get(item_id) or {}).get("touched", {}).get(REVIVING_KIND)
        retired_at = (ledger.items[item_id].get("retired") or {}).get("at")
        if used is not None and retired_at is not None and int(used) >= int(retired_at):
            ledger.revive(item_id)
    save(ledger)
    return touched


__all__ = ["ACTIVE", "RETIRED", "KINDS", "LEDGER_NAME", "ledger_path", "load", "save", "touch", "forget",
           "retired_ids", "standing", "status", "set_policy", "pin", "retire", "revive", "tick", "merge"]
