"""Record-only usage log: redaction, place, machine id, append, and list."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from playbook import ops, usage
from playbook.cli import main
from playbook.mcp import handle_message, serve
from playbook.store import config_file


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "home"
    work = home / "work"
    store = tmp_path / "store"
    log_dir = tmp_path / "usage"
    home.mkdir()
    work.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PLAYBOOK_STORE", str(store))
    monkeypatch.setenv("PLAYBOOK_USAGE", str(log_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for name in (
        "PLAYBOOK_USAGE_OFF",
        "PLAYBOOK_MACHINE",
        "PLAYBOOK_RUN",
        "PLAYBOOK_HARNESS",
        "PLAYBOOK_RUN_KIND",
        "PLAYBOOK_PLACE",
        "PLAYBOOK_DEBUG",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(work)
    usage.new_invocation()
    return {"home": home, "work": work, "store": store, "usage": log_dir, "root": tmp_path}


def _lines(log_dir: Path) -> list[bytes]:
    found: list[bytes] = []
    if not log_dir.is_dir():
        return found
    for path in sorted(log_dir.glob("*.jsonl")):
        for line in path.read_bytes().splitlines(keepends=True):
            if line.strip():
                found.append(line)
    return found


def _events(log_dir: Path) -> list[dict]:
    return [json.loads(line) for line in _lines(log_dir)]


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _plan(walk: str, n: int = 20) -> None:
    Path(walk[:-3] + ".plan.md").write_text(f"1. steps 1-{n}: do them against this piece\n", encoding="utf-8")


def _mcp(name: str, arguments: dict) -> dict:
    response = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None
    return response["result"]


def _mcp_body(name: str, arguments: dict) -> dict:
    result = _mcp(name, arguments)
    assert result["isError"] is False
    return json.loads(result["content"][0]["text"])


def test_redactor_strips_tokens_and_caps_at_200_chars() -> None:
    ghp = "ghp_" + "a" * 36
    gho = "gho_" + "b" * 36
    pat = "github_pat_" + "c" * 22 + "_" + "d" * 20
    sk = "sk-" + "e" * 20
    slack = "xoxb-1234567890-abcdef"
    aws = "AKIAIOSFODNN7EXAMPLE"
    hex_run = "ab" * 16
    short_hex = "a" * 31
    b64 = "ab+/" * 10

    assert usage.redact(f"use {ghp} please") == "use [redacted] please"
    assert "gho_" not in usage.redact(f"oauth {gho}")
    assert "github_pat_" not in usage.redact(pat)
    assert "sk-" not in usage.redact(f"key {sk} end")
    assert "xoxb-" not in usage.redact(slack)
    assert "AKIA" not in usage.redact(f"key {aws} end")
    assert usage.redact(f"id {hex_run}") == "id [redacted]"
    assert usage.redact(short_hex) == short_hex
    assert usage.redact(b64) == "[redacted]"
    almost_b64 = "ab+/" * 9 + "ab+"  # 39 chars, under the base64 floor, and not hex
    assert usage.redact(almost_b64) == almost_b64
    assert usage.redact("password=hunter2 next") == "password=[redacted] next"
    assert usage.redact("token = s3cret") == "token=[redacted]"
    assert usage.redact("Password=hunter2") == "Password=[redacted]"
    plain = "deploy the service and check the logs"
    assert usage.redact(plain) == plain

    # The token starts inside the 200-char window and runs past it. Redacting
    # first removes the whole token; capping first would leave "ghp_" behind.
    straddling = ("note " * 38) + ghp
    capped = usage.redact(straddling)
    assert "ghp_" not in capped
    assert capped.endswith("[redacted]")
    assert len(capped) == 200
    long_text = "word " * 80
    assert usage.redact(long_text) == long_text[:200]
    assert len(usage.redact(long_text)) == 200


def test_place_normalizes_git_origins_and_hides_home(
    isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert usage.normalize_origin("git@github.com:Owner/Repo.git") == "github.com/owner/repo"
    assert usage.normalize_origin("https://github.com/Owner/Repo") == "github.com/owner/repo"
    assert usage.normalize_origin("https://github.com/Owner/Repo.git") == "github.com/owner/repo"
    assert usage.normalize_origin("https://github.com/Owner/Repo/") == "github.com/owner/repo"
    assert usage.normalize_origin("ssh://git@github.com/Owner/Repo.git") == "github.com/owner/repo"
    assert usage.normalize_origin("git://github.com/Owner/Repo.git") == "github.com/owner/repo"
    assert usage.normalize_origin("https://github.com:443/Owner/Repo.git") == "github.com/owner/repo"
    assert usage.normalize_origin("https://gitlab.com/Group/Sub/Repo.git") == "gitlab.com/group/sub/repo"

    home = isolated["home"]
    outside = isolated["root"] / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    usage.log_search("outside", [])
    outside_event = _events(isolated["usage"])[-1]
    assert outside_event["place"] == outside.resolve().as_posix()
    assert "sub" not in outside_event
    assert not outside_event["place"].startswith("/home/")

    monkeypatch.chdir(home)
    usage.log_search("home", [])
    assert _events(isolated["usage"])[-1]["place"] == "~"

    notes = home / "notes"
    notes.mkdir()
    monkeypatch.chdir(notes)
    usage.log_search("notes", [])
    notes_event = _events(isolated["usage"])[-1]
    assert notes_event["place"] == "~/notes"
    assert str(home.resolve()) not in json.dumps(notes_event)

    repo = home / "code" / "proj"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    usage.log_search("no-origin", [])
    assert _events(isolated["usage"])[-1]["place"] == "~/code/proj"

    (repo / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = git@github.com:Owner/Repo.git\n',
        encoding="utf-8",
    )
    nested = repo / "src" / "app"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    usage.log_search("in-repo", [])
    repo_event = _events(isolated["usage"])[-1]
    assert repo_event["place"] == "github.com/owner/repo"
    assert repo_event["sub"] == "src/app"
    assert str(home.resolve()) not in json.dumps(repo_event)
    assert "/home/" not in json.dumps(repo_event)

    monkeypatch.chdir(repo)
    usage.log_search("root", [])
    assert _events(isolated["usage"])[-1]["sub"] == ""

    worktree = home / "wt"
    (worktree / "sub").mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {(repo / '.git').as_posix()}\n", encoding="utf-8")
    monkeypatch.chdir(worktree / "sub")
    usage.log_search("worktree", [])
    wt_event = _events(isolated["usage"])[-1]
    assert wt_event["place"] == "github.com/owner/repo"
    assert wt_event["sub"] == "sub"

    monkeypatch.setenv("PLAYBOOK_PLACE", "grokbot:routine:ai-news")
    usage.log_search("override", [])
    overridden = _events(isolated["usage"])[-1]
    assert overridden["place"] == "grokbot:routine:ai-news"
    assert "sub" not in overridden


def test_machine_id_is_saved_once_per_hostname(
    isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage.socket, "gethostname", lambda: "Build Box")
    usage.log_search("first", [])
    usage.log_search("second", [])
    events = _events(isolated["usage"])
    assert events[0]["machine"] == events[1]["machine"]
    assert events[0]["machine"].startswith("build-box-")
    suffix = events[0]["machine"].rsplit("-", 1)[1]
    assert len(suffix) == 4
    int(suffix, 16)
    saved = json.loads((isolated["usage"] / "machines.json").read_text(encoding="utf-8"))
    assert saved["Build Box"] == events[0]["machine"]
    if os.name == "posix":
        mode = stat.S_IMODE((isolated["usage"] / "machines.json").stat().st_mode)
        assert mode == 0o600

    monkeypatch.setattr(usage.socket, "gethostname", lambda: "Other")
    usage.log_search("third", [])
    third = _events(isolated["usage"])[-1]["machine"]
    assert third != events[0]["machine"]
    saved = json.loads((isolated["usage"] / "machines.json").read_text(encoding="utf-8"))
    assert saved["Build Box"] == events[0]["machine"]
    assert saved["Other"] == third


def test_machine_prefers_env_then_config(isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    path = config_file()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"machine": "bench-9"}) + "\n", encoding="utf-8")
    monkeypatch.setenv("PLAYBOOK_MACHINE", "from-env")
    usage.log_search("env", [])
    assert _events(isolated["usage"])[-1]["machine"] == "from-env"
    assert not (isolated["usage"] / "machines.json").exists()

    monkeypatch.delenv("PLAYBOOK_MACHINE")
    usage.log_search("config", [])
    by_query = {event["query"]: event["machine"] for event in _events(isolated["usage"])}
    assert by_query == {"env": "from-env", "config": "bench-9"}
    assert not (isolated["usage"] / "machines.json").exists()


def test_append_is_one_private_json_line_under_4kb(
    isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAYBOOK_MACHINE", "box")
    monkeypatch.setenv("PLAYBOOK_HARNESS", "cursor")
    monkeypatch.setenv("PLAYBOOK_RUN_KIND", "gym")
    monkeypatch.setenv("PLAYBOOK_RUN", "run-fixed")
    writes: list[bytes] = []
    real_write = os.write

    def spy(fd: int, data: bytes) -> int:
        if isinstance(data, bytes) and b'"ev":' in data:
            writes.append(data)
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", spy)
    usage.log_search("word " * 80, ["a", "b", "c", "d", "e", "f"])
    usage.log_write("c" * 20000, "create")

    assert len(writes) == 2
    raw_lines = _lines(isolated["usage"])
    assert raw_lines == writes
    for line in raw_lines:
        assert len(line) < 4096
        assert line.count(b"\n") == 1
        json.loads(line)
    path = isolated["usage"] / "box.jsonl"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    search, write = _events(isolated["usage"])
    assert search["v"] == 1
    assert search["run"] == "run-fixed"
    assert search["kind"] == "gym"
    assert search["harness"] == "cursor"
    assert search["ev"] == "search"
    assert len(search["query"]) == 200
    assert search["hits"] == ["a", "b", "c", "d", "e"]
    assert search["ts"].endswith("Z") and "T" in search["ts"]
    assert write["ev"] == "write"
    assert write["op"] == "create"
    assert write["walk"] is False
    assert write["card"].startswith("c")
    assert len(write["card"]) < 20000

    monkeypatch.delenv("PLAYBOOK_HARNESS")
    monkeypatch.setenv("PLAYBOOK_RUN_KIND", "real")
    usage.log_opened("demo")
    opened = _events(isolated["usage"])[-1]
    assert opened["kind"] == "real"
    assert "harness" not in opened


def test_usage_off_writes_nothing(isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PLAYBOOK_USAGE_OFF", "1")
    monkeypatch.setenv("PLAYBOOK_DEBUG", "1")
    code, out, err = _run(["search", "anything"], capsys)
    assert code == 0
    assert json.loads(out)["ok"] is True
    assert err == ""
    assert not isolated["usage"].exists()

    monkeypatch.delenv("PLAYBOOK_USAGE_OFF")
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"usage": False, "store": str(isolated["store"])}) + "\n", encoding="utf-8")
    code, _, err = _run(
        ["create", "--unsearched", "item", "--title", "Item", "--description", "when", "--tags", "alpha"],
        capsys,
    )
    assert code == 0
    assert err == ""
    assert not isolated["usage"].exists()

    path.write_text("{not json", encoding="utf-8")
    code, out, err = _run(["search"], capsys)
    assert code == 0
    assert json.loads(out)["ok"] is True
    assert "playbook usage" not in out
    assert "playbook usage" in err
    assert not isolated["usage"].exists()


def test_failed_command_is_not_logged(isolated: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(
        ["create", "gated", "--title", "G", "--description", "d", "--tags", "a"],
        capsys,
    )
    assert code == 1
    assert json.loads(out)["ok"] is False
    assert err == ""
    assert _events(isolated["usage"]) == []

    failed = _mcp("playbook_open", {"id": "missing"})
    assert failed["isError"] is True
    assert _events(isolated["usage"]) == []


def test_cli_logs_search_open_walk_tick_and_write(
    isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PLAYBOOK_MACHINE", "cli-box")
    steps = json.dumps(
        [
            {"title": "One", "do": "Do one."},
            {"title": "Two", "do": "Do two."},
        ]
    )
    commands = [
        ["create", "--unsearched", "demo", "--title", "Demo", "--description", "when to pick this", "--tags", "demo", "--steps", steps],
        ["edit", "demo", "--title", "Demo procedure"],
        ["add-step", "demo", "--title", "Three", "--do", "Do three."],
        ["add-steps", "demo", "--steps", json.dumps([{"title": "Four", "do": "Do four."}])],
        ["edit-step", "demo", "--title", "Three", "--do", "Do three now."],
        ["move-step", "demo", "--title", "Four", "--to", "1"],
        ["remove-step", "demo", "--title", "Four"],
    ]
    for argv in commands:
        code, out, err = _run(argv, capsys)
        assert err == ""
        assert code == 0, out

    code, out, err = _run(["open", "demo", "--for", "landing", "--dir", str(isolated["work"])], capsys)
    assert code == 0, out
    assert err == ""
    walk = json.loads(out)["path"]
    _plan(walk)
    code, out, err = _run(
        ["edit-step", "--walk", walk, "--step", "1", "--do", "Updated one."],
        capsys,
    )
    assert code == 0, out
    assert err == ""
    code, out, err = _run(
        ["add-step", "--walk", walk, "--after", "1", "--title", "Inserted", "--do", "Do inserted."],
        capsys,
    )
    assert code == 0, out
    assert err == ""
    code, out, err = _run(
        ["tick", walk, "--done", "1", "--note", "saw it", "--skip", "2", "--because", "not this piece", "--dir", str(isolated["work"])],
        capsys,
    )
    assert code == 0, out
    assert err == ""

    for argv in (["load", "demo"], ["show", "demo"], ["get", "demo"]):
        code, out, err = _run(argv, capsys)
        assert code == 0, out
        assert err == ""
        assert json.loads(out)["id"] == "demo"

    secret = "password=hunter2"
    code, out, err = _run(["search", secret, "demo"], capsys)
    assert code == 0
    assert err == ""
    assert secret in json.loads(out)["query"]

    code, _, err = _run(["delete", "demo"], capsys)
    assert code == 0
    assert err == ""

    events = _events(isolated["usage"])
    kinds = [event["ev"] for event in events]
    assert "search" in kinds
    assert "opened" in kinds
    assert "walked" in kinds
    assert "tick" in kinds
    assert "write" in kinds

    writes = [(event["op"], event["walk"], event["card"]) for event in events if event["ev"] == "write"]
    assert ("create", False, "demo") in writes
    assert ("edit", False, "demo") in writes
    assert ("add-step", False, "demo") in writes
    assert ("edit-step", False, "demo") in writes
    assert ("remove-step", False, "demo") in writes
    assert ("move-step", False, "demo") in writes
    assert ("delete", False, "demo") in writes
    assert ("edit-step", True, "demo") in writes
    assert ("add-step", True, "demo") in writes
    assert sum(1 for op, via, _card in writes if op == "add-step" and not via) == 2

    opened = [event for event in events if event["ev"] == "opened"]
    assert len(opened) == 3
    assert {event["card"] for event in opened} == {"demo"}

    walked = [event for event in events if event["ev"] == "walked"]
    assert len(walked) == 1
    assert walked[0]["card"] == "demo"
    assert walked[0]["purpose"] == "landing"

    ticks = [event for event in events if event["ev"] == "tick"]
    assert len(ticks) == 1
    assert ticks[0]["card"] == "demo"
    assert ticks[0]["done"] == [1]
    assert ticks[0]["skipped"] == [{"n": 2, "because": "not this piece"}]
    assert ticks[0]["note"] == "saw it"

    search = [event for event in events if event["ev"] == "search"][-1]
    assert secret not in json.dumps(search)
    assert "password=[redacted]" in search["query"]
    assert "demo" in search["hits"]


def test_mcp_logs_events_without_changing_tools_or_output(
    isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PLAYBOOK_MACHINE", "mcp-box")
    monkeypatch.setenv("PLAYBOOK_RUN", "mcp-run")
    listed = handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    tools = listed["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["playbook_search", "playbook_open", "playbook_write"]
    assert set(tools[0]["inputSchema"]["properties"]) == {"query", "limit"}
    assert "required" not in tools[0]["inputSchema"]
    assert set(tools[1]["inputSchema"]["properties"]) == {"id", "at", "full", "upstream"}
    assert tools[1]["inputSchema"]["required"] == ["id"]
    assert tools[2]["inputSchema"]["properties"]["op"]["enum"] == ["create", "append", "edit", "remove", "meta"]
    assert tools[2]["inputSchema"]["required"] == ["id", "op"]
    assert "could not derive" in tools[2]["description"]

    created = _mcp_body(
        "playbook_write",
        {
            "op": "create",
            "id": "item",
            "title": "Item",
            "description": "choose a mode",
            "tags": ["alpha"],
            "steps": [{"title": "Ask", "do": "Set the mode."}],
        },
    )
    assert set(created) == {"ok", "id", "path", "steps"}
    assert created["id"] == "item"
    direct_open = ops.load_procedure("item")
    opened = _mcp_body("playbook_open", {"id": "item"})
    assert opened == direct_open
    assert "ev" not in opened

    secret = "github_pat_" + "c" * 22
    direct_search = ops.search_procedures(secret + " mode", limit=5)
    found = _mcp_body("playbook_search", {"query": secret + " mode", "limit": 5})
    assert found == direct_search
    assert secret in found["query"]

    appended = _mcp_body(
        "playbook_write",
        {"op": "append", "id": "item", "steps": [{"title": "Next", "do": "Do next."}]},
    )
    assert set(appended) == {"ok", "id", "added", "steps"}
    edited = _mcp_body(
        "playbook_write",
        {"op": "edit", "id": "item", "step": "Ask", "do": "Set the mode carefully."},
    )
    assert set(edited) == {"ok", "id", "step_id", "title", "do"}
    removed = _mcp_body("playbook_write", {"op": "remove", "id": "item", "step": "Next"})
    assert set(removed) == {"ok", "id", "removed", "steps"}
    meta = _mcp_body("playbook_write", {"op": "meta", "id": "item", "title": "Renamed"})
    assert set(meta) == {"ok", "id", "title", "description", "tags"}
    duplicate = _mcp(
        "playbook_write",
        {"op": "create", "id": "item", "title": "Item", "description": "again", "tags": ["alpha"]},
    )
    assert duplicate["isError"] is True

    events = _events(isolated["usage"])
    assert {event["run"] for event in events} == {"mcp-run"}
    assert [event["ev"] for event in events if event["ev"] != "write"] == ["opened", "search"]
    write_ops = [event["op"] for event in events if event["ev"] == "write"]
    assert write_ops == ["create", "add-step", "edit-step", "remove-step", "edit"]
    assert all(event["walk"] is False for event in events if event["ev"] == "write")
    assert all(event["card"] == "item" for event in events if event["ev"] in {"write", "opened"})
    search = next(event for event in events if event["ev"] == "search")
    assert secret not in json.dumps(search)
    assert "[redacted]" in search["query"]
    assert "mode" in search["query"]
    assert search["hits"] == ["item"]

    monkeypatch.delenv("PLAYBOOK_RUN")
    incoming = "\n".join(
        json.dumps(message)
        for message in (
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "playbook_search", "arguments": {"query": "mode"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "playbook_open", "arguments": {"id": "item"}},
            },
        )
    ) + "\n"
    import io

    stdout = io.StringIO()
    serve(io.StringIO(incoming), stdout)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    replies = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert [reply["id"] for reply in replies] == [1, 2, 3]
    assert all("jsonrpc" in reply for reply in replies)
    assert "playbook usage" not in stdout.getvalue()
    names = [tool["name"] for tool in replies[0]["result"]["tools"]]
    assert names == ["playbook_search", "playbook_open", "playbook_write"]
    fresh = [event for event in _events(isolated["usage"]) if event["run"] != "mcp-run"]
    assert len(fresh) == 2
    assert fresh[0]["run"] == fresh[1]["run"]
    assert fresh[0]["run"].startswith("r-")
    assert {event["ev"] for event in fresh} == {"search", "opened"}


def test_unwritable_usage_dir_does_not_break_commands(
    isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = isolated["root"] / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("PLAYBOOK_USAGE", str(blocker / "usage"))
    code, out, err = _run(["search", "deploy"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert "hits" in payload
    assert err == ""
    assert "playbook usage" not in out

    monkeypatch.setenv("PLAYBOOK_DEBUG", "1")
    code, out, err = _run(["search", "deploy"], capsys)
    assert code == 0
    assert json.loads(out)["ok"] is True
    assert "playbook usage" in err
    assert "playbook usage" not in out

    import io

    stdout = io.StringIO()
    message = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "playbook_search", "arguments": {"query": "deploy"}},
        }
    )
    serve(io.StringIO(message + "\n"), stdout)
    captured = capsys.readouterr()
    text = stdout.getvalue()
    reply = json.loads(text)
    assert reply["id"] == 7
    assert reply["result"]["isError"] is False
    body = json.loads(reply["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert "playbook usage" not in text
    assert "playbook usage" in captured.err


def test_search_and_validate_ignore_the_usage_directory(
    isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PLAYBOOK_USAGE")
    code, out, err = _run(
        ["create", "--unsearched", "real", "--title", "Real", "--description", "the real card", "--tags", "alpha",
         "--steps", json.dumps([{"title": "Only", "do": "Do the real step."}])],
        capsys,
    )
    assert code == 0, out
    assert err == ""
    hidden = isolated["store"] / ".usage"
    assert hidden.is_dir()
    shadow = {
        "id": "real",
        "title": "SHADOW",
        "description": "should not be indexed",
        "tags": ["secret"],
        "steps": [{"id": "a" * 32, "title": "Nope", "do": "Do not index."}],
    }
    evil = {
        "id": "evil-card",
        "title": "Evil",
        "description": "should not be indexed",
        "tags": ["secret"],
        "steps": [{"id": "b" * 32, "title": "Nope", "do": "Do not index."}],
    }
    (hidden / "real.json").write_text(json.dumps(shadow), encoding="utf-8")
    (hidden / "evil-card.json").write_text(json.dumps(evil), encoding="utf-8")

    code, out, err = _run(["search"], capsys)
    assert code == 0
    assert err == ""
    listed = json.loads(out)
    assert {hit["id"] for hit in listed["hits"]} == {"real"}
    assert listed["hits"][0]["title"] == "Real"

    code, out, err = _run(["load", "real"], capsys)
    assert code == 0
    assert json.loads(out)["title"] == "Real"

    code, out, err = _run(["validate", "real"], capsys)
    assert code == 0
    assert json.loads(out)["valid"] is True

    code, out, err = _run(["validate", "evil-card"], capsys)
    assert code != 0
    body = json.loads(out)
    assert body.get("valid") is not True
    assert "should not be indexed" not in out


def test_usage_list_reads_every_log_newest_first(
    isolated: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(["usage", "list"], capsys)
    assert code == 0
    assert err == ""
    assert "ts" in out.splitlines()[0]
    assert not isolated["usage"].exists()

    isolated["usage"].mkdir()
    older = isolated["usage"] / "old-box.jsonl"
    newer = isolated["usage"] / "new-box.jsonl"
    older.write_text(
        "\n".join(
            (
                "not json",
                "",
                "[]",
                json.dumps({"v": 1, "ts": "2026-01-01T00:00:00Z", "ev": "opened", "card": "early", "machine": "old-box", "place": "~/notes"}),
                json.dumps({"v": 1, "ts": "2026-03-01T00:00:00Z", "ev": "write", "card": "early", "op": "edit", "machine": "old-box", "place": "~/notes"}),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    newer.write_text(
        json.dumps({"v": 1, "ts": "2026-06-01T00:00:00Z", "ev": "search", "query": "later", "hits": ["early"], "machine": "new-box", "place": "github.com/owner/repo"})
        + "\n",
        encoding="utf-8",
    )

    code, out, err = _run(["usage", "list"], capsys)
    assert code == 0 and err == ""
    rows = [line for line in out.splitlines() if not line.startswith("ts ")]
    assert len(rows) == 3
    assert rows[0].split()[0] == "2026-06-01T00:00:00Z"
    assert "search" in rows[0] and "early" in rows[0]
    assert rows[-1].split()[0] == "2026-01-01T00:00:00Z"

    code, out, err = _run(["usage", "list", "--json", "--card", "early", "--ev", "write"], capsys)
    assert code == 0 and err == ""
    parsed = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert len(parsed) == 1
    assert parsed[0]["op"] == "edit"
    assert " " not in out.strip()

    code, out, err = _run(["usage", "list", "--json", "--machine", "new-box", "--since", "2026-05-01", "--limit", "1"], capsys)
    assert code == 0
    parsed = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert len(parsed) == 1
    assert parsed[0]["ev"] == "search"

    code, out, err = _run(["usage", "list", "--since", "yesterday"], capsys)
    assert code == 1
    assert json.loads(out)["ok"] is False


def test_store_gitignore_gains_a_usage_line(isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLAYBOOK_USAGE")
    gitignore = isolated["store"] / ".gitignore"
    gitignore.parent.mkdir(parents=True)
    gitignore.write_text("*.tmp", encoding="utf-8")
    usage.log_search("first", [])
    text = gitignore.read_text(encoding="utf-8")
    assert text.splitlines() == ["*.tmp", ".usage/"]
    usage.log_search("second", [])
    assert gitignore.read_text(encoding="utf-8").splitlines() == ["*.tmp", ".usage/"]
    assert (isolated["store"] / ".usage").is_dir()

    other = isolated["root"] / "elsewhere"
    monkeypatch.setenv("PLAYBOOK_USAGE", str(other))
    bare = isolated["root"] / "bare-store"
    monkeypatch.setenv("PLAYBOOK_STORE", str(bare))
    usage.log_search("third", [])
    assert not (bare / ".gitignore").exists()
    assert (other / ".gitignore").exists() is False


def test_run_id_is_per_cli_invocation_and_per_mcp_process(
    isolated: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PLAYBOOK_MACHINE", "box")
    issued = iter(("aaaaaa", "bbbbbb", "cccccc", "dddddd"))
    monkeypatch.setattr(usage.secrets, "token_hex", lambda _n: next(issued))

    assert _run(["search", "one"], capsys)[0] == 0
    assert _run(["search", "two"], capsys)[0] == 0
    runs = [event["run"] for event in _events(isolated["usage"])]
    assert runs == ["r-aaaaaa", "r-bbbbbb"]

    monkeypatch.setenv("PLAYBOOK_RUN", "held")
    assert _run(["search", "three"], capsys)[0] == 0
    assert _run(["search", "four"], capsys)[0] == 0
    assert [event["run"] for event in _events(isolated["usage"])][-2:] == ["held", "held"]


def test_tick_note_and_purpose_are_redacted(isolated: dict[str, Path]) -> None:
    token = "sk-" + "f" * 24
    usage.log_walked("demo", f"landing {token}")
    usage.log_tick("demo", [1], [{"n": 2, "because": "token=abc123"}], note=token)
    walked, tick = _events(isolated["usage"])[-2:]
    assert token not in json.dumps(walked)
    assert "[redacted]" in walked["purpose"]
    assert tick["note"] == "[redacted]"
    assert tick["skipped"] == [{"n": 2, "because": "token=[redacted]"}]
    assert "abc123" not in json.dumps(tick)
