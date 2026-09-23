"""Bookmarks, stored as a JSON document through the storage backend."""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Signal

from lilx.core.storage import StorageBackend, StorageError

log = logging.getLogger(__name__)

BOOKMARKS_DOCUMENT = "bookmarks.json"
_ALLOWED_SCHEMES = ("http", "https", "lilx")
_MAX_TITLE = 200


class BookmarkError(ValueError):
    """Invalid bookmark (message is safe to show)."""


@dataclass
class Bookmark:
    id: str
    url: str
    title: str
    created_at: float

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    def to_dict(self) -> dict[str, Any]:
        return {**dataclasses.asdict(self), "host": self.host}


def _clean_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES or not (parts.netloc or parts.scheme == "lilx"):
        raise BookmarkError("only http, https and lilx addresses can be bookmarked")
    return url


class BookmarkStore(QObject):
    changed = Signal()

    def __init__(self, storage: StorageBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._storage = storage
        self._items: list[Bookmark] = []
        self._load()

    def _load(self) -> None:
        try:
            data = self._storage.read_json(BOOKMARKS_DOCUMENT) or []
        except StorageError as exc:
            log.error("Bookmarks are unreadable: %s", exc)
            return
        for item in data if isinstance(data, list) else []:
            try:
                self._items.append(Bookmark(
                    id=str(item["id"]), url=_clean_url(str(item["url"])),
                    title=str(item.get("title", ""))[:_MAX_TITLE], created_at=float(item.get("created_at", 0)),
                ))
            except (KeyError, TypeError, ValueError):
                continue

    def _save(self) -> None:
        try:
            self._storage.write_json(BOOKMARKS_DOCUMENT, [dataclasses.asdict(b) for b in self._items])
        except StorageError as exc:
            log.error("Could not save bookmarks: %s", exc)
        self.changed.emit()

    def all(self) -> list[Bookmark]:
        return list(self._items)

    def find(self, url: str) -> Bookmark | None:
        url = url.strip()
        return next((b for b in self._items if b.url == url), None)

    def add(self, url: str, title: str = "") -> Bookmark:
        url = _clean_url(url)
        existing = self.find(url)
        if existing is not None:
            return existing
        title = (title.strip() or urlsplit(url).hostname or url)[:_MAX_TITLE]
        bookmark = Bookmark(id=uuid.uuid4().hex, url=url, title=title, created_at=time.time())
        self._items.append(bookmark)
        self._save()
        return bookmark

    def remove(self, bookmark_id: str) -> bool:
        before = len(self._items)
        self._items = [b for b in self._items if b.id != bookmark_id]
        if len(self._items) != before:
            self._save()
            return True
        return False

    def remove_url(self, url: str) -> bool:
        bookmark = self.find(url)
        return self.remove(bookmark.id) if bookmark else False
