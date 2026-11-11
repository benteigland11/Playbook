from __future__ import annotations

import json
from pathlib import Path

import pytest

from playbook.cli import main
from playbook.store import procedure_path


@pytest.fixture(autouse=True)
def searched_tree(tmp_path: Path, monkeypatch) -> None:
    """create is gated behind a search from the working tree; these tests are about the rest."""
    monkeypatch.chdir(tmp_path)
    (tmp_path/".playbook").mkdir(exist_ok=True)
    (tmp_path/".playbook"/".searched").write_text("fixture\n")


@pytest.fixture(autouse=True)
def _no_cooldown(monkeypatch):
    from playbook import ops


def _plan(walk: str, n: int = 60) -> None:
    """The plan a worker writes before its first tick: every step placed under an action."""
    Path(walk[:-3] + ".plan.md").write_text(f"1. steps 1-{n}: do them against this piece\n")


def test_create_is_refused_until_the_tree_has_searched(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    (tmp_path/".playbook"/".searched").unlink()
    assert main(["create", "gated", "--title", "G", "--description", "d", "--tags", "a"]) == 1
    assert "Search first" in capsys.readouterr().out
    assert main(["search", "gated", "--limit", "2"]) == 0
    assert (tmp_path/".playbook"/".searched").read_text().strip() == "gated"
    assert main(["create", "gated", "--title", "G", "--description", "d", "--tags", "a"]) == 0
    assert main(["create", "gated2", "--title", "G", "--description", "d", "--tags", "a", "--unsearched"]) == 0


def test_cli_create_add_validate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "item", "--title", "Item", "--description", "choose a mode", "--tags", "alpha"]) == 0
    path = procedure_path("item")
    assert main(["add-step", "item", "--title", "Ask for mode", "--do", "Set mode to draft or final."]) == 0
    assert main(["validate", "item"]) == 0
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["id"] == "item"
    assert document["title"] == "Item"
    assert document["description"] == "choose a mode"
    assert document["tags"] == ["alpha"]
    assert len(document["steps"][0]["id"]) == 32
    int(document["steps"][0]["id"], 16)
    assert document["steps"][0]["title"] == "Ask for mode"
    assert document["steps"][0]["do"] == "Set mode to draft or final."
    assert main(["edit-step", "item", "--title", "Ask for mode", "--do", "Set mode to draft, exploratory, or final."]) == 0
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["steps"][0]["do"] == "Set mode to draft, exploratory, or final."
    assert main(["edit-step", "item", "--title", "Ask for mode", "--rename", "Pick a mode"]) == 0
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["steps"][0]["title"] == "Pick a mode"
    assert len(document["steps"][0]["id"]) == 32
    assert main(["add-step", "item", "--title", "Later", "--do", "Do later."]) == 0
    assert main(["add-step", "item", "--title", "Middle", "--do", "Do middle.", "--after", "Pick a mode"]) == 0
    document = json.loads(path.read_text(encoding="utf-8"))
    assert [step["title"] for step in document["steps"]] == ["Pick a mode", "Middle", "Later"]
    assert main(["remove-step", "item", "--title", "Pick a mode"]) == 0
    document = json.loads(path.read_text(encoding="utf-8"))
    assert [step["title"] for step in document["steps"]] == ["Middle", "Later"]


def test_cli_load_and_start(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "item", "--title", "Item", "--description", "choose a mode", "--tags", "alpha"]) == 0
    assert main(["add-step", "item", "--title", "First", "--do", "Do first."]) == 0
    assert main(["add-step", "item", "--title", "Second", "--do", "Do second."]) == 0
    assert main(["add-step", "item", "--title", "Third", "--do", "Do third."]) == 0
    capsys.readouterr()
    assert main(["load", "item"]) == 0
    loaded = json.loads(capsys.readouterr().out)
    assert loaded["title"] == "Item"
    assert loaded["full"] is True
    assert [step["title"] for step in loaded["steps"]] == ["First", "Second", "Third"]
    assert [step["do"] for step in loaded["steps"]] == ["Do first.", "Do second.", "Do third."]
    assert all("id" not in step for step in loaded["steps"])
    assert main(["load", "item", "--titles"]) == 0
    outline = json.loads(capsys.readouterr().out)
    assert outline["full"] is False
    assert [step["title"] for step in outline["steps"]] == ["First", "Second", "Third"]
    assert all("do" not in step for step in outline["steps"])
    assert main(["load", "item", "--full"]) == 0
    whole = json.loads(capsys.readouterr().out)
    assert whole["full"] is True
    assert [step["do"] for step in whole["steps"]] == ["Do first.", "Do second.", "Do third."]
    assert main(["load", "item", "--titles", "--full"]) == 1
    capsys.readouterr()
    for alias in ("show", "get"):
        assert main([alias, "item"]) == 0
        assert json.loads(capsys.readouterr().out)["title"] == "Item"
    capsys.readouterr()
    assert main(["start", "item", "--title", "Second"]) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["at"] == "Second"
    assert started["do"] == "Do second."
    assert started["position"] == "2/3"
    assert started["prev"] == "First"
    assert started["next"] == "Third"
    assert main(["start", "item", "--title", "First"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["prev"] is None
    assert first["position"] == "1/3"
    assert main(["edit", "item", "--title", "Renamed item", "--tags", "beta"]) == 0
    edited = json.loads(capsys.readouterr().out)
    assert edited["title"] == "Renamed item"
    assert edited["tags"] == ["beta"]
    assert edited["description"] == "choose a mode"


def test_cli_validate_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    path = procedure_path("bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"id": "bad", "description": "x", "tags": ["alpha"], "steps": [{"title": "Ask"}]}\n',
        encoding="utf-8",
    )
    assert main(["validate", "bad"]) == 2


def test_cli_errors_are_json(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["add-step", "missing", "--title", "Ask", "--do", "Do it."]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "error" in payload


def test_cli_search(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "cli-to-mcp", "--title", "CLI to MCP", "--description", "verbose JSON CLI into condensed MCP", "--tags", "cli,mcp"]) == 0
    assert main(["create", "other", "--title", "Cooking notes", "--description", "unrelated cooking notes", "--tags", "food"]) == 0
    capsys.readouterr()
    assert main(["search", "mcp"]) == 0
    ranked = json.loads(capsys.readouterr().out)
    assert ranked["ok"] is True
    assert ranked["hits"][0]["id"] == "cli-to-mcp"
    assert ranked["hits"][0]["title"] == "CLI to MCP"
    assert "description" in ranked["hits"][0]
    assert "do" not in ranked["hits"][0]
    assert "titles" not in ranked["hits"][0]
    assert main(["search", "I", "want", "to", "turn", "a", "CLI", "into", "MCP", "tools"]) == 0
    intent = json.loads(capsys.readouterr().out)
    assert intent["hits"][0]["id"] == "cli-to-mcp"
    assert main(["search"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {hit["id"] for hit in listed["hits"]} == {"cli-to-mcp", "other"}
    assert listed["limit"] == 8
    assert listed["total"] == 2
    assert main(["search", "--limit", "1"]) == 0
    truncated = json.loads(capsys.readouterr().out)
    assert len(truncated["hits"]) == 1
    assert truncated["total"] == 2
    assert truncated["limit"] == 1





def test_create_with_steps_in_one_call(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    steps = json.dumps(
        [
            {"title": "First", "do": "Do first."},
            {"title": "Second", "do": "Do second."},
            {"title": "Third", "do": "Do third."},
        ]
    )
    assert main(
        ["create", "item", "--title", "Item", "--description", "when to pick", "--tags", "alpha", "--steps", steps]
    ) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["steps"] == 3
    document = json.loads(procedure_path("item").read_text(encoding="utf-8"))
    assert [step["title"] for step in document["steps"]] == ["First", "Second", "Third"]
    assert [step["do"] for step in document["steps"]] == ["Do first.", "Do second.", "Do third."]
    assert len({step["id"] for step in document["steps"]}) == 3

    more = json.dumps([{"title": "Fourth", "do": "Do fourth."}, {"title": "Fifth", "do": "Do fifth."}])
    assert main(["add-steps", "item", "--steps", more]) == 0
    appended = json.loads(capsys.readouterr().out)
    assert appended["added"] == ["Fourth", "Fifth"]
    assert appended["steps"] == ["First", "Second", "Third", "Fourth", "Fifth"]

    inserted = json.dumps([{"title": "1a", "do": "Do 1a."}, {"title": "1b", "do": "Do 1b."}])
    assert main(["add-steps", "item", "--steps", inserted, "--after", "First"]) == 0
    document = json.loads(procedure_path("item").read_text(encoding="utf-8"))
    assert [step["title"] for step in document["steps"]] == [
        "First", "1a", "1b", "Second", "Third", "Fourth", "Fifth",
    ]

    assert main(["add-steps", "item", "--steps", '[{"title": "Broken"}]']) == 1
    assert main(["add-steps", "item", "--steps", "not json"]) == 1


def test_batch_add_is_atomic(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "item", "--title", "Item", "--description", "when to pick", "--tags", "alpha"]) == 0
    assert main(["add-step", "item", "--title", "Kept", "--do", "Keep this."]) == 0
    bad = json.dumps([{"title": "Good", "do": "Fine."}, {"title": "Bad", "do": ""}])
    assert main(["add-steps", "item", "--steps", bad]) == 1
    document = json.loads(procedure_path("item").read_text(encoding="utf-8"))
    assert [step["title"] for step in document["steps"]] == ["Kept"]



def test_search_falls_back_to_weak_matches(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(
        [
            "create", "rollback-deploy",
            "--title", "Roll back a bad deploy",
            "--description", "Use when a release is live and breaking users and you need the previous version back.",
            "--tags", "deploy,rollback,release",
        ]
    ) == 0
    capsys.readouterr()
    assert main(["search", "roll back a deploy"]) == 0
    strong = json.loads(capsys.readouterr().out)
    assert strong["hits"][0]["id"] == "rollback-deploy"
    assert "weak" not in strong

    assert main(["search", "the deployment went sideways"]) == 0
    weak = json.loads(capsys.readouterr().out)
    assert weak["weak"] is True
    assert weak["hits"][0]["id"] == "rollback-deploy"
    assert "note" in weak
    assert len(weak["hits"]) <= 3

    # An unrelated query still surfaces a low-scoring candidate rather than an
    # empty list. That is deliberate: the payload is labelled weak and the note
    # tells the caller to check the description before trusting it.
    assert main(["search", "unrelated bread baking question"]) == 0
    junk = json.loads(capsys.readouterr().out)
    assert junk["weak"] is True
    assert "only use one if it genuinely covers the job" in junk["note"]
    assert junk["hits"][0]["score"] < weak["hits"][0]["score"]


def test_validate_reports_every_schema_error(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    path = procedure_path("bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"id": "bad", "description": "x", "tags": ["alpha"], "steps": [{"title": "Ask"}]}\n',
        encoding="utf-8",
    )
    assert main(["validate", "bad"]) == 2


def test_a_step_can_link_another_procedure_and_open_renders_the_walk(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "measure-gutters", "--title", "Measure gutters", "--description", "d", "--tags", "layout"]) == 0
    assert main(["add-step", "measure-gutters", "--title", "Read", "--do", "page readings at 375 and 1280."]) == 0
    assert main(["create", "build-page", "--title", "Build a page", "--description", "d", "--tags", "layout"]) == 0
    # A dangling link is refused; a real one is stored and validates.
    assert main(["add-step", "build-page", "--title", "Gutters", "--do", "Check the gutters.", "--procedure", "no-such"]) == 1
    assert "does not exist" in capsys.readouterr().out
    assert main(["add-step", "build-page", "--title", "Gutters", "--do", "Check the gutters.", "--procedure", "measure-gutters"]) == 0
    assert main(["validate", "build-page"]) == 0
    document = json.loads(procedure_path("build-page").read_text(encoding="utf-8"))
    assert document["steps"][0]["procedure"] == "measure-gutters"
    capsys.readouterr()
    assert main(["open", "build-page", "--for", "the landing page", "--dir", str(tmp_path), "--nested"]) == 0
    text = next((tmp_path/".playbook"/"open").glob("build-page--*.md")).read_text()
    assert 'playbook open measure-gutters --for "the landing page: Gutters"' in text
    # Unlink, and a link to a procedure deleted afterwards shows up in validate.
    assert main(["edit-step", "build-page", "--title", "Gutters", "--procedure", ""]) == 0
    assert "procedure" not in json.loads(procedure_path("build-page").read_text(encoding="utf-8"))["steps"][0]
    assert main(["edit-step", "build-page", "--title", "Gutters", "--procedure", "measure-gutters"]) == 0
    procedure_path("measure-gutters").unlink()
    assert main(["validate", "build-page"]) == 2


def test_a_step_may_not_link_its_own_procedure(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "size-pack", "--title", "Size a pack", "--description", "d", "--tags", "power"]) == 0
    assert main(["add-step", "size-pack", "--title", "Mass", "--do", "Compute the mass."]) == 0
    # Refused at add and at edit time.
    assert main(["add-step", "size-pack", "--title", "Again", "--do", "See step 1.", "--procedure", "size-pack"]) == 1
    assert "link to its own procedure" in capsys.readouterr().out
    assert main(["edit-step", "size-pack", "--title", "Mass", "--procedure", "size-pack"]) == 1
    assert "link to its own procedure" in capsys.readouterr().out
    # A self-link written by hand shows up in validate.
    path = procedure_path("size-pack")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["steps"][0]["procedure"] = "size-pack"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["validate", "size-pack"]) != 0
    assert "links to its own procedure" in capsys.readouterr().out


def test_open_hands_back_an_unfinished_walk_and_steps_skip_one_at_a_time(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A worker whose window rolled over opened the same procedure again and left a fresh unticked copy for
    the gate to hold the task on; and it looked twice for a verb to mark a walk not needed."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "tune-pedal", "--title", "Tune the pedal", "--description", "d", "--tags", "midi"]) == 0
    assert main(["add-step", "tune-pedal", "--title", "Find harmonies", "--do", "List the harmony changes."]) == 0
    assert main(["add-step", "tune-pedal", "--title", "Place pedal", "--do", "Pedal at each change."]) == 0
    capsys.readouterr()
    assert main(["open", "tune-pedal", "--for", "the piece", "--dir", str(tmp_path)]) == 0
    first = json.loads(capsys.readouterr().out)["path"]
    _plan(first)
    # A second open, whatever the purpose, hands the unfinished walk back with its next step.
    assert main(["open", "tune-pedal", "--for", "the piece after the handoff", "--dir", str(tmp_path)]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["already_open"] and reply["path"] == first and reply["next"] == "1. Find harmonies" and reply["unticked"] == 2
    assert len([p for p in (tmp_path/".playbook"/"open").glob("tune-pedal--*.md") if not p.name.endswith(".plan.md")]) == 1
    # Tick the first step by hand; the next step moves on.
    path = Path(first)
    path.write_text(path.read_text().replace("- [ ] **1.", "- [x] **1.", 1))
    assert main(["open", "tune-pedal", "--for", "again", "--dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["next"] == "2. Place pedal"
    # --again is a deliberate second walk for a second thing.
    assert main(["open", "tune-pedal", "--for", "the second piece", "--dir", str(tmp_path), "--again"]) == 0
    second = json.loads(capsys.readouterr().out)["path"]
    _plan(second)
    assert second != first and len([p for p in (tmp_path/".playbook"/"open").glob("tune-pedal--*.md") if not p.name.endswith(".plan.md")]) == 2
    # Two unfinished walks: tick by id is ambiguous, tick by file is not. Several steps close in one call only
    # once the walk has been read (its plan written); then one reason covers the steps it is true of.
    assert main(["tick", "tune-pedal", "--skip", "1", "--because", "not needed", "--dir", str(tmp_path)]) == 1
    assert "2 unfinished walks" in capsys.readouterr().out
    plan = Path(second[:-3] + ".plan.md"); plan.unlink()
    assert main(["tick", second, "--skip", "1", "2", "--because", "no pedal part", "--dir", str(tmp_path)]) == 1
    assert "no plan yet" in capsys.readouterr().out
    _plan(second)
    assert main(["tick", second, "--skip", "1", "--dir", str(tmp_path)]) == 1
    assert "--because" in capsys.readouterr().out
    assert main(["tick", second, "--skip", "1", "2", "--because", "the piece has no pedal part", "--dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["unticked"] == []
    text = Path(second).read_text()
    assert "- [-] **1." in text and "- [-] **2." in text and text.count("not needed here: the piece has no pedal part") == 2
    # The first walk is now the only unfinished one: by id works and marks only what is left.
    assert main(["tick", "tune-pedal", "--skip", "2", "--because", "measured another way", "--dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["unticked"] == []
    assert "- [x] **1." in path.read_text() and "- [-] **2." in path.read_text()
    # Nothing unfinished: a plain open makes a fresh walk.
    assert main(["open", "tune-pedal", "--for", "a third piece", "--dir", str(tmp_path)]) == 0
    assert "already_open" not in json.loads(capsys.readouterr().out)
    assert main(["tick", "no-such", "--done", "1", "--note", "found it", "--dir", str(tmp_path)]) == 1


def test_search_lists_open_walks_here_first(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Local first: a worker searching again after a handoff sees what it already started before any hit."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "tune-pedal", "--title", "Tune the pedal", "--description", "pedal per harmony", "--tags", "midi"]) == 0
    assert main(["add-step", "tune-pedal", "--title", "Find harmonies", "--do", "List the harmony changes."]) == 0
    capsys.readouterr()
    assert main(["search", "pedal", "--dir", str(tmp_path)]) == 0
    assert "open_here" not in json.loads(capsys.readouterr().out)
    assert main(["open", "tune-pedal", "--for", "the piece", "--dir", str(tmp_path)]) == 0
    _plan(json.loads(capsys.readouterr().out)["path"])
    assert main(["search", "something unrelated", "--dir", str(tmp_path)]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert list(reply)[:2] == ["ok", "open_here"]
    assert reply["open_here"][0]["id"] == "tune-pedal" and reply["open_here"][0]["next"] == "1. Find harmonies"
    assert main(["tick", "tune-pedal", "--skip", "1", "--because", "no pedal part", "--dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["search", "pedal", "--dir", str(tmp_path)]) == 0
    assert "open_here" not in json.loads(capsys.readouterr().out)


def test_tick_marks_several_steps_in_one_call(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "tune-pedal", "--title", "Tune the pedal", "--description", "d", "--tags", "midi"]) == 0
    for n in ("One", "Two", "Three"):
        assert main(["add-step", "tune-pedal", "--title", n, "--do", n.lower()]) == 0
    capsys.readouterr()
    assert main(["open", "tune-pedal", "--for", "the piece", "--dir", str(tmp_path)]) == 0
    path = json.loads(capsys.readouterr().out)["path"]
    _plan(path)
    assert main(["tick", "tune-pedal", "--done", "1,3", "--skip", "2", "--because", "no second thing here", "--note", "found it", "--dir", str(tmp_path)]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["marked"] == [1, 2, 3] and reply["unticked"] == []
    text = Path(path).read_text()
    assert "- [x] **1." in text and "- [-] **2." in text and "- [x] **3." in text
    assert main(["tick", path, "--done", "9", "--note", "found it", "--dir", str(tmp_path)]) == 1
    assert "no such step" in capsys.readouterr().out
    assert main(["tick", path, "--done", "1", "--skip", "1", "--note", "found it", "--dir", str(tmp_path)]) == 1


def test_a_linked_step_ticks_only_after_its_procedure_is_walked(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A step that is another procedure closes only once that walk is closed — opened, every box ticked or the
    walk skipped with a reason. Ticking the parent on the strength of the widget call left three procedures
    unwalked in one task."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "pedal-per-harmony", "--title", "Pedal", "--description", "d", "--tags", "midi"]) == 0
    assert main(["add-step", "pedal-per-harmony", "--title", "Down after the onset", "--do", "cc64"]) == 0
    assert main(["create", "compose", "--title", "Compose", "--description", "d", "--tags", "midi"]) == 0
    assert main(["add-step", "compose", "--title", "Notes", "--do", "write them"]) == 0
    assert main(["add-step", "compose", "--title", "Pedal", "--do", "apply", "--procedure", "pedal-per-harmony"]) == 0
    capsys.readouterr()
    assert main(["open", "compose", "--for", "the piece", "--dir", str(tmp_path), "--nested"]) == 0
    parent = json.loads(capsys.readouterr().out)["path"]
    _plan(parent)
    assert main(["tick", parent, "--done", "1", "2", "--note", "found it", "--dir", str(tmp_path)]) == 1
    refusal = capsys.readouterr().out
    assert "step(s) 2 are other procedures" in refusal and "playbook open pedal-per-harmony" in refusal
    assert "- [ ] **2." in Path(parent).read_text()   # nothing ticked, not even step 1
    assert main(["tick", parent, "--skip", "2", "--because", "x", "--dir", str(tmp_path)]) == 1   # skipping it needs the walk opened
    assert "open it before deciding" in capsys.readouterr().out
    assert main(["open", "pedal-per-harmony", "--for", "the piece: Pedal", "--dir", str(tmp_path), "--nested"]) == 0
    child = json.loads(capsys.readouterr().out)["path"]
    _plan(child)
    assert main(["tick", parent, "--done", "2", "--note", "found it", "--dir", str(tmp_path)]) == 1   # opened but not finished: not done
    capsys.readouterr()
    # Opened is enough to skip it with a reason (the library's links run in circles; a closed-walk rule on both
    # sides deadlocks), and the reason goes under the step.
    assert main(["tick", parent, "--skip", "2", "--because", "its walk is open above; the pedal is placed there", "--dir", str(tmp_path)]) == 0
    assert "not needed here: its walk is open above" in Path(parent).read_text()
    Path(parent).write_text(Path(parent).read_text().replace("- [-] **2.", "- [ ] **2.", 1).replace("\n\n  not needed here: its walk is open above; the pedal is placed there", "", 1))
    capsys.readouterr()
    assert main(["tick", child, "--skip", "1", "--because", "the widget applies it", "--dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["tick", parent, "--done", "1", "2", "--note", "found it", "--dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["unticked"] == []


def test_a_link_that_leads_back_is_refused_and_reported(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A step links a narrower procedure it runs, never one that runs this one — through any number of others.
    The music library grew 266 such circles (validation linked voicing linked validation) and a worker holding
    both walks open could tick neither."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    for pid in ("compose", "validate", "pedal"):
        assert main(["create", pid, "--title", pid, "--description", "d", "--tags", "midi"]) == 0
    assert main(["add-step", "compose", "--title", "Validate", "--do", "x", "--procedure", "validate"]) == 0
    assert main(["add-step", "validate", "--title", "Pedal", "--do", "x", "--procedure", "pedal"]) == 0
    capsys.readouterr()
    assert main(["add-step", "pedal", "--title", "Back", "--do", "x", "--procedure", "compose"]) == 1
    out = capsys.readouterr().out
    assert "would make a circle" in out and "pedal -> compose -> validate -> pedal" in out
    assert main(["edit-step", "validate", "--title", "Pedal", "--procedure", "compose"]) == 1
    capsys.readouterr()
    # A circle written by hand (a library from before the rule) is reported by validate.
    import json as _json
    path = tmp_path/"playbook"/"procedures"/"pedal.json"
    doc = _json.loads(path.read_text()); doc["steps"].append(dict(id="s9", title="Back", do="x", procedure="compose")); path.write_text(_json.dumps(doc))
    assert main(["validate", "pedal"]) == 2
    reply = _json.loads(capsys.readouterr().out)
    assert not reply["valid"] and any("links a circle" in e["message"] for e in reply["errors"])


def test_a_flat_walk_inlines_links_cuts_at_a_boundary_and_edits_write_through(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    """One straight list through the links, each step naming its source; the cut lands where the method comes
    back to its own steps; edits by walk step change the procedure the step came from and re-render the block."""
    import json as _json
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    for pid, n in (("pedal", 3), ("compose", 0)):
        assert main(["create", pid, "--title", pid, "--description", "d", "--tags", "midi"]) == 0
        for i in range(1, n + 1):
            assert main(["add-step", pid, "--title", f"{pid} {i}", "--do", f"do {pid} {i}"]) == 0
    assert main(["add-step", "compose", "--title", "Notes", "--do", "write them"]) == 0
    assert main(["add-step", "compose", "--title", "Pedal", "--do", "apply", "--procedure", "pedal"]) == 0
    assert main(["add-step", "compose", "--title", "Rubato", "--do", "slow the end"]) == 0
    capsys.readouterr()
    assert main(["reach", "compose"]) == 0
    r = _json.loads(capsys.readouterr().out)
    assert r["steps"] == 6 and r["procedures"] == {"compose": 3, "pedal": 3} and r["walks"] == 1
    # limit 4 would cut inside pedal; the cut moves to where compose's own steps resume (after pedal 3).
    assert main(["open", "compose", "--for", "the piece", "--dir", str(tmp_path), "--limit", "4"]) == 0
    reply = _json.loads(capsys.readouterr().out)
    path = Path(reply["path"])
    _plan(str(path))
    assert reply["steps"] == 5 and reply["continues"] == 1 and "--from 5" in reply["next_walk"]
    text = path.read_text()
    assert "- [ ] **3. pedal 1**" in text and "source: `pedal` · step 1" in text and "continues: 1 more step" in text
    assert "→ this step is another procedure" not in text
    # Every step's text is open; nothing ticks before the plan, and the plan must place the step.
    assert "write them" in text and "do pedal 1" in text
    Path(str(path)[:-3] + ".plan.md").unlink()
    assert main(["tick", str(path), "--done", "1", "--note", "x", "--dir", str(tmp_path)]) == 1
    assert "no plan yet" in capsys.readouterr().out
    Path(str(path)[:-3] + ".plan.md").write_text("1. steps 1-2: the notes and the pedal call\n2. steps 4-5: the pedal itself\n")
    assert main(["tick", str(path), "--done", "3", "--note", "x", "--dir", str(tmp_path)]) == 1
    assert "not in" in capsys.readouterr().out
    _plan(str(path))
    assert main(["tick", str(path), "--done", "1", "2", "--note", "found it", "--dir", str(tmp_path)]) == 0
    reply = _json.loads(capsys.readouterr().out)
    assert reply["next"]["number"] == 3 and reply["next"]["do"] == "do pedal 1"
    text = path.read_text()
    assert "- [x] **1. Notes**" in text and "done: found it" in text
    assert main(["tick", str(path), "--done", "3", "--note", "found it", "--dir", str(tmp_path)]) == 0
    capsys.readouterr()
    text = path.read_text()
    assert "do pedal 2" in text
    # edit where you stand: walk step 4 is pedal step 2
    assert main(["edit-step", "--walk", str(path), "--step", "4", "--do", "release before the next harmony"]) == 0
    capsys.readouterr()
    doc = _json.loads((tmp_path/"playbook"/"procedures"/"pedal.json").read_text())
    assert doc["steps"][1]["do"] == "release before the next harmony"
    assert "release before the next harmony" in path.read_text() and "- [ ] **4. pedal 2**" in path.read_text()
    assert main(["tick", str(path), "--done", "4", "--note", "found it", "--dir", str(tmp_path)]) == 0
    capsys.readouterr()
    # add after walk step 3 (pedal 1): goes into pedal after step 1; the walk renumbers and later sources shift
    assert main(["add-step", "--walk", str(path), "--after", "3", "--title", "pedal 1b", "--do", "half pedal"]) == 0
    capsys.readouterr()
    doc = _json.loads((tmp_path/"playbook"/"procedures"/"pedal.json").read_text())
    assert [s["title"] for s in doc["steps"]] == ["pedal 1", "pedal 1b", "pedal 2", "pedal 3"]
    text = path.read_text()
    assert "- [ ] **4. pedal 1b**" in text and "- [x] **5. pedal 2**" in text and "source: `pedal` · step 3" in text and "- [x] **1. Notes**" in text
    assert "half pedal" in text   # the step you are on now
    # remove walk step 6 (pedal 3)
    assert main(["remove-step", "--walk", str(path), "--step", "6"]) == 0
    capsys.readouterr()
    doc = _json.loads((tmp_path/"playbook"/"procedures"/"pedal.json").read_text())
    assert [s["title"] for s in doc["steps"]] == ["pedal 1", "pedal 1b", "pedal 2"]
    assert "pedal 3" not in path.read_text() and "continues: 1 more step" in path.read_text()
    # the continuation is its own walk even while the first is open
    assert main(["open", "compose", "--for", "the piece", "--dir", str(tmp_path), "--from", "5"]) == 0
    reply = _json.loads(capsys.readouterr().out)
    assert "already_open" not in reply and reply["steps"] == 1 and "Rubato" in Path(reply["path"]).read_text()


def test_a_twin_procedure_is_refused_with_the_one_to_improve(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    """One composition method that grows, not a sibling per gym: a create whose id and title share the skill words
    of a procedure in the store is refused, naming it; a narrower procedure (one decision) is not a twin."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "compose-eight-bar-piano-midi", "--title", "Compose eight-bar piano MIDI with repeated melody", "--description", "d", "--tags", "midi,piano,composition"]) == 0
    capsys.readouterr()
    assert main(["create", "compose-nine-bar-block-chord-midi", "--title", "Compose a nine-bar block-chord MIDI", "--description", "d", "--tags", "midi,composition,chords,piano"]) == 1
    out = capsys.readouterr().out
    assert "compose-eight-bar-piano-midi" in out and "improve it" in out
    assert main(["create", "midi-pedal-per-harmony", "--title", "Apply MIDI pedal per harmony", "--description", "d", "--tags", "midi,pedal"]) == 0
    assert main(["create", "midi-closing-ritardando", "--title", "Set MIDI closing ritardando", "--description", "d", "--tags", "midi,tempo"]) == 0


def _search(capsys, *query: str) -> dict:
    capsys.readouterr()
    main(["search", *query])
    return json.loads(capsys.readouterr().out)


def test_a_decayed_procedure_returns_only_for_a_strong_match_and_a_walk_revives_it(tmp_path: Path, monkeypatch, capsys) -> None:
    """Decay molds the surface: a retired procedure stays out of an ordinary search, comes back for a query it
    answers clearly better than anything active (marked decayed), and walking it brings it back for good."""
    from playbook import library
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    main(["create", "amd-gpu-free-check", "--title", "Check an AMD GPU is free", "--description",
          "rocm-smi shows no process on the amd gpu before a llama server starts", "--tags", "amd,gpu,rocm"])
    main(["create", "check-the-server-port", "--title", "Check the server port is free", "--description",
          "before a server starts, nothing listens on its port", "--tags", "server,port"])
    main(["library", "decay", "amd-gpu-free-check"])
    assert library.retired_ids() == {"amd-gpu-free-check"}

    ordinary = _search(capsys, "server", "starts")
    assert [h["id"] for h in ordinary["hits"]] == ["check-the-server-port"] and "decayed_note" not in ordinary

    strong = _search(capsys, "amd", "gpu", "rocm")
    hit = next(h for h in strong["hits"] if h["id"] == "amd-gpu-free-check")
    assert hit["decayed"] is True and "decayed_note" in strong
    assert "amd-gpu-free-check" in library.retired_ids()   # seen in a search is not a revival

    main(["start", "amd-gpu-free-check"])
    assert "amd-gpu-free-check" not in library.retired_ids()


def test_a_walk_in_a_travelling_copy_revives_on_merge(tmp_path: Path, monkeypatch) -> None:
    from playbook import library
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    main(["create", "old-way", "--title", "Old way", "--description", "d", "--tags", "a"])
    main(["library", "decay", "old-way"])
    copy = library.load().to_dict()
    at = library.load().clock
    copy["items"]["old-way"]["touched"]["use"] = at
    library.merge(copy)
    assert "old-way" not in library.retired_ids()
