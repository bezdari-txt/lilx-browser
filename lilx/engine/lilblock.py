"""lilBlock: the built-in ad and tracker blocker.

Every :class:`~lilx.engine.page.BrowserPage` gets its own
:class:`PageBlocker` (a per-page ``QWebEngineUrlRequestInterceptor``), so the
number of blocked requests is known per tab. The decision itself is made by
:class:`LilBlock`, which owns the filter lists, the per-site exceptions (from
settings) and the block history.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWebEngineCore import QWebEngineScript, QWebEngineUrlRequestInfo, QWebEngineUrlRequestInterceptor

from lilx.core.adblock import FilterEngine, Request
from lilx.core.hosts import host_matches
from lilx.core.settings import SettingsManager
from lilx.core.storage import StorageBackend, StorageError
from lilx.engine.profile import apply_privacy_headers
from lilx.paths import RESOURCES_DIR

log = logging.getLogger(__name__)

LOG_DOCUMENT = "lilblock-log.json"
BUILTIN_LISTS = sorted((RESOURCES_DIR / "filters").glob("*.txt"))
# Page scripts for ads that network rules cannot see (e.g. YouTube embeds ad data in the
# player JSON). Each script checks location.hostname itself, so it also covers embeds.
SCRIPTLETS_DIR = RESOURCES_DIR / "filters" / "scriptlets"
SCRIPT_PREFIX = "lilblock:"
_MAX_RECENT = 300
_MAX_HOSTS = 500
_SAVE_DELAY_MS = 3000

_Type = QWebEngineUrlRequestInfo.ResourceType
_TYPE_NAMES: dict[_Type, str] = {
    _Type.ResourceTypeMainFrame: "document",
    _Type.ResourceTypeSubFrame: "subdocument",
    _Type.ResourceTypeStylesheet: "stylesheet",
    _Type.ResourceTypeScript: "script",
    _Type.ResourceTypeImage: "image",
    _Type.ResourceTypeFontResource: "font",
    _Type.ResourceTypeSubResource: "other",
    _Type.ResourceTypeObject: "object",
    _Type.ResourceTypeMedia: "media",
    _Type.ResourceTypeWorker: "script",
    _Type.ResourceTypeSharedWorker: "script",
    _Type.ResourceTypePrefetch: "other",
    _Type.ResourceTypeFavicon: "image",
    _Type.ResourceTypeXhr: "xmlhttprequest",
    _Type.ResourceTypePing: "ping",
    _Type.ResourceTypeServiceWorker: "script",
    _Type.ResourceTypeCspReport: "other",
    _Type.ResourceTypePluginResource: "object",
    _Type.ResourceTypeNavigationPreloadMainFrame: "document",
    _Type.ResourceTypeNavigationPreloadSubFrame: "subdocument",
}
if hasattr(_Type, "ResourceTypeWebSocket"):
    _TYPE_NAMES[_Type.ResourceTypeWebSocket] = "websocket"


class BlockLog(QObject):
    """History of blocked requests: per-host totals and the most recent requests."""

    def __init__(self, storage: StorageBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._storage = storage
        self._hosts: dict[str, dict[str, Any]] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=_MAX_RECENT)
        self.total = 0
        self._save_timer = QTimer(self, singleShot=True, interval=_SAVE_DELAY_MS, timeout=self.save)
        self._load()

    def _load(self) -> None:
        try:
            data = self._storage.read_json(LOG_DOCUMENT) or {}
        except StorageError as exc:
            log.error("lilBlock history is unreadable: %s", exc)
            return
        if isinstance(data, dict):
            self.total = int(data.get("total", 0))
            self._hosts = {k: v for k, v in data.get("hosts", {}).items() if isinstance(v, dict)}
            self._recent.extend(r for r in data.get("recent", []) if isinstance(r, dict))

    def save(self) -> None:
        self._save_timer.stop()
        try:
            self._storage.write_json(LOG_DOCUMENT, {
                "total": self.total, "hosts": self._hosts, "recent": list(self._recent),
            })
        except StorageError as exc:
            log.error("Could not save lilBlock history: %s", exc)

    def record(self, url: str, host: str, page_host: str, rule: str) -> None:
        now = time.time()
        self.total += 1
        entry = self._hosts.setdefault(host, {"count": 0, "last": now})
        entry["count"] += 1
        entry["last"] = now
        if len(self._hosts) > _MAX_HOSTS:
            oldest = min(self._hosts, key=lambda h: self._hosts[h]["last"])
            self._hosts.pop(oldest, None)
        self._recent.appendleft({"time": now, "url": url[:500], "host": host, "page": page_host, "rule": rule})
        if not self._save_timer.isActive():
            self._save_timer.start()

    def clear(self) -> None:
        self._hosts.clear()
        self._recent.clear()
        self.total = 0
        self.save()

    def summary(self, hosts: int = 30, recent: int = 100) -> dict[str, Any]:
        top = sorted(self._hosts.items(), key=lambda kv: kv[1]["count"], reverse=True)[:hosts]
        return {
            "total": self.total,
            "hosts": [{"host": h, **v} for h, v in top],
            "recent": list(self._recent)[:recent],
        }


class LilBlock(QObject):
    changed = Signal()  # enabled state or exceptions changed

    def __init__(
        self,
        settings: SettingsManager,
        storage: StorageBackend,
        extra_lists_dir: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self.log = BlockLog(storage, self)
        self.engine = FilterEngine()
        self.extra_lists_dir = extra_lists_dir
        for path in BUILTIN_LISTS:
            self.engine.load_file(path)
        if extra_lists_dir.is_dir():
            for path in sorted(extra_lists_dir.glob("*.txt")):
                self.engine.load_file(path)
        self._scriptlet_sources = self._load_scriptlets()
        log.info("lilBlock ready: %d rules, %d scriptlets", self.engine.rule_count, len(self._scriptlet_sources))
        settings.changed.connect(self._on_setting_changed)

    @staticmethod
    def _load_scriptlets() -> dict[str, str]:
        sources: dict[str, str] = {}
        for path in sorted(SCRIPTLETS_DIR.glob("*.js")):
            try:
                sources[path.stem] = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.error("Cannot read scriptlet %s: %s", path.name, exc)
        return sources

    def page_scripts(self) -> list[QWebEngineScript]:
        """Scriptlets for a page, reflecting the current on/off state and site exceptions.

        QtWebEngine applies a changed script set only from the next navigation, so pages
        keep these installed all the time (see BrowserPage) instead of adding them per
        navigation. Exceptions are embedded, and the script skips those sites itself.
        """
        if not self.enabled:
            return []
        allowed = json.dumps(self._settings.current.adblock_allowlist)
        guard = (
            f"const __lilblockOff = {allowed};\n"
            "const __lilblockHost = location.hostname;\n"
            "if (__lilblockOff.some((d) => __lilblockHost === d || __lilblockHost.endsWith('.' + d))) return;\n"
        )
        scripts = []
        for name, source in self._scriptlet_sources.items():
            script = QWebEngineScript()
            script.setName(SCRIPT_PREFIX + name)
            script.setSourceCode(f"(() => {{\n{guard}{source}\n}})();")
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            # The page's own world: a scriptlet has to wrap functions the page itself uses.
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(True)  # also embedded players on other sites
            scripts.append(script)
        return scripts

    def _on_setting_changed(self, key: str) -> None:
        if key in ("adblock_enabled", "adblock_allowlist"):
            self.changed.emit()

    @property
    def settings(self) -> SettingsManager:
        return self._settings

    @property
    def enabled(self) -> bool:
        return self._settings.current.adblock_enabled

    def is_allowed_site(self, host: str) -> bool:
        """True if the user turned lilBlock off for this site."""
        return any(host_matches(host, h) for h in self._settings.current.adblock_allowlist)

    def active_for(self, page_host: str) -> bool:
        return self.enabled and bool(page_host) and not self.is_allowed_site(page_host)

    def check(self, info: QWebEngineUrlRequestInfo, page_host: str) -> bool:
        """Block the request if a filter matches. Returns True when blocked."""
        url = info.requestUrl()
        if url.scheme() not in ("http", "https", "ws", "wss"):
            return False
        resource_type = _TYPE_NAMES.get(info.resourceType(), "other")
        if resource_type == "document":
            return False  # never block top-level navigations
        first_party = info.firstPartyUrl().host() or page_host
        if not self.active_for(first_party):
            return False
        request = Request.create(url.toString(), url.host(), first_party, resource_type)
        rule = self.engine.match(request)
        if rule is None:
            return False
        info.block(True)
        page_for_log = first_party if self._settings.current.history_enabled else ""
        # Query strings often carry identifiers; they are not kept in the history.
        clean_url = request.url.split("?", 1)[0].split("#", 1)[0]
        self.log.record(clean_url, request.host, page_for_log, rule.text)
        return True


class PageBlocker(QWebEngineUrlRequestInterceptor):
    """Per-page interceptor: applies lilBlock, counts blocked requests of the current page and adds
    the privacy headers (the profile interceptor is not consulted for pages with their own)."""

    count_changed = Signal(int)

    def __init__(self, lilblock: LilBlock, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lilblock = lilblock
        self._page_host = ""
        self.count = 0

    def reset(self, page_host: str) -> None:
        """A new document starts loading: its counter starts from zero.

        Called on QWebEnginePage.loadStarted rather than on the main-frame request:
        sites with a service worker (YouTube) serve navigations without one.
        """
        self._page_host = page_host
        if self.count:
            self.count = 0
            self.count_changed.emit(0)

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802 (Qt API)
        apply_privacy_headers(info, self._lilblock.settings)
        if info.resourceType() in (_Type.ResourceTypeMainFrame, _Type.ResourceTypeNavigationPreloadMainFrame):
            self._page_host = info.requestUrl().host()
            return
        if self._lilblock.check(info, self._page_host):
            self.count += 1
            self.count_changed.emit(self.count)


class ProfileBlocker(QWebEngineUrlRequestInterceptor):
    """Profile-wide interceptor for requests that do not belong to a page's own
    interceptor (service workers, shared workers, …): privacy headers + lilBlock."""

    def __init__(self, lilblock: LilBlock, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lilblock = lilblock

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802 (Qt API)
        apply_privacy_headers(info, self._lilblock.settings)
        self._lilblock.check(info, "")
