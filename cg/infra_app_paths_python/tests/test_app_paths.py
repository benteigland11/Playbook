from pathlib import Path

from src.app_paths import normalize_platform
from src.app_paths import resolve_app_paths


def test_resolve_app_paths_uses_xdg_on_linux() -> None:
    home = Path("home") / "user"
    paths = resolve_app_paths(
        "example_app",
        platform="linux",
        home=home,
        environ={
            "XDG_CONFIG_HOME": "cfg",
            "XDG_STATE_HOME": "state",
            "XDG_CACHE_HOME": "cache",
            "XDG_DATA_HOME": "data",
        },
    )

    assert paths.config_dir == Path("cfg/example_app")
    assert paths.state_dir == Path("state/example_app")
    assert paths.cache_dir == Path("cache/example_app")
    assert paths.data_dir == Path("data/example_app")


def test_resolve_app_paths_uses_linux_defaults() -> None:
    home = Path("home") / "user"
    paths = resolve_app_paths("example_app", platform="linux", home=home, environ={})

    assert paths.config_dir == home / ".config" / "example_app"
    assert paths.state_dir == home / ".local" / "state" / "example_app"
    assert paths.cache_dir == home / ".cache" / "example_app"
    assert paths.data_dir == home / ".local" / "share" / "example_app"


def test_resolve_app_paths_uses_windows_appdata() -> None:
    home = Path("Users") / "person"
    paths = resolve_app_paths(
        "ExampleApp",
        vendor_name="ExampleOrg",
        platform="win32",
        home=home,
        environ={
            "APPDATA": "Users/person/AppData/Roaming",
            "LOCALAPPDATA": "Users/person/AppData/Local",
        },
    )

    assert paths.config_dir == Path("Users/person/AppData/Roaming/ExampleOrg/ExampleApp")
    assert paths.state_dir == Path("Users/person/AppData/Local/ExampleOrg/ExampleApp/State")
    assert paths.cache_dir == Path("Users/person/AppData/Local/ExampleOrg/ExampleApp/Cache")


def test_resolve_app_paths_uses_macos_library() -> None:
    home = Path("Users") / "person"
    paths = resolve_app_paths("ExampleApp", platform="darwin", home=home, environ={})

    assert paths.config_dir == home / "Library" / "Application Support" / "ExampleApp"
    assert paths.state_dir == home / "Library" / "Application Support" / "ExampleApp"
    assert paths.cache_dir == home / "Library" / "Caches" / "ExampleApp"


def test_normalize_platform() -> None:
    assert normalize_platform("win32") == "windows"
    assert normalize_platform("darwin") == "macos"
    assert normalize_platform("linux") == "linux"
