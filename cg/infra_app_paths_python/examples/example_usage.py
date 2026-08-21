from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.app_paths import resolve_app_paths


paths = resolve_app_paths(
    "example_app",
    platform="linux",
    home=Path("home") / "user",
    environ={},
)

print(paths.config_dir)
print(paths.state_dir)
