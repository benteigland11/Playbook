"""A library of recipes that retires what nobody cooks. The clock is releases:
every release ticks the ledger; a recipe that was neither edited, cooked nor
even listed in a search for long enough is retired, but can be pinned or
brought back. No I/O: the caller keeps the dict wherever it likes."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.usage_decay_ledger import Ledger, Policy

policy = Policy(enabled=True, grace=3, weights={"edit": 3, "use": 2, "search": 1}, protected=("house-",))
ledger = Ledger(policy=policy)

recipes = ["house-bread", "soup", "salad", "cake"]
ledger.tick(known=recipes)                 # release 1: everything seen, full grace
ledger.pin("cake")                         # never retires
ledger.touch("soup", "use")                # cooked this release
for release in range(2, 12):
    retired = ledger.tick(known=recipes)
    if release == 5:
        ledger.touch("salad", "search")    # it came up in a search; a weak touch
    if retired:
        print(f"release {release}: retired {retired}")

for recipe in recipes:
    s = ledger.standing(recipe)
    print(f"{recipe:12} {s.status:8} pinned={s.pinned!s:5} protected={s.protected!s:5} grace_left={s.grace_left}")

ledger.revive("soup")
print("revived soup, grace left:", ledger.grace_left("soup"))
snapshot = ledger.to_dict()                # what the caller persists
assert Ledger(snapshot).standing("cake").pinned
