"""Global procedure store: platform data dir via infra-app-paths."""

from __future__ import annotations

from pathlib import Path

from playbook.app_paths import resolve_app_paths

APP_NAME = "playbook"


def procedures_dir() -> Path:
    return resolve_app_paths(APP_NAME).data_dir / "procedures"


def procedure_path(procedure_id: str) -> Path:
    return procedures_dir() / f"{procedure_id}.json"
