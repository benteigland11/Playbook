import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.bm25_ngram import Field, char_ngrams, search, word_tokens

RECORDS = {
    "a": {"title": "Login handler", "body": "session cookie", "tags": ["auth"]},
    "b": {"title": "Logins are broken", "body": "reported by support", "tags": []},
    "c": {"title": "Cooking soup", "body": "unrelated notes", "tags": ["food"]},
}


def test_word_tokens_and_stopwords() -> None:
    assert word_tokens("Login Handler!") == ["login", "handler"]
    assert word_tokens("I want login", stopwords=["i", "want"]) == ["login"]


def test_char_ngrams_slides_and_keeps_short_words() -> None:
    assert char_ngrams("mcp", n=3) == ["mcp"]
    assert "log" in char_ngrams("login", n=3)
    assert "ogi" in char_ngrams("login", n=3)


def test_empty_query_returns_no_hits() -> None:
    assert search(RECORDS, "", ["title"]) == []
    assert search(RECORDS, "   ", ["title"]) == []


def test_word_match_ranks_title_over_body() -> None:
    hits = search(RECORDS, "login", [Field("title", 3.0), Field("body", 1.0)])
    ids = [hit.item_id for hit in hits]
    assert ids[0] == "a"
    assert "c" not in ids


def test_ngrams_match_morphology() -> None:
    hits = search(RECORDS, "login", [Field("title", 1.0)], ngram=3, ngram_weight=1.0)
    ids = {hit.item_id for hit in hits}
    assert "a" in ids
    assert "b" in ids


def test_intent_sentence_does_not_require_every_token() -> None:
    hits = search(
        RECORDS,
        "I want to fix the login session please",
        [Field("title", 2.0), Field("body", 1.0), Field("tags", 2.0)],
        stopwords=["i", "want", "to", "fix", "the", "please"],
    )
    assert hits
    assert hits[0].item_id == "a"


def test_require_word_hit_drops_ngram_only_junk() -> None:
    records = {
        "real": {"title": "login handler"},
        "junk": {"title": "logistics catalog"},
    }
    loose = search(records, "login", ["title"], ngram=3, ngram_weight=1.0)
    assert {hit.item_id for hit in loose} >= {"real", "junk"}
    strict = search(records, "login", ["title"], ngram=3, ngram_weight=1.0, require_word_hit=True)
    assert [hit.item_id for hit in strict] == ["real"]


def test_min_ratio_keeps_only_near_the_top() -> None:
    records = {
        "best": {"title": "login", "body": ""},
        "weak": {"title": "other", "body": "login"},
        "none": {"title": "zzz", "body": ""},
    }
    hits = search(
        records,
        "login",
        [Field("title", 8.0), Field("body", 0.1)],
        min_ratio=0.5,
    )
    assert [hit.item_id for hit in hits] == ["best"]


def test_limit_and_field_strings() -> None:
    hits = search(RECORDS, "login", ["title"], limit=1)
    assert len(hits) == 1


def test_rejects_bad_parameters() -> None:
    with pytest.raises(ValueError):
        Field("", 1.0)
    with pytest.raises(ValueError):
        Field("title", 0)
    with pytest.raises(ValueError):
        char_ngrams("x", n=0)
    with pytest.raises(ValueError):
        search(RECORDS, "login", [])
    with pytest.raises(ValueError):
        search(RECORDS, "login", ["title"], ngram=0)
    with pytest.raises(ValueError):
        search(RECORDS, "login", ["title"], ngram_weight=-1)
    with pytest.raises(ValueError):
        search(RECORDS, "login", ["title"], b=2)
    with pytest.raises(ValueError):
        search(RECORDS, "login", ["title"], k1=-1)
    with pytest.raises(ValueError):
        search(RECORDS, "login", ["title"], min_score=-1)
    with pytest.raises(ValueError):
        search(RECORDS, "login", ["title"], min_ratio=2)


def test_object_records_and_ids_filter() -> None:
    class Item:
        def __init__(self, title: str) -> None:
            self.title = title

    records = {"z": Item("login"), "skip": Item("cooking")}
    hits = search(records, "login", ["title"], ids=["z", "skip"])
    assert [hit.item_id for hit in hits] == ["z"]


def test_lists_flatten_and_bools() -> None:
    records = {"x": {"title": ["alpha", "beta"], "flag": True}}
    hits = search(records, "alpha", ["title"])
    assert hits[0].item_id == "x"
    hits = search({"y": {"title": "true"}}, "true", ["title"])
    assert hits[0].item_id == "y"
