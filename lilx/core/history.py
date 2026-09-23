"""Browsing history stored in SQLite.

The database is a plain (unencrypted) SQLite file. ``secure_delete`` is on, so
removed rows are overwritten instead of lingering in free pages. When the
encrypted vault lands, this store is expected to move to an encrypted database
(e.g. SQLCipher) or be serialized through the vault backend; callers only use
the methods of :class:`HistoryStore`.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# A reload or redirect back to the same URL within this window is not a new visit.
_DEDUP_SECONDS = 30.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS visits (
    id         INTEGER PRIMARY KEY,
    url        TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    host       TEXT NOT NULL,
    visited_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS visits_visited_at ON visits (visited_at);
CREATE INDEX IF NOT EXISTS visits_host ON visits (host);
"""


@dataclass(frozen=True)
class HistoryEntry:
    id: int
    url: str
    title: str
    host: str
    visited_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class HistoryStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._db = sqlite3.connect(db_path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA secure_delete = ON")
        self._db.executescript(_SCHEMA)
        self._db.commit()
        try:
            db_path.chmod(0o600)
        except OSError:
            pass

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> HistoryEntry:
        return HistoryEntry(row["id"], row["url"], row["title"], row["host"], row["visited_at"])

    def add_visit(self, url: str, title: str = "") -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return
        now = time.time()
        last = self._db.execute(
            "SELECT id, url, visited_at FROM visits ORDER BY visited_at DESC LIMIT 1"
        ).fetchone()
        with self._db:
            if last and last["url"] == url and now - last["visited_at"] < _DEDUP_SECONDS:
                if title:
                    self._db.execute("UPDATE visits SET title = ? WHERE id = ?", (title, last["id"]))
                return
            self._db.execute(
                "INSERT INTO visits (url, title, host, visited_at) VALUES (?, ?, ?, ?)",
                (url, title, parts.hostname.lower(), now),
            )

    def update_title(self, url: str, title: str) -> None:
        if not title:
            return
        with self._db:
            self._db.execute(
                "UPDATE visits SET title = ? WHERE id = "
                "(SELECT id FROM visits WHERE url = ? ORDER BY visited_at DESC LIMIT 1)",
                (title, url),
            )

    def search(self, query: str = "", limit: int = 200, offset: int = 0) -> list[HistoryEntry]:
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)
        if query:
            pattern = f"%{_escape_like(query)}%"
            rows = self._db.execute(
                "SELECT * FROM visits WHERE url LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' "
                "ORDER BY visited_at DESC LIMIT ? OFFSET ?",
                (pattern, pattern, limit, offset),
            )
        else:
            rows = self._db.execute(
                "SELECT * FROM visits ORDER BY visited_at DESC LIMIT ? OFFSET ?", (limit, offset)
            )
        return [self._row(r) for r in rows]

    def top_sites(self, limit: int = 8, exclude: list[str] | tuple[str, ...] = ()) -> list[dict[str, Any]]:
        """Most visited hosts. ``exclude``: hosts the user removed from "Frequently visited"."""
        placeholders = ",".join("?" * len(exclude))
        where = f"WHERE host NOT IN ({placeholders}) " if exclude else ""
        rows = self._db.execute(
            "SELECT host, url, title, COUNT(*) AS visits, MAX(visited_at) AS last FROM visits "
            f"{where}GROUP BY host ORDER BY visits DESC, last DESC LIMIT ?",
            (*exclude, limit),
        )
        return [
            {
                "host": r["host"],
                "url": f"{urlsplit(r['url']).scheme}://{r['host']}/",
                "title": r["title"],
                "visits": r["visits"],
            }
            for r in rows
        ]

    def delete_entry(self, entry_id: int) -> None:
        with self._db:
            self._db.execute("DELETE FROM visits WHERE id = ?", (entry_id,))

    def delete_host(self, host: str) -> int:
        """Delete visits to ``host`` and its subdomains. Returns the number of rows removed."""
        host = host.lower().lstrip(".")
        if not host:
            return 0
        with self._db:
            cur = self._db.execute(
                "DELETE FROM visits WHERE host = ? OR host LIKE ? ESCAPE '\\'",
                (host, f"%.{_escape_like(host)}"),
            )
        return cur.rowcount

    def clear(self) -> None:
        with self._db:
            self._db.execute("DELETE FROM visits")
        self._db.execute("VACUUM")

    def count(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM visits").fetchone()[0])
