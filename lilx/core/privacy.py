"""Per-site "forget on close" cleanup that runs while the web engine is *not* running.

Chromium keeps its data in files inside the profile directory. While the
profile is alive those files are owned by the engine, so lilx removes
per-site data from them only before the profile is created (startup) and after
it has been destroyed (shutdown). Runtime cleanup through Qt APIs lives in
:mod:`lilx.engine.browser_data`.

What is covered today (honestly):

* cookies            – rows in the Chromium ``Cookies`` SQLite database;
* IndexedDB          – per-origin directories;
* history            – handled by :class:`lilx.core.history.HistoryStore`.

Not yet covered: ``Local Storage`` and ``Service Worker``/Cache Storage are
shared LevelDB databases that are not split per origin on disk. Removing one
origin from them requires a LevelDB writer; this is tracked as a known gap.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from lilx.core.hosts import host_matches
from lilx.core.settings import SiteRule

log = logging.getLogger(__name__)

UNSUPPORTED_SITE_DATA = ("Local Storage", "Service Worker / Cache Storage")

# Chromium moved the cookie database into "Network/" in newer versions.
_COOKIE_DB_CANDIDATES = ("Network/Cookies", "Cookies")
_ORIGIN_DIR_PARENTS = ("IndexedDB",)


@dataclass
class CleanupReport:
    cookies_removed: int = 0
    history_removed: int = 0
    removed_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _origin_host(dir_name: str) -> str | None:
    """``https_example.com_0.indexeddb.leveldb`` -> ``example.com``."""
    scheme, sep, rest = dir_name.partition("_")
    if not sep or scheme not in ("http", "https"):
        return None
    host, sep, _ = rest.rpartition("_")  # strip the trailing port component
    return host or None


def remove_cookies(profile_dir: Path, hosts: list[str], report: CleanupReport) -> None:
    for relative in _COOKIE_DB_CANDIDATES:
        db_path = profile_dir / relative
        if not db_path.is_file():
            continue
        try:
            with sqlite3.connect(db_path) as db:
                db.execute("PRAGMA secure_delete = ON")
                rows = db.execute("SELECT rowid, host_key FROM cookies").fetchall()
                doomed = [(rowid,) for rowid, host_key in rows if any(host_matches(host_key, h) for h in hosts)]
                db.executemany("DELETE FROM cookies WHERE rowid = ?", doomed)
            db.close()
            report.cookies_removed += len(doomed)
        except sqlite3.Error as exc:
            report.errors.append(f"cookies ({db_path.name}): {exc}")


def remove_origin_dirs(profile_dir: Path, hosts: list[str], report: CleanupReport) -> None:
    for parent_name in _ORIGIN_DIR_PARENTS:
        parent = profile_dir / parent_name
        if not parent.is_dir():
            continue
        for entry in parent.iterdir():
            host = _origin_host(entry.name)
            if host is None or not any(host_matches(host, h) for h in hosts):
                continue
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                report.removed_paths.append(str(entry.relative_to(profile_dir)))
            except OSError as exc:
                report.errors.append(f"{entry.name}: {exc}")


def run_offline_cleanup(profile_dir: Path, rules: list[SiteRule]) -> CleanupReport:
    """Remove on-disk data of the given sites. The web engine must not be running."""
    report = CleanupReport()
    cookie_hosts = [r.host for r in rules if r.clear_cookies]
    storage_hosts = [r.host for r in rules if r.clear_site_data]
    if cookie_hosts:
        remove_cookies(profile_dir, cookie_hosts, report)
    if storage_hosts:
        remove_origin_dirs(profile_dir, storage_hosts, report)
    if report.cookies_removed or report.removed_paths:
        log.info("Offline cleanup: %d cookies, %d storage entries removed",
                 report.cookies_removed, len(report.removed_paths))
    for error in report.errors:
        log.warning("Offline cleanup error: %s", error)
    return report
