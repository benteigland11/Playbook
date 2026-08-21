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
    assert [step["title"] for step in loaded["steps"]] == ["First", "Second", "Third"]
    assert all("do" not in step for step in loaded["steps"])
    assert loaded["full"] is False
    assert main(["load", "item", "--full"]) == 0
    whole = json.loads(capsys.readouterr().out)
    assert whole["full"] is True
    assert [step["do"] for step in whole["steps"]] == ["Do first.", "Do second.", "Do third."]
    assert main(["start", "item", "--title", "Second"]) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["at"] == "Second"
    assert started["do"] == "Do second."
    assert started["before"] == ["First"]
    assert started["after"] == ["Third"]
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
