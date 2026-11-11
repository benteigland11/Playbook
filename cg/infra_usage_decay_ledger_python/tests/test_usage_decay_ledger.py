import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.usage_decay_ledger import ACTIVE, RETIRED, Ledger, Policy

ON = Policy(enabled=True, grace=2, weights={"edit": 3, "use": 2, "search": 1}, protected=("core-",))


def test_first_sighting_gets_full_grace_and_ticks_count_down():
    ledger = Ledger(policy=ON)
    assert ledger.see("item") is True
    assert ledger.see("item") is False
    assert ledger.grace_left("item") == 6  # edit is the strongest kind: 3 * 2
    for left in (5, 4, 3, 2, 1):
        assert ledger.tick() == []
        assert ledger.grace_left("item") == left
    assert ledger.tick() == ["item"]
    s = ledger.standing("item")
    assert s.status == RETIRED and s.retired_reason == "decay" and s.retired_at == 6
    assert ledger.retired() == ["item"] and ledger.active() == []


def test_each_kind_buys_its_own_grace_and_the_longest_wins():
    ledger = Ledger(policy=ON)
    ledger.touch("a", "search")
    ledger.touch("b", "use")
    assert ledger.grace_left("a") == 2 and ledger.grace_left("b") == 4
    ledger.tick()
    ledger.touch("a", "search")  # refreshed: 2 more from now
    assert ledger.grace_left("a") == 2
    ledger.tick(); ledger.tick()
    assert "a" in ledger.retired()
    assert ledger.grace_left("b") == 1
    with pytest.raises(ValueError):
        ledger.touch("b", "smell")


def test_pinned_protected_and_disabled_never_retire():
    ledger = Ledger(policy=ON)
    ledger.see("pinned"); ledger.pin("pinned")
    ledger.see("core-boot")
    for _ in range(20):
        ledger.tick()
    assert ledger.retired() == []
    assert ledger.standing("pinned").safe and ledger.standing("core-boot").protected
    ledger.pin("pinned", on=False)
    assert ledger.grace_left("pinned") == 0 and ledger.due() == ["pinned"]
    off = Ledger(policy=Policy(enabled=False, grace=1))
    off.see("x")
    for _ in range(5):
        assert off.tick() == []
    assert off.clock == 5 and off.grace_left("x") is None


def test_retire_revive_and_manual_reason():
    ledger = Ledger(policy=ON)
    ledger.see("x")
    ledger.retire("x")
    assert ledger.standing("x").retired_reason == "manual"
    assert ledger.grace_left("x") is None
    ledger.tick(); ledger.tick(); ledger.tick()
    ledger.revive("x")
    assert ledger.standing("x").status == ACTIVE
    assert ledger.grace_left("x") == 6  # a revival is the strongest touch


def test_tick_with_known_ids_sees_new_and_forgets_gone():
    ledger = Ledger(policy=ON)
    ledger.tick(known=["a", "b"])
    assert sorted(ledger.items) == ["a", "b"]
    ledger.tick(known=["b", "c"])
    assert sorted(ledger.items) == ["b", "c"]


def test_merge_takes_the_latest_stamp_per_kind_but_not_decisions():
    ledger = Ledger(policy=ON)
    ledger.see("a"); ledger.tick(); ledger.tick()
    travelled = {"items": {"a": {"touched": {"use": 1, "bogus": 9}, "pinned": True, "status": "retired"},
                           "new": {"touched": {"search": 2}}}}
    assert ledger.merge(travelled) == ["a", "new"]
    assert ledger.items["a"]["touched"]["use"] == 1
    assert "bogus" not in ledger.items["a"]["touched"]
    assert ledger.standing("a").pinned is False and ledger.standing("a").status == ACTIVE
    ledger.touch("a", "use", at=0)  # never moves backwards
    assert ledger.items["a"]["touched"]["use"] == 1


def test_round_trips_through_a_plain_dict():
    ledger = Ledger(policy=ON)
    ledger.see("a"); ledger.pin("a"); ledger.tick()
    again = Ledger(ledger.to_dict())
    assert again.clock == 1 and again.policy == ON and again.standing("a").pinned
    assert Policy.from_dict({"grace": 0}).grace == 1
    assert Policy.from_dict(None, default=ON) == ON
    assert Policy.from_dict({"weights": {}}).weights == Policy().weights
