from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import sys


@dataclass(frozen=True)
class AppPaths:
    """Resolved per-user paths for a local application."""

    config_dir: Path
    state_dir: Path
    cache_dir: Path
    data_dir: Path


def resolve_app_paths(
    app_name: str,
    *,
    vendor_name: str = "",
    platform: str | None = None,
    home: str | Path | None = None,
    environ: dict[str, str] | None = None,
) -> AppPaths:
    name = normalize_app_name(app_name)
    vendor = normalize_app_name(vendor_name) if vendor_name else ""
    resolved_platform = normalize_platform(platform)
    resolved_home = Path(home).expanduser() if home is not None else Path.home()
    env = dict(os.environ if environ is None else environ)

    if resolved_platform == "windows":
        roaming_root = Path(env.get("APPDATA") or resolved_home / "AppData" / "Roaming")
        local_root = Path(env.get("LOCALAPPDATA") or resolved_home / "AppData" / "Local")
        relative = Path(vendor) / name if vendor else Path(name)
        return AppPaths(
            config_dir=roaming_root / relative,
            state_dir=local_root / relative / "State",
            cache_dir=local_root / relative / "Cache",
            data_dir=local_root / relative / "Data",
        )

    if resolved_platform == "macos":
        relative = Path(vendor) / name if vendor else Path(name)
        support_root = resolved_home / "Library" / "Application Support"
        return AppPaths(
            config_dir=support_root / relative,
            state_dir=support_root / relative,
            cache_dir=resolved_home / "Library" / "Caches" / relative,
            data_dir=support_root / relative,
        )

    config_root = Path(env.get("XDG_CONFIG_HOME") or resolved_home / ".config")
    state_root = Path(env.get("XDG_STATE_HOME") or resolved_home / ".local" / "state")
    cache_root = Path(env.get("XDG_CACHE_HOME") or resolved_home / ".cache")
    data_root = Path(env.get("XDG_DATA_HOME") or resolved_home / ".local" / "share")
    return AppPaths(
        config_dir=config_root / name,
        state_dir=state_root / name,
        cache_dir=cache_root / name,
        data_dir=data_root / name,
    )


def normalize_platform(platform: str | None = None) -> str:
    raw = (platform or sys.platform).strip().lower()
    if raw.startswith(("win", "cygwin", "msys")):
        return "windows"
    if raw.startswith("darwin") or raw in {"mac", "macos", "osx"}:
        return "macos"
    return "linux"


def normalize_app_name(app_name: str) -> str:
    normalized = str(app_name or "").strip()
    if not normalized:
        raise ValueError("app_name is required")
    return "".join(ch for ch in normalized if ch.isalnum() or ch in {"-", "_", ".", " "}).strip()
