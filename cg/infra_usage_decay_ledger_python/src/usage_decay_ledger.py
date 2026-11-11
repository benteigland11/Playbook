"""Use it or lose it: a ledger of items that retire when nothing touches them.

The clock is whatever the caller counts (releases, green builds, sessions), not
wall time. Every touch is stamped with the clock reading; each kind of touch buys
a different grace, `weight[kind] * grace` ticks. An item retires on a tick once
every touch it ever had has run out. Pinned items and protected ids never retire;
a retired item can be revived and starts with full grace again.

Pure logic over a plain dict, so the caller owns persistence and the file format
stays readable. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

ACTIVE = "active"
RETIRED = "retired"


@dataclass(frozen=True)
class Policy:
    """How long a touch lasts and who is exempt.

    `weights` maps a touch kind to how many `grace` units it buys; the strongest
    kind is the one whose weight is largest. `protected` are id prefixes that
    never retire (a library's own bootstrap entries, say). With `enabled` false
    the ledger still records touches and ticks the clock but retires nothing.
    """

    enabled: bool = False
    grace: int = 10
    weights: Mapping[str, int] = field(default_factory=lambda: {"edit": 3, "use": 2, "search": 1})
    protected: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "grace": self.grace,
            "weights": dict(self.weights),
            "protected": list(self.protected),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None, default: "Policy | None" = None) -> "Policy":
        base = default or cls()
        if not data:
            return base
        weights = data.get("weights")
        return cls(
            enabled=bool(data.get("enabled", base.enabled)),
            grace=max(1, int(data.get("grace", base.grace))),
            weights=dict(weights) if isinstance(weights, Mapping) and weights else dict(base.weights),
            protected=tuple(data.get("protected") or base.protected),
        )

    @property
    def strongest(self) -> str:
        return max(self.weights, key=lambda k: self.weights[k])


@dataclass
class Standing:
    """One item as the ledger sees it now."""

    id: str
    status: str
    pinned: bool
    protected: bool
    touched: dict[str, int]
    grace_left: int | None
    retired_at: int | None
    retired_reason: str | None

    @property
    def safe(self) -> bool:
        """Cannot retire by decay: pinned, protected, or the policy is off."""
        return self.grace_left is None


class Ledger:
    """The clock, the policy, and every item's touches and standing."""

    def __init__(self, data: Mapping[str, Any] | None = None, policy: Policy | None = None) -> None:
        data = dict(data or {})
        self.clock: int = int(data.get("clock", 0))
        self.policy: Policy = policy or Policy.from_dict(data.get("policy"))
        self.items: dict[str, dict[str, Any]] = {
            str(k): dict(v) for k, v in (data.get("items") or {}).items() if isinstance(v, Mapping)
        }

    def to_dict(self) -> dict[str, Any]:
        return {"clock": self.clock, "policy": self.policy.to_dict(), "items": self.items}

    # -- recording -------------------------------------------------------

    def _item(self, item_id: str) -> dict[str, Any]:
        entry = self.items.setdefault(item_id, {})
        entry.setdefault("touched", {})
        entry.setdefault("pinned", False)
        entry.setdefault("status", ACTIVE)
        return entry

    def see(self, item_id: str) -> bool:
        """Register an item the first time it is met: it starts with full grace,
        as if touched by the strongest kind now. Returns True when it was new."""
        if item_id in self.items:
            return False
        self._item(item_id)["touched"][self.policy.strongest] = self.clock
        return True

    def touch(self, item_id: str, kind: str, at: int | None = None) -> None:
        """Stamp a touch of `kind` (must be one of the policy's kinds) at the
        clock reading `at`, or now. A later stamp never moves backwards."""
        if kind not in self.policy.weights:
            raise ValueError(f"unknown touch kind {kind!r}; policy knows {sorted(self.policy.weights)}")
        entry = self._item(item_id)
        when = self.clock if at is None else int(at)
        entry["touched"][kind] = max(int(entry["touched"].get(kind, -1)), when)

    def merge(self, other: Mapping[str, Any]) -> list[str]:
        """Fold in touches recorded elsewhere on the same clock (a copy of the
        ledger that travelled with the items). Pins and statuses are not
        merged: those are decisions made here. Returns the ids touched."""
        touched: list[str] = []
        for item_id, entry in (other.get("items") or {}).items():
            if not isinstance(entry, Mapping):
                continue
            for kind, at in (entry.get("touched") or {}).items():
                if kind in self.policy.weights:
                    self.touch(str(item_id), str(kind), int(at))
                    touched.append(str(item_id))
        return sorted(set(touched))

    # -- decisions -------------------------------------------------------

    def pin(self, item_id: str, on: bool = True) -> None:
        self._item(item_id)["pinned"] = bool(on)

    def retire(self, item_id: str, reason: str = "manual") -> None:
        entry = self._item(item_id)
        entry["status"] = RETIRED
        entry["retired"] = {"at": self.clock, "reason": reason}

    def revive(self, item_id: str) -> None:
        """Back to active with full grace: a revival is the strongest touch."""
        entry = self._item(item_id)
        entry["status"] = ACTIVE
        entry.pop("retired", None)
        entry["touched"][self.policy.strongest] = self.clock

    def forget(self, item_id: str) -> None:
        self.items.pop(item_id, None)

    # -- reading ---------------------------------------------------------

    def is_protected(self, item_id: str) -> bool:
        return any(item_id.startswith(p) for p in self.policy.protected)

    def grace_left(self, item_id: str) -> int | None:
        """Ticks until this item retires by decay; None when it cannot (pinned,
        protected, policy off, or already retired)."""
        entry = self.items.get(item_id)
        if entry is None or not self.policy.enabled or entry.get("pinned") or self.is_protected(item_id):
            return None
        if entry.get("status") == RETIRED:
            return None
        touched = entry.get("touched") or {}
        if not touched:
            return 0
        expiry = max(
            int(at) + self.policy.weights.get(kind, 0) * self.policy.grace
            for kind, at in touched.items()
            if kind in self.policy.weights
        )
        return max(0, expiry - self.clock)

    def standing(self, item_id: str) -> Standing:
        entry = self._item(item_id) if item_id in self.items else {"touched": {}, "pinned": False, "status": ACTIVE}
        retired = entry.get("retired") or {}
        return Standing(
            id=item_id,
            status=entry.get("status", ACTIVE),
            pinned=bool(entry.get("pinned")),
            protected=self.is_protected(item_id),
            touched={str(k): int(v) for k, v in (entry.get("touched") or {}).items()},
            grace_left=self.grace_left(item_id),
            retired_at=retired.get("at"),
            retired_reason=retired.get("reason"),
        )

    def active(self) -> list[str]:
        return sorted(i for i, e in self.items.items() if e.get("status", ACTIVE) == ACTIVE)

    def retired(self) -> list[str]:
        return sorted(i for i, e in self.items.items() if e.get("status") == RETIRED)

    def due(self) -> list[str]:
        """Items that would retire on the next tick."""
        return sorted(i for i in self.items if self.grace_left(i) == 0 and self.items[i].get("status", ACTIVE) == ACTIVE)

    # -- the clock -------------------------------------------------------

    def tick(self, known: Iterable[str] | None = None) -> list[str]:
        """Advance the clock by one and retire what has run out. `known` is the
        full set of ids that exist now: new ones are seen (full grace), ids no
        longer present are forgotten. Returns the ids retired this tick."""
        if known is not None:
            present = set(known)
            for item_id in present:
                self.see(item_id)
            for item_id in list(self.items):
                if item_id not in present:
                    self.forget(item_id)
        self.clock += 1
        retired: list[str] = []
        if self.policy.enabled:
            for item_id in sorted(self.items):
                if self.items[item_id].get("status", ACTIVE) == ACTIVE and self.grace_left(item_id) == 0:
                    self.retire(item_id, reason="decay")
                    retired.append(item_id)
        return retired
