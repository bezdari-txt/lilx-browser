"""Factory reset: remove every piece of browser data lilx created.

Only entries lilx itself creates are deleted, never the whole data directory:
``LILX_DATA_DIR`` may point at a folder that also holds other files. Application
code and files the user downloaded are never touched.

Must run while the web engine is stopped. Chromium helper processes can keep
writing for a moment after the profile is destroyed, so deletion is retried, and
a ``reset-pending`` marker lets the next start finish the job before the
profile is opened (see :func:`finish_pending_reset`).
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from lilx.paths import AppPaths

log = logging.getLogger(__name__)

# Relative to AppPaths.data_dir. Prefix matches also remove temp/journal siblings.
RESET_MARKER = "reset-pending"
_DATA_ENTRIES = (
    RESET_MARKER,
    "settings.json",
    "downloads.json",
    "bookmarks.json",
    "extensions.json",  # installed extensions themselves live in webengine/Extensions
    "lilblock-log.json",
    "history.sqlite3",
    "webengine",  # cookies, local/session storage, IndexedDB, internal page data, ...
    "filters",  # user-added lilBlock lists
)
_CACHE_ENTRIES = ("webengine",)


def _remove(path: Path, removed: list[str], errors: list[str]) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(str(path))
    except FileNotFoundError:
        pass
    except OSError as exc:
        errors.append(f"{path}: {exc}")


def _targets(paths: AppPaths) -> list[Path]:
    targets: list[Path] = []
    if paths.data_dir.is_dir():
        for entry in paths.data_dir.iterdir():
            # "settings.json.corrupted", "history.sqlite3-journal", ".settings.json.tmp" ...
            name = entry.name.lstrip(".")
            if any(name == known or name.startswith(known + ".") or name.startswith(known + "-")
                   for known in _DATA_ENTRIES):
                targets.append(entry)
    targets.extend(paths.cache_dir / name for name in _CACHE_ENTRIES)
    # The marker goes last: if anything fails, the next start retries.
    targets.sort(key=lambda p: p.name == RESET_MARKER)
    return targets


def factory_reset(paths: AppPaths, attempts: int = 5, delay: float = 0.3) -> tuple[list[str], list[str]]:
    """Delete lilx data. Returns (removed paths, errors of the last attempt)."""
    mark_reset_pending(paths)
    removed: list[str] = []
    errors: list[str] = []
    for attempt in range(attempts):
        errors = []
        for target in _targets(paths):
            if target.name == RESET_MARKER and errors:
                continue  # keep the marker while something could not be deleted
            _remove(target, removed, errors)
        if not errors:
            break
        if attempt + 1 < attempts:
            time.sleep(delay)
    for error in errors:
        log.error("Reset: could not remove %s", error)
    log.info("Factory reset removed %d entries%s", len(removed), " (incomplete, will retry)" if errors else "")
    return removed, errors


def mark_reset_pending(paths: AppPaths) -> None:
    try:
        paths.data_dir.mkdir(parents=True, exist_ok=True)
        (paths.data_dir / RESET_MARKER).write_text("1")
    except OSError as exc:
        log.error("Cannot write reset marker: %s", exc)


def finish_pending_reset(paths: AppPaths) -> None:
    """At startup, before the profile exists: complete an interrupted reset."""
    wait_pid = os.environ.pop("LILX_WAIT_FOR_PID", "")
    if wait_pid.isdigit():
        _wait_for_exit(int(wait_pid), timeout=10.0)
    if (paths.data_dir / RESET_MARKER).exists():
        log.info("Finishing a pending reset")
        factory_reset(paths)


def _wait_for_exit(pid: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return  # not ours any more
        time.sleep(0.1)
