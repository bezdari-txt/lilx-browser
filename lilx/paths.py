"""Filesystem locations used by lilx.

All persistent browser data lives under a single data directory so that it can
later be moved into (or replaced by) an encrypted vault in one place.

Environment overrides:
    LILX_DATA_DIR   – use this directory for all data (cache goes to <dir>/cache).
                      Handy for development, tests and portable setups.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from lilx import APP_NAME

_PRIVATE_DIR_MODE = 0o700
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _platform_data_root() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else home / "AppData" / "Roaming") / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / APP_NAME


def _platform_cache_root() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / APP_NAME
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else home / "AppData" / "Local") / APP_NAME / "Cache"
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else home / ".cache") / APP_NAME


@dataclass(frozen=True)
class AppPaths:
    data_dir: Path
    cache_dir: Path

    @classmethod
    def default(cls, profile: str = "") -> AppPaths:
        """Paths of the default profile, or of a named one (``lilx --profile work``).

        A named profile is a complete, separate set of lilx data (settings, history,
        bookmarks, cookies, storage) in ``<data dir>/profiles/<name>``.
        """
        override = os.environ.get("LILX_DATA_DIR")
        if override:
            root = Path(override).expanduser().resolve()
            paths = cls(data_dir=root, cache_dir=root / "cache")
        else:
            paths = cls(data_dir=_platform_data_root(), cache_dir=_platform_cache_root())
        name = profile.strip()
        if not name or name == "default":
            return paths
        if not _PROFILE_NAME.match(name):
            raise ValueError("profile names may use letters, digits, '-' and '_' (up to 40)")
        return cls(data_dir=paths.data_dir / "profiles" / name, cache_dir=paths.cache_dir / "profiles" / name)

    @property
    def history_db(self) -> Path:
        return self.data_dir / "history.sqlite3"

    @property
    def webengine_dir(self) -> Path:
        """Chromium profile data: cookies, local storage, IndexedDB, ..."""
        return self.data_dir / "webengine"

    @property
    def filters_dir(self) -> Path:
        """Extra lilBlock filter lists (*.txt, Adblock Plus syntax) added by the user."""
        return self.data_dir / "filters"

    @property
    def webengine_cache_dir(self) -> Path:
        return self.cache_dir / "webengine"

    def ensure(self) -> None:
        """Create the directories with owner-only permissions."""
        for directory in (self.data_dir, self.cache_dir, self.webengine_dir, self.webengine_cache_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
        for directory in (self.data_dir, self.cache_dir):
            try:
                directory.chmod(_PRIVATE_DIR_MODE)
            except OSError:
                pass  # not fatal (e.g. a filesystem without POSIX permissions)


def default_download_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


RESOURCES_DIR = Path(__file__).resolve().parent / "resources"
PAGES_DIR = RESOURCES_DIR / "pages"
