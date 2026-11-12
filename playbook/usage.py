"""Append-only record of searches, opens, walks, ticks, and writes.

The log is bookkeeping. It never changes what a command returns, and a
failure while writing it is swallowed so the command still succeeds.

One JSON object per line, under ``<store>/.usage/<machine>.jsonl``
(or ``PLAYBOOK_USAGE``). Lines stay under 4 KB so a single ``O_APPEND``
write is atomic on the platforms that guarantee that for writes below
``PIPE_BUF``.
"""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from playbook import store

ENV_USAGE = "PLAYBOOK_USAGE"
ENV_OFF = "PLAYBOOK_USAGE_OFF"
ENV_MACHINE = "PLAYBOOK_MACHINE"
ENV_RUN = "PLAYBOOK_RUN"
ENV_HARNESS = "PLAYBOOK_HARNESS"
ENV_KIND = "PLAYBOOK_RUN_KIND"
ENV_PLACE = "PLAYBOOK_PLACE"
ENV_DEBUG = "PLAYBOOK_DEBUG"

MAX_LINE_BYTES = 4096
TEXT_CAP = 200
HIT_CAP = 5

# Run id for this process invocation. PLAYBOOK_RUN wins and is not cached.
_run_id: str | None = None

_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"gho_[A-Za-z0-9]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"xox[abpr]-[A-Za-z0-9\-]{8,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
)
# Key names whose value is treated as a secret in "name=value" text. The
# name may carry a prefix, so client_secret=, GITHUB_TOKEN= and db-password=
# all match. Quoted values are removed whole.
_SECRET_KEY_NAMES = (
    r"password|passwd|pwd|passphrase|secret|token|api[_-]?key|key"
    r"|auth|credentials?|session|cookie"
)
_ASSIGN_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.-]*(?:" + _SECRET_KEY_NAMES + r"))"
    r"\s*=\s*(?:\"[^\"]*\"|'[^']*'|\S+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_HEX_PATTERN = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{32,}(?![A-Za-z0-9])")
_B64_PATTERN = re.compile(
    r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/_-]{40,}={0,2}(?![A-Za-z0-9+/_-])"
)
_SINCE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")
_WRITE_OPS = {
    "create": "create",
    "edit": "edit",
    "add-step": "add-step",
    "add-steps": "add-step",
    "edit-step": "edit-step",
    "remove-step": "remove-step",
    "move-step": "move-step",
    "delete": "delete",
}


def new_invocation() -> None:
    """Drop the cached run id so the next record mints a new one.

    The CLI calls this once per process invocation. The MCP server calls
    it once at startup and then reuses that id for every tool call.
    ``PLAYBOOK_RUN`` still overrides whatever was minted.
    """
    global _run_id
    _run_id = None


def run_id() -> str:
    forced = os.environ.get(ENV_RUN, "").strip()
    if forced:
        return forced
    global _run_id
    if not _run_id:
        _run_id = "r-" + secrets.token_hex(3)
    return _run_id


def redact(text: str, limit: int = TEXT_CAP) -> str:
    """Replace token-shaped secrets, then cap the length.

    Redaction happens before the cap so a token that would have been cut
    in half is still removed whole.
    """
    cleaned = str(text)
    for pattern in _TOKEN_PATTERNS:
        cleaned = pattern.sub("[redacted]", cleaned)

    def _assign(match: re.Match[str]) -> str:
        return f"{match.group(1)}=[redacted]"

    cleaned = _ASSIGN_PATTERN.sub(_assign, cleaned)
    cleaned = _BEARER_PATTERN.sub(lambda m: f"{m.group(1)} [redacted]", cleaned)
    cleaned = _HEX_PATTERN.sub("[redacted]", cleaned)
    cleaned = _B64_PATTERN.sub("[redacted]", cleaned)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit]
    return cleaned


def normalize_origin(url: str) -> str:
    """Turn a git remote URL into ``host/owner/repo``.

    ``git@github.com:Owner/Repo.git`` and ``https://github.com/Owner/Repo``
    both become ``github.com/owner/repo``.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        rest = raw.split("://", 1)[1]
        host_part, _, path = rest.partition("/")
        if "@" in host_part:
            host_part = host_part.split("@", 1)[1]
        host = host_part.split(":", 1)[0]
    else:
        host, sep, path = raw.partition(":")
        if not sep:
            host, path = "", raw
        if "@" in host:
            host = host.split("@", 1)[1]
    path = path.strip().strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")].strip("/")
    joined = "/".join(part for part in (host.strip(), path) if part)
    return joined.lower()


def usage_dir() -> Path:
    """Directory that holds ``<machine>.jsonl``. Does not create it."""
    env = os.environ.get(ENV_USAGE)
    if env is not None and env.strip():
        return store.expand_store_path(env)
    return store.procedures_dir() / ".usage"


def enabled() -> bool:
    """False when logging is switched off.

    A config file that cannot be read counts as off. The off switch is
    privacy-sensitive, and a command must still succeed either way.
    """
    if os.environ.get(ENV_OFF, "").strip() == "1":
        return False
    try:
        data = store.load_config()
    except Exception as exc:
        _warn(exc)
        return False
    return not (isinstance(data, dict) and data.get("usage") is False)


def machine_id() -> str:
    """``PLAYBOOK_MACHINE``, else config ``machine``, else a saved slug."""
    forced = os.environ.get(ENV_MACHINE, "").strip()
    if forced:
        return forced
    configured = _configured_machine()
    if configured:
        return configured
    _ensure_dir()
    return _persistent_machine()


def record(ev: str, **fields: Any) -> None:
    """Append one event. Never raises."""
    try:
        _record(ev, fields)
    except Exception as exc:
        _warn(exc)


def log_search(query: str, hits: list[Any]) -> None:
    try:
        ids: list[str] = []
        for hit in hits or []:
            if isinstance(hit, str) and hit:
                ids.append(hit)
            elif isinstance(hit, dict) and isinstance(hit.get("id"), str) and hit["id"]:
                ids.append(hit["id"])
            if len(ids) >= HIT_CAP:
                break
        record("search", query=str(query or ""), hits=ids)
    except Exception as exc:
        _warn(exc)


def log_opened(card: str) -> None:
    try:
        if card:
            record("opened", card=str(card))
    except Exception as exc:
        _warn(exc)


def log_walked(card: str, purpose: str) -> None:
    try:
        record("walked", card=str(card or ""), purpose=str(purpose or ""))
    except Exception as exc:
        _warn(exc)


def log_tick(card: str, done: list[int], skipped: list[dict[str, Any]], note: str = "") -> None:
    try:
        fields: dict[str, Any] = {
            "card": str(card or ""),
            "done": [int(number) for number in done],
            "skipped": [
                {"n": int(item["n"]), "because": str(item.get("because", ""))}
                for item in skipped
                if isinstance(item, dict) and "n" in item
            ],
        }
        if str(note or "").strip():
            fields["note"] = str(note)
        record("tick", **fields)
    except Exception as exc:
        _warn(exc)


def log_write(card: str, op: str, walk: bool = False) -> None:
    try:
        record("write", card=str(card or ""), op=str(op), walk=bool(walk))
    except Exception as exc:
        _warn(exc)


def note_cli(args: Any, payload: Any) -> None:
    """Record a successful CLI invocation. Never raises."""
    try:
        command = str(getattr(args, "command", "") or "")
        if command == "search":
            query = " ".join(getattr(args, "query", None) or [])
            log_search(query, _hit_ids(payload))
        elif command in {"load", "show", "get"}:
            log_opened(str(getattr(args, "id", "") or ""))
        elif command == "open":
            log_walked(str(getattr(args, "id", "") or ""), str(getattr(args, "purpose", "") or ""))
        elif command == "tick":
            _note_tick(args, payload)
        elif command in _WRITE_OPS:
            _note_write(args, payload, _WRITE_OPS[command])
    except Exception as exc:
        _warn(exc)


def list_events(
    card: str | None = None,
    machine: str | None = None,
    ev: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Every well-formed event in the usage directory, newest first.

    Malformed lines are skipped. ``since`` is a ``YYYY-MM-DD`` date or an
    ISO timestamp compared lexicographically with the UTC ``Z`` timestamps.
    """
    if since:
        since = since.strip()
        if since and not _SINCE_PATTERN.match(since):
            raise ValueError("--since must be a date (YYYY-MM-DD) or an ISO timestamp")
    events = _read_all()
    if card:
        events = [item for item in events if item.get("card") == card]
    if machine:
        events = [item for item in events if item.get("machine") == machine]
    if ev:
        events = [item for item in events if item.get("ev") == ev]
    if since:
        events = [item for item in events if str(item.get("ts", "")) >= since]
    # Read order is append order. A later line in the same second stays newer.
    indexed = list(enumerate(events))
    indexed.sort(key=lambda pair: (str(pair[1].get("ts", "")), pair[0]), reverse=True)
    events = [item for _, item in indexed]
    if isinstance(limit, int) and limit > 0:
        events = events[:limit]
    return events


def format_table(events: list[dict[str, Any]]) -> str:
    """A compact text table. The JSON lines mode prints the records as stored."""
    header = (
        f"{'ts':<22} {'ev':<8} {'card':<24} {'detail':<42} {'machine':<18} place"
    )
    rows = [header]
    for event in events:
        rows.append(
            " ".join(
                (
                    _cell(event.get("ts", ""), 22),
                    _cell(event.get("ev", ""), 8),
                    _cell(event.get("card", ""), 24),
                    _cell(_detail(event), 42),
                    _cell(event.get("machine", ""), 18),
                    _cell(event.get("place", "")),
                )
            )
        )
    return "\n".join(rows)


def _record(ev: str, fields: dict[str, Any]) -> None:
    if not enabled():
        return
    _ensure_dir()
    event: dict[str, Any] = {
        "v": 1,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": run_id(),
        "machine": machine_id(),
    }
    harness = os.environ.get(ENV_HARNESS, "").strip()
    if harness:
        event["harness"] = harness
    kind = os.environ.get(ENV_KIND, "").strip()
    event["kind"] = "gym" if kind == "gym" else "real"
    place, sub = locate()
    event["place"] = place
    if sub is not None:
        event["sub"] = sub
    event["ev"] = ev
    event.update(_prepare_fields(fields))
    line = _fit(event)
    _append_line(usage_dir() / _log_name(str(event["machine"])), line)


def _prepare_fields(fields: dict[str, Any]) -> dict[str, Any]:
    prepared: dict[str, Any] = {}
    for key, value in fields.items():
        if key in {"query", "purpose", "note"} and value is not None:
            prepared[key] = redact(str(value))
        elif key == "hits":
            prepared[key] = [str(item) for item in (value or [])][:HIT_CAP]
        elif key == "skipped" and isinstance(value, list):
            prepared[key] = [_redact_skip(item) for item in value if isinstance(item, dict)]
        else:
            prepared[key] = value
    return prepared


def _redact_skip(item: dict[str, Any]) -> dict[str, Any]:
    skipped = dict(item)
    if "because" in skipped:
        skipped["because"] = redact(str(skipped.get("because", "")))
    return skipped


def locate() -> tuple[str, str | None]:
    """``(place, sub)``. ``sub`` is None when it should be left off the line.

    ``PLAYBOOK_PLACE`` is a label and replaces both. Inside a git checkout
    with an origin, place is the normalized remote and sub is the path
    inside the repo (empty at the root). Anywhere else, place is relative
    to the home directory when the working directory lives under it.
    """
    override = os.environ.get(ENV_PLACE, "").strip()
    if override:
        return override, None
    try:
        cwd = Path.cwd()
    except OSError:
        return "~", None
    try:
        found = _git_place(cwd)
    except OSError:
        found = None
    if found is not None:
        return found
    return _home_place(cwd), None


def _git_place(start: Path) -> tuple[str, str] | None:
    found = _find_git(start)
    if found is None:
        return None
    root, config = found
    origin = _origin_url(config)
    if not origin:
        return None
    place = normalize_origin(origin)
    if not place:
        return None
    return place, _relative_sub(start, root)


def _find_git(start: Path) -> tuple[Path, Path] | None:
    current = start.resolve()
    for directory in (current, *current.parents):
        git_path = directory / ".git"
        if git_path.is_dir():
            config = git_path / "config"
            if config.is_file():
                return directory, config
        elif git_path.is_file():
            config = _config_from_gitfile(git_path)
            if config is not None and config.is_file():
                return directory, config
        if directory.parent == directory:
            break
    return None


def _config_from_gitfile(git_file: Path) -> Path | None:
    text = git_file.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("gitdir:"):
            raw = stripped.split(":", 1)[1].strip()
            gitdir = Path(raw)
            if not gitdir.is_absolute():
                gitdir = (git_file.parent / gitdir).resolve()
            return gitdir / "config"
    return None


def _origin_url(config: Path) -> str:
    text = config.read_text(encoding="utf-8", errors="replace")
    in_origin = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            header = stripped[1:-1].strip().replace("'", '"').lower()
            in_origin = header == 'remote "origin"'
            continue
        if in_origin and stripped.lower().startswith("url") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _relative_sub(start: Path, root: Path) -> str:
    try:
        relative = start.resolve().relative_to(root.resolve())
    except ValueError:
        return ""
    text = relative.as_posix()
    return "" if text == "." else text


def _home_place(path: Path) -> str:
    """A ``~/...`` path when ``path`` is under the home directory.

    An absolute ``/home/<user>`` path is never returned. A directory outside
    the home directory keeps its absolute path; that is not a home path.
    """
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    try:
        home = Path.home().resolve()
    except OSError:
        return resolved.as_posix()
    text = resolved.as_posix()
    home_text = home.as_posix()
    if text == home_text:
        return "~"
    prefix = home_text + "/"
    if text.startswith(prefix):
        rest = text[len(prefix):]
        return "~/" + rest if rest else "~"
    return text


def _configured_machine() -> str | None:
    data = store.load_config()
    if not isinstance(data, dict):
        return None
    machine = data.get("machine")
    if isinstance(machine, str) and machine.strip():
        return machine.strip()
    return None


def _persistent_machine() -> str:
    host = socket.gethostname().strip() or "localhost"
    path = usage_dir() / "machines.json"
    data: dict[str, Any] = {}
    if path.is_file():
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError(f"{path} is not a JSON object")
        data = parsed
        current = data.get(host)
        if isinstance(current, str) and current.strip():
            return current.strip()
    ident = f"{_slug(host)}-{secrets.token_hex(2)}"
    data[host] = ident
    _write_private_json(path, data)
    return ident


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "host"


def _log_name(machine: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", machine).strip(".-") or "machine"
    return safe[:80] + ".jsonl"


def _ensure_dir() -> None:
    directory = usage_dir()
    created = not directory.exists()
    directory.mkdir(parents=True, exist_ok=True)
    if not created:
        return
    try:
        os.chmod(directory, 0o700)
    except OSError as exc:
        _warn(exc)
    try:
        _gitignore_usage(directory)
    except Exception as exc:
        _warn(exc)


def _gitignore_usage(directory: Path) -> None:
    """Append ``.usage/`` when this directory is the store's and a gitignore exists.

    The line is added only when Playbook creates ``.usage``. A store with no
    ``.gitignore`` is left alone; Playbook does not start one.
    """
    if directory.name != ".usage":
        return
    store_root = store.procedures_dir()
    if directory.resolve() != (store_root / ".usage").resolve():
        return
    gitignore = store_root / ".gitignore"
    if not gitignore.is_file():
        return
    text = gitignore.read_text(encoding="utf-8")
    existing = {line.strip() for line in text.splitlines()}
    if ".usage/" in existing or ".usage" in existing:
        return
    prefix = "" if text.endswith("\n") or text == "" else "\n"
    with gitignore.open("a", encoding="utf-8") as handle:
        handle.write(f"{prefix}.usage/\n")


def _append_line(path: Path, line: bytes) -> None:
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(path, flags, 0o600)
    try:
        _force_private(fd)
        os.write(fd, line)
    finally:
        os.close(fd)


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(temporary, flags, 0o600)
    try:
        _force_private(fd)
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.replace(temporary, path)


def _force_private(fd: int) -> None:
    """Ask for mode 0600 even when the process umask would widen it.

    ``fchmod`` is Unix. Where it is missing, the ``os.open`` mode is the
    best the platform can do, and a failure here must not drop the line.
    """
    force = getattr(os, "fchmod", None)
    if force is None:
        return
    try:
        force(fd, 0o600)
    except OSError as exc:
        _warn(exc)


def _dumps(event: dict[str, Any]) -> bytes:
    text = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def _fit(event: dict[str, Any]) -> bytes:
    """Keep the encoded line strictly under 4 KB."""
    current = copy.deepcopy(event)
    data = _dumps(current)
    for _ in range(1000):
        if len(data) < MAX_LINE_BYTES:
            return data
        if not _shorten_one(current):
            break
        data = _dumps(current)
    if len(data) < MAX_LINE_BYTES:
        return data
    keep: dict[str, Any] = {}
    for key in ("v", "ts", "run", "machine", "kind", "ev", "card", "op", "place"):
        if key not in current:
            continue
        value = current[key]
        if isinstance(value, str) and len(value) > 80:
            value = value[:80]
        keep[key] = value
    data = _dumps(keep)
    if len(data) < MAX_LINE_BYTES:
        return data
    tiny = {
        "v": 1,
        "ts": str(event.get("ts", ""))[:32],
        "ev": str(event.get("ev", ""))[:32],
    }
    return _dumps(tiny)


def _shorten_one(node: Any) -> bool:
    holder: Any = None
    key: Any = None
    longest = 0
    for parent, slot in _string_slots(node):
        text = parent[slot]
        if isinstance(text, str) and len(text) > longest:
            longest = len(text)
            holder = parent
            key = slot
    if holder is not None and longest:
        holder[key] = holder[key][: longest // 2]
        return True
    return _drop_list_item(node)


def _string_slots(node: Any) -> Iterator[tuple[Any, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                yield node, key
            elif isinstance(value, (dict, list)):
                yield from _string_slots(value)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str):
                yield node, index
            elif isinstance(value, (dict, list)):
                yield from _string_slots(value)


def _drop_list_item(node: Any) -> bool:
    if isinstance(node, dict):
        for value in node.values():
            if isinstance(value, list) and value:
                value.pop()
                return True
            if isinstance(value, (dict, list)) and _drop_list_item(value):
                return True
    elif isinstance(node, list):
        if node:
            node.pop()
            return True
    return False


def _read_all() -> list[dict[str, Any]]:
    directory = usage_dir()
    if not directory.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                found.append(item)
    return found


def _note_tick(args: Any, payload: Any) -> None:
    path = ""
    if isinstance(payload, dict):
        path = str(payload.get("path") or "")
    card = _card_from_walk(path) if path else str(getattr(args, "target", "") or "")
    done = _numbers(getattr(args, "done", None))
    skipped_numbers = _numbers(getattr(args, "skip", None))
    because = str(getattr(args, "because", "") or "")
    note = str(getattr(args, "note", "") or "")
    log_tick(
        card,
        done,
        [{"n": number, "because": because} for number in skipped_numbers],
        note=note,
    )


def _note_write(args: Any, payload: Any, op: str) -> None:
    if not isinstance(payload, dict):
        return
    if op == "delete":
        card = str(payload.get("deleted") or getattr(args, "id", "") or "")
    else:
        card = str(payload.get("id") or getattr(args, "id", "") or "")
    via_walk = bool(str(getattr(args, "walk", "") or "").strip())
    log_write(card, op, walk=via_walk)


def _hit_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
        return []
    ids: list[str] = []
    for hit in payload["hits"]:
        if isinstance(hit, dict) and isinstance(hit.get("id"), str):
            ids.append(hit["id"])
        if len(ids) >= HIT_CAP:
            break
    return ids


def _numbers(tokens: Any) -> list[int]:
    if not tokens:
        return []
    values: list[int] = []
    for token in tokens:
        for part in str(token).replace(" ", "").split(","):
            if part:
                values.append(int(part))
    return values


def _card_from_walk(path: str) -> str:
    file_path = Path(path)
    try:
        for line in file_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("procedure:"):
                match = re.search(r"`([^`]+)`", line)
                if match:
                    return match.group(1)
                break
    except OSError:
        pass
    name = file_path.name
    if "--" in name:
        return name.split("--", 1)[0]
    return name


def _detail(event: dict[str, Any]) -> str:
    kind = event.get("ev")
    if kind == "search":
        hits = event.get("hits") or []
        listed = ",".join(str(hit) for hit in hits)
        return f"q={event.get('query', '')} hits={listed}".strip()
    if kind == "walked":
        return f"for={event.get('purpose', '')}"
    if kind == "tick":
        done = ",".join(str(number) for number in (event.get("done") or []))
        skipped = event.get("skipped") or []
        numbers = ",".join(str(item.get("n")) for item in skipped if isinstance(item, dict))
        return f"done={done} skipped={numbers}".strip()
    if kind == "write":
        via = " walk" if event.get("walk") else ""
        return f"op={event.get('op', '')}{via}"
    return ""


def _cell(value: Any, width: int | None = None) -> str:
    text = str("" if value is None else value).replace("\n", " ").replace("\r", " ")
    if width is None:
        return text
    if len(text) > width:
        text = text[: width - 3] + "..."
    return text.ljust(width)


def _warn(exc: BaseException) -> None:
    if os.environ.get(ENV_DEBUG, "").strip():
        print(f"playbook usage: {exc}", file=sys.stderr)
