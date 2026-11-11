"""The procedure directory: default, PLAYBOOK_STORE, config file, one resolver."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from playbook.app_paths import resolve_app_paths
from playbook.cli import main
from playbook.mcp import handle_message
from playbook.store import config_file, default_procedures_dir, procedures_dir


def _silence_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))


def test_default_path_is_unchanged_without_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("PLAYBOOK_STORE", raising=False)
    _silence_config(monkeypatch, tmp_path)

    resolved = procedures_dir()
    assert resolved == default_procedures_dir()
    assert resolved == resolve_app_paths("playbook").data_dir / "procedures"
    if sys.platform.startswith("linux"):
        assert resolved == home / ".local" / "share" / "playbook" / "procedures"
    assert not resolved.exists()


def test_default_path_honors_xdg_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.delenv("PLAYBOOK_STORE", raising=False)
    _silence_config(monkeypatch, tmp_path)

    assert procedures_dir() == data / "playbook" / "procedures"
    assert procedures_dir() == resolve_app_paths("playbook").data_dir / "procedures"
    assert not procedures_dir().exists()


def test_playbook_store_overrides_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    custom = tmp_path / "custom" / "procedures"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("PLAYBOOK_STORE", str(custom))

    assert procedures_dir() == custom
    assert not custom.exists()
    assert not data.exists()


def test_tilde_and_env_vars_expand_in_playbook_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PLAYBOOK_STORE", "~/procedures")
    assert procedures_dir() == home / "procedures"

    root = tmp_path / "root"
    monkeypatch.setenv("PLAYBOOK_ROOT", str(root))
    monkeypatch.setenv("PLAYBOOK_STORE", "$PLAYBOOK_ROOT/procedures")
    assert procedures_dir() == root / "procedures"

    monkeypatch.setenv("PLAYBOOK_STORE", "${PLAYBOOK_ROOT}/procedures")
    assert procedures_dir() == root / "procedures"

    monkeypatch.setenv("PLAYBOOK_ROOT", "~/synced")
    monkeypatch.setenv("PLAYBOOK_STORE", "$PLAYBOOK_ROOT/procedures")
    assert procedures_dir() == home / "synced" / "procedures"


def test_blank_env_falls_through_and_a_blank_expansion_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("PLAYBOOK_STORE", "   ")
    assert procedures_dir() == data / "playbook" / "procedures"

    monkeypatch.setenv("EMPTY_STORE", "")
    monkeypatch.setenv("PLAYBOOK_STORE", "$EMPTY_STORE")
    with pytest.raises(ValueError, match="empty"):
        procedures_dir()


def test_config_file_lives_in_the_platform_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("PLAYBOOK_STORE", raising=False)

    assert config_file() == resolve_app_paths("playbook").config_dir / "config.json"
    if sys.platform.startswith("linux"):
        assert config_file() == home / ".config" / "playbook" / "config.json"
    assert procedures_dir() == default_procedures_dir()

    config_file().parent.mkdir(parents=True)
    config_file().write_text(json.dumps({"store": str(tmp_path / "via-home-config")}) + "\n", encoding="utf-8")
    assert procedures_dir() == tmp_path / "via-home-config"


def test_config_sets_the_store_and_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("PLAYBOOK_STORE", raising=False)
    assert procedures_dir() == data / "playbook" / "procedures"

    from_config = tmp_path / "from-config"
    path = config_file()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"store": str(from_config), "ignored": True}) + "\n", encoding="utf-8")
    assert procedures_dir() == from_config

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SYNC_DIR", "synced")
    path.write_text(json.dumps({"store": "~/$SYNC_DIR/procedures"}) + "\n", encoding="utf-8")
    assert procedures_dir() == home / "synced" / "procedures"

    from_env = tmp_path / "from-env"
    monkeypatch.setenv("PLAYBOOK_STORE", str(from_env))
    assert procedures_dir() == from_env

    monkeypatch.setenv("PLAYBOOK_STORE", "  ")
    assert procedures_dir() == home / "synced" / "procedures"


def test_config_without_a_store_is_ignored_and_a_bad_file_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    path = config_file()
    path.parent.mkdir(parents=True)

    path.write_text("{}\n", encoding="utf-8")
    assert procedures_dir() == data / "playbook" / "procedures"

    path.write_text(json.dumps({"store": "  "}) + "\n", encoding="utf-8")
    assert procedures_dir() == data / "playbook" / "procedures"

    path.write_text(json.dumps({"store": None}) + "\n", encoding="utf-8")
    assert procedures_dir() == data / "playbook" / "procedures"

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read playbook config"):
        procedures_dir()

    path.write_text(json.dumps(["nope"]), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        procedures_dir()

    path.write_text(json.dumps({"store": 3}), encoding="utf-8")
    with pytest.raises(ValueError, match="must be a string"):
        procedures_dir()


def test_cli_and_mcp_use_the_store_and_create_it_on_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "missing" / "procedures"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("PLAYBOOK_STORE", str(store))
    assert procedures_dir() == store
    assert not store.exists()

    assert main(["create", "--unsearched", "item", "--title", "Item", "--description", "when", "--tags", "alpha"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["path"] == str(store / "item.json")
    assert (store / "item.json").is_file()
    assert not (tmp_path / "xdg-data").exists()

    opened = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "playbook_open", "arguments": {"id": "item"}},
        }
    )
    assert opened is not None
    body = json.loads(opened["result"]["content"][0]["text"])
    assert opened["result"]["isError"] is False
    assert body["id"] == "item"
    assert body["title"] == "Item"

    written = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "playbook_write",
                "arguments": {
                    "op": "create",
                    "id": "other",
                    "title": "Other",
                    "description": "also",
                    "tags": ["beta"],
                },
            },
        }
    )
    assert written is not None and written["result"]["isError"] is False
    assert (store / "other.json").is_file()
    capsys.readouterr()
    assert main(["load", "other"]) == 0
    loaded = json.loads(capsys.readouterr().out)
    assert loaded["id"] == "other"


def test_config_store_is_what_cli_and_mcp_write_until_env_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configured = tmp_path / "configured"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("PLAYBOOK_STORE", raising=False)
    path = config_file()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"store": str(configured)}) + "\n", encoding="utf-8")

    assert main(["create", "--unsearched", "from-config", "--title", "From config", "--description", "d", "--tags", "t"]) == 0
    capsys.readouterr()
    assert (configured / "from-config.json").is_file()
    assert not (tmp_path / "xdg-data").exists()

    found = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "playbook_search", "arguments": {"query": ""}},
        }
    )
    assert found is not None and found["result"]["isError"] is False
    hits = json.loads(found["result"]["content"][0]["text"])["hits"]
    assert [hit["id"] for hit in hits] == ["from-config"]

    overridden = tmp_path / "overridden"
    monkeypatch.setenv("PLAYBOOK_STORE", str(overridden))
    missing = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "playbook_open", "arguments": {"id": "from-config"}},
        }
    )
    assert missing is not None and missing["result"]["isError"] is True
    assert main(["create", "--unsearched", "from-env", "--title", "From env", "--description", "d", "--tags", "t"]) == 0
    assert (overridden / "from-env.json").is_file()
    assert not (overridden / "from-config.json").exists()


def test_a_bad_config_is_a_json_error_for_the_cli_and_a_tool_error_for_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("PLAYBOOK_STORE", raising=False)
    path = config_file()
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")

    assert main(["search"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "cannot read playbook config" in payload["error"]

    reply = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "playbook_search", "arguments": {"query": "anything"}},
        }
    )
    assert reply is not None
    assert reply["result"]["isError"] is True
    assert "cannot read playbook config" in reply["result"]["content"][0]["text"]
