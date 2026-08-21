"""
Example usage of GOFAI Procedure.

This file must run and exit cleanly with no user input, no network calls,
and no external services or API keys. Use fake/hardcoded data to demonstrate the API.
The widget's own declared dependencies are fine - the validator installs them first.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.gofai_procedure import add_step, dump_procedure, edit_step, new_procedure, parse_procedure, validate_procedure

document = new_procedure("item", "Item", "choose a mode for an item", ["example"])
document = add_step(document, "Ask for mode", "Set mode to draft, exploratory, or final.")
document = add_step(document, "Draft path", "Skip the stamp.")
document = edit_step(document, "Draft path", do="Skip the stamp when mode is draft.")

result = validate_procedure(document)
text = dump_procedure(document)
parsed = parse_procedure(text)

print(result.valid)
print(parsed["id"])
print([step["id"] for step in parsed["steps"]])
print([step["title"] for step in parsed["steps"]])
