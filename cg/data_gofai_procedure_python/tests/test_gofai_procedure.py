import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.gofai_procedure import (
    add_step,
    dump_procedure,
    edit_procedure,
    edit_step,
    new_procedure,
    parse_procedure,
    remove_step,
    validate_procedure,
)

DESC = "short description of the item"
TITLE = "Item"
TAGS = ["alpha"]


def test_new_procedure_is_valid_empty_document() -> None:
    document = new_procedure("item", TITLE, DESC, TAGS)
    result = validate_procedure(document)
    assert result.valid is True
    assert document == {"id": "item", "title": TITLE, "description": DESC, "tags": TAGS, "steps": []}


def test_new_procedure_rejects_blank_id() -> None:
    with pytest.raises(ValueError):
        new_procedure("  ", TITLE, DESC, TAGS)


def test_new_procedure_rejects_blank_title() -> None:
    with pytest.raises(ValueError):
        new_procedure("item", "  ", DESC, TAGS)


def test_new_procedure_rejects_blank_description() -> None:
    with pytest.raises(ValueError):
        new_procedure("item", TITLE, "  ", TAGS)


def test_new_procedure_rejects_empty_tags() -> None:
    with pytest.raises(ValueError, match="non-empty array"):
        new_procedure("item", TITLE, DESC, [])


def test_new_procedure_rejects_duplicate_tags() -> None:
    with pytest.raises(ValueError, match="duplicate tag"):
        new_procedure("item", TITLE, DESC, ["alpha", "alpha"])


def test_roundtrip_parse_dump_bytes() -> None:
    document = new_procedure("item", TITLE, DESC, TAGS)
    text = dump_procedure(document)
    parsed = parse_procedure(text.encode("utf-8"))
    assert parsed == document


def test_parse_rejects_malformed_json() -> None:
    with pytest.raises(ValueError, match="malformed JSON"):
        parse_procedure("{")


def test_parse_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="object"):
        parse_procedure("[]")


def test_parse_rejects_non_utf8_bytes() -> None:
    with pytest.raises(ValueError, match="utf-8"):
        parse_procedure(b"\xff\xfe")


def test_parse_rejects_non_str_bytes() -> None:
    with pytest.raises(TypeError):
        parse_procedure(123)  # type: ignore[arg-type]


def test_dump_rejects_non_mapping() -> None:
    with pytest.raises(TypeError):
        dump_procedure(["x"])  # type: ignore[arg-type]


def test_add_step_appends_title_and_do() -> None:
    document = add_step(
        new_procedure("item", TITLE, DESC, TAGS),
        "Ask for mode",
        "Set mode to draft, exploratory, or final.",
        step_id="s1",
    )
    document = add_step(document, "Draft path", "Skip the stamp.", step_id="s2")
    result = validate_procedure(document)
    assert result.valid is True
    assert [step["title"] for step in document["steps"]] == ["Ask for mode", "Draft path"]
    assert [step["id"] for step in document["steps"]] == ["s1", "s2"]


def test_add_step_assigns_random_hex_id() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask for mode", "Set the mode.")
    document = add_step(document, "Draft path", "Skip the stamp.")
    ids = [step["id"] for step in document["steps"]]
    assert len(ids) == 2
    assert ids[0] != ids[1]
    for step_id in ids:
        assert len(step_id) == 32
        int(step_id, 16)


def test_add_step_rejects_duplicate_explicit_id() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask", "First.", step_id="s1")
    with pytest.raises(ValueError, match="duplicate step id"):
        add_step(document, "Other", "Second.", step_id="s1")


def test_add_step_inserts_after_title() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "A", "Do A.", step_id="s1")
    document = add_step(document, "C", "Do C.", step_id="s3")
    document = add_step(document, "B", "Do B.", step_id="s2", after="A")
    assert [step["title"] for step in document["steps"]] == ["A", "B", "C"]
    assert [step["id"] for step in document["steps"]] == ["s1", "s2", "s3"]


def test_add_step_after_unknown_title() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "A", "Do A.")
    with pytest.raises(ValueError, match="no step titled"):
        add_step(document, "B", "Do B.", after="Missing")


def test_add_step_after_blank() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "A", "Do A.")
    with pytest.raises(ValueError, match="after"):
        add_step(document, "B", "Do B.", after="  ")


def test_add_step_rejects_duplicate_title() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask for mode", "Set the mode.")
    with pytest.raises(ValueError, match="duplicate title"):
        add_step(document, "Ask for mode", "Set it again.")


def test_add_step_rejects_blank_fields() -> None:
    document = new_procedure("item", TITLE, DESC, TAGS)
    with pytest.raises(ValueError, match="title"):
        add_step(document, "  ", "do it")
    with pytest.raises(ValueError, match="do"):
        add_step(document, "Ask", "  ")
    with pytest.raises(ValueError, match="step id"):
        add_step(document, "Ask", "Do it.", step_id="  ")


def test_edit_procedure_updates_metadata() -> None:
    document = new_procedure("item", TITLE, DESC, TAGS)
    updated = edit_procedure(document, title="Renamed", description="longer text here.", tags=["beta"])
    assert updated["id"] == "item"
    assert updated["title"] == "Renamed"
    assert updated["description"] == "longer text here."
    assert updated["tags"] == ["beta"]


def test_edit_procedure_requires_a_change() -> None:
    with pytest.raises(ValueError, match="title, description, and/or tags"):
        edit_procedure(new_procedure("item", TITLE, DESC, TAGS))


def test_edit_procedure_rejects_blank_fields() -> None:
    document = new_procedure("item", TITLE, DESC, TAGS)
    with pytest.raises(ValueError, match="title"):
        edit_procedure(document, title="  ")
    with pytest.raises(ValueError, match="description"):
        edit_procedure(document, description="  ")


def test_edit_procedure_rejects_empty_tags() -> None:
    with pytest.raises(ValueError, match="non-empty array"):
        edit_procedure(new_procedure("item", TITLE, DESC, TAGS), tags=[])


def test_edit_step_changes_do_and_keeps_id() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask for mode", "Old do.", step_id="s1")
    updated = edit_step(document, "Ask for mode", do="New do.")
    assert updated["steps"][0]["do"] == "New do."
    assert updated["steps"][0]["id"] == "s1"
    assert updated["steps"][0]["title"] == "Ask for mode"


def test_edit_step_renames_title() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask for mode", "Set it.", step_id="s1")
    updated = edit_step(document, "Ask for mode", new_title="Pick a mode")
    assert updated["steps"][0]["title"] == "Pick a mode"
    assert updated["steps"][0]["id"] == "s1"


def test_edit_step_rejects_unknown_title() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask for mode", "Set it.")
    with pytest.raises(ValueError, match="no step titled"):
        edit_step(document, "Missing", do="x")


def test_edit_step_rejects_duplicate_rename() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask", "First.", step_id="s1")
    document = add_step(document, "Other", "Second.", step_id="s2")
    with pytest.raises(ValueError, match="duplicate title"):
        edit_step(document, "Other", new_title="Ask")


def test_edit_step_requires_a_change() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask", "Do it.")
    with pytest.raises(ValueError, match="new_title and/or do"):
        edit_step(document, "Ask")


def test_edit_step_requires_steps_array() -> None:
    with pytest.raises(ValueError, match="steps must be an array"):
        edit_step({"id": "item", "description": DESC, "tags": TAGS, "steps": {}}, "Ask", do="x")


def test_edit_step_rejects_blank_fields() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask", "Do it.")
    with pytest.raises(ValueError, match="title"):
        edit_step(document, "  ", do="x")
    with pytest.raises(ValueError, match="new_title"):
        edit_step(document, "Ask", new_title="  ")
    with pytest.raises(ValueError, match="do"):
        edit_step(document, "Ask", do="  ")


def test_remove_step_fuses_remaining_order() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "A", "Do A.", step_id="s1")
    document = add_step(document, "B", "Do B.", step_id="s2")
    document = add_step(document, "C", "Do C.", step_id="s3")
    updated = remove_step(document, "B")
    assert [step["title"] for step in updated["steps"]] == ["A", "C"]
    assert [step["id"] for step in updated["steps"]] == ["s1", "s3"]


def test_remove_step_rejects_unknown_title() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask", "Do it.")
    with pytest.raises(ValueError, match="no step titled"):
        remove_step(document, "Missing")


def test_remove_step_rejects_blank_title() -> None:
    document = add_step(new_procedure("item", TITLE, DESC, TAGS), "Ask", "Do it.")
    with pytest.raises(ValueError, match="title"):
        remove_step(document, "  ")


def test_remove_step_requires_steps_array() -> None:
    with pytest.raises(ValueError, match="steps must be an array"):
        remove_step({"id": "item", "description": DESC, "tags": TAGS, "steps": {}}, "Ask")


def test_add_step_requires_steps_array() -> None:
    with pytest.raises(ValueError, match="steps must be an array"):
        add_step({"id": "item", "description": DESC, "tags": TAGS, "steps": {}}, "Ask", "Do it")


def test_validate_rejects_non_mapping_document() -> None:
    result = validate_procedure(["x"])  # type: ignore[arg-type]
    assert result.valid is False
    assert result.errors[0].message == "expected object"


def test_validate_rejects_unknown_top_level_key() -> None:
    document = new_procedure("item", TITLE, DESC, TAGS)
    document["extra"] = 1
    result = validate_procedure(document)
    assert result.valid is False
    assert result.errors[0].path == "$"


def test_validate_missing_and_wrong_shaped_fields() -> None:
    result = validate_procedure({"id": "", "title": "", "description": "", "tags": "nope", "steps": {}})
    assert result.valid is False
    paths = {err.path for err in result.errors}
    assert "$.id" in paths
    assert "$.title" in paths
    assert "$.description" in paths
    assert "$.tags" in paths
    assert "$.steps" in paths

    missing = validate_procedure({})
    assert {err.path for err in missing.errors} >= {"$.id", "$.title", "$.description", "$.tags", "$.steps"}


def test_validate_rejects_bad_tags() -> None:
    document = new_procedure("item", TITLE, DESC, TAGS)
    document["tags"] = ["ok", "", "ok"]
    assert validate_procedure(document).valid is False
    empty = new_procedure("item", TITLE, DESC, TAGS)
    empty["tags"] = []
    assert validate_procedure(empty).valid is False


def test_validate_step_shape_errors() -> None:
    document = {
        "id": "item",
        "title": TITLE,
        "description": DESC,
        "tags": TAGS,
        "steps": [
            "nope",
            {"extra": 1},
            {"title": "  "},
            {"title": "Ask", "do": "  "},
            {"id": "ask", "title": "Ask", "do": "again"},
            {"id": "ask", "title": "Other", "do": "dup id"},
        ],
    }
    result = validate_procedure(document)
    assert result.valid is False
