from __future__ import annotations

import json
from pathlib import Path

import pytest

from playbook.cli import main
from playbook.mcp import handle_message
from playbook.store import procedure_path


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


def test_mcp_create_and_list_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    listed = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert "playbook_create" in names
    assert "playbook_edit" in names
    assert "playbook_search" in names
    assert "playbook_open" in names
    assert "playbook_step" in names
    assert "playbook_add_step" not in names

    created = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "playbook_create",
                "arguments": {
                    "id": "item",
                    "title": "Item",
                    "description": "choose a mode",
                    "tags": ["alpha"],
                },
            },
        }
    )
    assert created is not None
    assert created["result"]["isError"] is False
    added = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "playbook_step",
                "arguments": {"id": "item", "op": "add", "title": "Ask for mode", "do": "Set the mode."},
            },
        }
    )
    assert added is not None
    assert added["result"]["isError"] is False
    assert procedure_path("item").is_file()


def test_mcp_open_returns_whole_procedure_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "item", "--title", "Item", "--description", "choose a mode", "--tags", "alpha"]) == 0
    assert main(["add-step", "item", "--title", "First", "--do", "Do first."]) == 0
    assert main(["add-step", "item", "--title", "Second", "--do", "Do second."]) == 0

    def call(arguments: dict) -> dict:
        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "playbook_open", "arguments": arguments},
            }
        )
        assert response is not None
        return response["result"]

    result = call({"id": "item"})
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["full"] is True
    assert [step["do"] for step in payload["steps"]] == ["Do first.", "Do second."]

    outline = json.loads(call({"id": "item", "full": False})["content"][0]["text"])
    assert outline["full"] is False
    assert all("do" not in step for step in outline["steps"])

    at_step = json.loads(call({"id": "item", "at": "Second"})["content"][0]["text"])
    assert at_step["do"] == "Do second."
    assert at_step["position"] == "2/2"

    assert call({"id": "item", "at": "Second", "full": True})["isError"] is True


def test_mcp_payloads_are_compact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["create", "item", "--title", "Item", "--description", "choose a mode", "--tags", "alpha"]) == 0
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "playbook_open", "arguments": {"id": "item"}},
        }
    )
    assert response is not None
    text = response["result"]["content"][0]["text"]
    assert "\n" not in text
    assert ", " not in text
    assert json.loads(text)["id"] == "item"


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


def test_mcp_create_and_step_accept_batches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    def call(name: str, arguments: dict) -> dict:
        response = handle_message(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        )
        assert response is not None
        return response["result"]

    created = call(
        "playbook_create",
        {
            "id": "item",
            "title": "Item",
            "description": "when to pick",
            "tags": ["alpha"],
            "steps": [{"title": "First", "do": "Do first."}, {"title": "Second", "do": "Do second."}],
        },
    )
    assert created["isError"] is False
    assert json.loads(created["content"][0]["text"])["steps"] == 2

    added = call(
        "playbook_step",
        {"id": "item", "op": "add", "steps": [{"title": "Third", "do": "Do third."}]},
    )
    assert added["isError"] is False
    assert json.loads(added["content"][0]["text"])["added"] == ["Third"]

    single = call("playbook_step", {"id": "item", "op": "add", "title": "Fourth", "do": "Do fourth."})
    assert single["isError"] is False
    document = json.loads(procedure_path("item").read_text(encoding="utf-8"))
    assert [step["title"] for step in document["steps"]] == ["First", "Second", "Third", "Fourth"]
    assert call("playbook_step", {"id": "item", "op": "remove"})["isError"] is True


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
