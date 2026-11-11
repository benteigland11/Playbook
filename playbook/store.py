"""Global procedure store.

One resolver for the CLI and the MCP server. Precedence:

1. ``PLAYBOOK_STORE``, when it is set to a non-blank value
2. ``store`` in the config file, when that file exists and sets it
3. the platform data directory (unchanged when nothing is set)

No CLI flag selects the store. ``~`` and environment variables in a setting
are expanded. The directory is created on write, the same as the default.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from playbook.app_paths import resolve_app_paths

APP_NAME = "playbook"
ENV_STORE = "PLAYBOOK_STORE"
CONFIG_FILENAME = "config.json"
CONFIG_KEY = "store"


def procedures_dir() -> Path:
    chosen = _configured_store()
    if chosen is not None:
        return chosen
    return default_procedures_dir()


def default_procedures_dir() -> Path:
    """Today's location: ``<platform data dir>/procedures``.

    On Linux that is ``$XDG_DATA_HOME/playbook/procedures`` or
    ``~/.local/share/playbook/procedures``. macOS and Windows follow
    ``resolve_app_paths`` the same way they did before this setting existed.
    """
    return resolve_app_paths(APP_NAME).data_dir / "procedures"


def procedure_path(procedure_id: str) -> Path:
    return procedures_dir() / f"{procedure_id}.json"


def config_file() -> Path:
    """Config file path. Not created by Playbook.

    ``store`` selects the procedure directory. ``machine`` and ``usage`` are
    read by the usage log and ignored by store resolution.
    """
    return resolve_app_paths(APP_NAME).config_dir / CONFIG_FILENAME


def load_config() -> dict[str, Any] | None:
    """The config object, or None when no config file exists.

    This is the same read ``procedures_dir`` uses. A missing file is empty.
    A file that is not a JSON object raises ValueError.
    """
    path = config_file()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read playbook config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"playbook config {path}: expected a JSON object")
    return data


def _configured_store() -> Path | None:
    env = os.environ.get(ENV_STORE)
    if env is not None and env.strip():
        return expand_store_path(env)
    return _store_from_config()


def _store_from_config() -> Path | None:
    data = load_config()
    if data is None:
        return None
    if CONFIG_KEY not in data or data[CONFIG_KEY] is None:
        return None
    store_value = data[CONFIG_KEY]
    if not isinstance(store_value, str):
        path = config_file()
        raise ValueError(f"playbook config {path}: {CONFIG_KEY!r} must be a string")
    if not store_value.strip():
        return None
    return expand_store_path(store_value)


def expand_store_path(value: str) -> Path:
    """Expand ``~`` and environment variables. A relative path stays relative."""
    expanded = os.path.expanduser(os.path.expandvars(value.strip()))
    if not expanded.strip():
        raise ValueError("playbook store path is empty")
    return Path(expanded)
