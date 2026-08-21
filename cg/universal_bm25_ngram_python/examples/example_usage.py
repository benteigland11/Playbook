"""
Example usage of BM25 N-gram Search.

This file must run and exit cleanly with no user input, no network calls,
and no external services or API keys. Use fake/hardcoded data to demonstrate the API.
The widget's own declared dependencies are fine - the validator installs them first.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.bm25_ngram import Field, search

records = {
    "a": {"title": "Login handler", "body": "session cookie"},
    "b": {"title": "Logins are broken", "body": "support ticket"},
    "c": {"title": "Cooking soup", "body": "unrelated notes"},
}

hits = search(
    records,
    "I want to fix logins",
    [Field("title", 3.0), Field("body", 1.0)],
    stopwords=["i", "want", "to"],
)
print(hits[0].item_id)
print(round(hits[0].score, 2))
print("c" in {hit.item_id for hit in hits})
