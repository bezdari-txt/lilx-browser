"""JSON API used by the internal lilx:// pages.

Each internal page may only call methods of its own namespace (``history.*``
from lilx://history, ...) plus ``common.*``. Methods that change state require
POST. The scheme handler additionally checks that the request comes from a
lilx:// page, so websites cannot reach this API.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6 import __version__ as pyside_version
from PySide6.QtGui import QFontDatabase
from PySide6.QtWebEngineCore import qWebEngineChromiumVersion, qWebEngineVersion

from lilx import __version__
from lilx.core import permissions as perms
from lilx.core.bookmarks import BookmarkError, BookmarkStore
from lilx.core.downloads import DownloadManager
from lilx.core.history import HistoryStore
from lilx.core.hosts import normalize_host
from lilx.core.privacy import UNSUPPORTED_SITE_DATA
from lilx.core.search import ENGINES, get_engine, looks_like_url, resolve_input
from lilx.core.settings import THEMES, ZOOM_LEVELS, SettingsError, SettingsManager, SiteRule
from lilx.core.storage import StorageBackend
from lilx.engine.browser_data import BrowserDataManager
from lilx.engine.extensions import ExtensionError, ExtensionService
from lilx.engine.lilblock import LilBlock
from lilx.i18n import LANGUAGES, resolve_language
from lilx.paths import AppPaths, default_download_dir

log = logging.getLogger(__name__)

Params = dict[str, Any]


class ApiError(Exception):
    """An error message that is safe to show on the page."""


@dataclass(frozen=True)
class _Method:
    handler: Callable[[Params], Any]
    mutating: bool


def _str(params: Params, key: str, default: str = "") -> str:
    value = params.get(key, default)
    if not isinstance(value, str):
        raise ApiError(f"'{key}' must be a string")
    return value


def _int(params: Params, key: str, default: int = 0) -> int:
    value = params.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(f"'{key}' must be a number") from None


def _font_families() -> list[str]:
    """Font families installed on the system (hidden/private families excluded)."""
    return sorted({f for f in QFontDatabase.families() if not f.startswith(".") and not QFontDatabase.isPrivateFamily(f)},
                  key=str.casefold)


class InternalApi:
    def __init__(
        self,
        *,
        paths: AppPaths,
        storage: StorageBackend,
        settings: SettingsManager,
        history: HistoryStore,
        downloads: DownloadManager,
        data: BrowserDataManager,
        lilblock: LilBlock,
        bookmarks: BookmarkStore,
        extensions: ExtensionService,
        choose_extension: Callable[[str], None],
        request_reset: Callable[[], None],
        choose_download_dir: Callable[[], None],
    ) -> None:
        self._paths = paths
        self._storage = storage
        self._settings = settings
        self._history = history
        self._downloads = downloads
        self._data = data
        self._lilblock = lilblock
        self._bookmarks = bookmarks
        self._extensions = extensions
        self._choose_extension = choose_extension
        self._request_reset = request_reset
        self._choose_download_dir = choose_download_dir
        self._methods: dict[str, _Method] = {}

        reads = {
            "home.data": self._home_data,
            "home.resolve": self._home_resolve,
            "settings.get": self._settings_get,
            "history.list": self._history_list,
            "downloads.list": self._downloads_list,
            "extensions.list": lambda p: self._extensions.summary(),
        }
        writes = {
            "settings.set": self._settings_set,
            # File dialogs open after the reply; the page refreshes on "lilx:settings-changed".
            "extensions.install_zip": lambda p: self._choose_extension("zip"),
            "extensions.install_folder": lambda p: self._choose_extension("folder"),
            "extensions.install_path": lambda p: self._extensions.install(_str(p, "path")),
            "extensions.set_enabled": lambda p: self._extensions.set_enabled(_str(p, "id"), bool(p.get("enabled"))),
            "extensions.uninstall": lambda p: self._extensions.uninstall(_str(p, "id")),
            "extensions.dismiss_errors": lambda p: self._extensions.dismiss_errors(),
            "home.bookmark_add": self._bookmark_add,
            "home.bookmark_remove": self._bookmark_remove,
            "home.top_site_hide": self._top_site_hide,
            "home.top_sites_off": lambda p: self._settings.set("show_top_sites", False),
            "settings.top_sites_restore": self._top_sites_restore,
            "settings.permission_default": self._permission_default,
            "settings.site_permission_set": self._site_permission_set,
            "settings.site_permission_remove": self._site_permission_remove,
            "settings.site_permissions_clear": self._site_permissions_clear,
            "settings.site_permissions_reset": self._site_permissions_reset,
            "settings.site_rule_set": self._site_rule_set,
            "settings.site_rule_remove": self._site_rule_remove,
            "settings.clear_data": self._clear_data,
            "history.delete": self._history_delete,
            "history.delete_host": self._history_delete_host,
            "history.clear": self._history_clear,
            "settings.adblock_allow": self._adblock_allow,
            "settings.adblock_block": self._adblock_block,
            "settings.adblock_clear_allowlist": self._adblock_clear_allowlist,
            "settings.adblock_clear_log": self._adblock_clear_log,
            "settings.reset": self._reset,
            # The folder dialog opens after the reply; pages learn the result from a
            # "lilx:settings-changed" event dispatched by the main window.
            "settings.choose_download_dir": lambda p: self._choose_download_dir(),
            "settings.open_download_dir": lambda p: self._downloads.open_directory(),
            "downloads.choose_dir": lambda p: self._choose_download_dir(),
            "downloads.open_dir": lambda p: self._downloads.open_directory(),
            "downloads.pause": lambda p: self._downloads.pause(_str(p, "id")),
            "downloads.resume": lambda p: self._downloads.resume(_str(p, "id")),
            "downloads.cancel": lambda p: self._downloads.cancel(_str(p, "id")),
            "downloads.open": lambda p: self._downloads.open_file(_str(p, "id")),
            "downloads.show": lambda p: self._downloads.show_in_folder(_str(p, "id")),
            "downloads.remove": lambda p: self._downloads.remove(_str(p, "id")),
            "downloads.clear": lambda p: self._downloads.clear_finished(),
        }
        for name, handler in reads.items():
            self._methods[name] = _Method(handler, mutating=False)
        for name, handler in writes.items():
            self._methods[name] = _Method(handler, mutating=True)

    def call(self, page: str, name: str, params: Params, http_method: str) -> dict[str, Any]:
        namespace = name.partition(".")[0]
        method = self._methods.get(name)
        if method is None or namespace not in (page, "common"):
            return {"ok": False, "error": f"unknown method: {name}"}
        if method.mutating and http_method != "POST":
            return {"ok": False, "error": "this method requires POST"}
        try:
            return {"ok": True, "data": method.handler(params)}
        except (ApiError, SettingsError, BookmarkError, ExtensionError, perms.PermissionRuleError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception:
            log.exception("Internal API method %s failed", name)
            return {"ok": False, "error": "internal error"}

    # -- home ------------------------------------------------------------------
    def _home_data(self, params: Params) -> Any:
        current = self._settings.current
        engine = get_engine(current.search_engine)
        return {
            "engine": {"id": engine.id, "name": engine.name},
            "show_bookmarks": current.show_home_bookmarks,
            "bookmarks": [b.to_dict() for b in self._bookmarks.all()] if current.show_home_bookmarks else [],
            "show_top_sites": current.show_top_sites,
            "top_sites": (self._history.top_sites(8, current.top_sites_hidden)
                          if current.history_enabled and current.show_top_sites else []),
            "privacy": {
                "history_enabled": current.history_enabled,
                "third_party_cookies_blocked": current.block_third_party_cookies,
                "privacy_signals": current.send_privacy_signals,
                "forget_on_close": len(current.forget_on_close),
                "lilblock_enabled": current.adblock_enabled,
                "lilblock_total": self._lilblock.log.total,
            },
        }

    def _bookmark_add(self, params: Params) -> Any:
        text = _str(params, "url").strip()
        # Accept what people type in the address bar ("github.com"), but never searches.
        if not (looks_like_url(text) or text.startswith(("http://", "https://", "lilx://"))):
            raise ApiError("enter an address like example.com")
        url = resolve_input(text, self._settings.current.search_engine)
        return self._bookmarks.add(url, _str(params, "title")).to_dict()

    def _bookmark_remove(self, params: Params) -> Any:
        return {"removed": self._bookmarks.remove(_str(params, "id"))}

    def _top_site_hide(self, params: Params) -> Any:
        host = _str(params, "host")
        if not host:
            raise ApiError("no site given")
        self._settings.hide_top_site(host)
        return self._home_data({})

    # Site permissions changed here are explicit user settings, so they are always persistent
    # (also when Settings is opened from a private window).
    def _permission_default(self, params: Params) -> Any:
        self._settings.set_permission_default(_str(params, "kind"), _str(params, "value"))
        return self._settings_get({})

    def _site_permission_set(self, params: Params) -> Any:
        self._settings.set_site_permission(_str(params, "origin"), _str(params, "kind"), _str(params, "value"))
        return self._settings_get({})

    def _site_permission_remove(self, params: Params) -> Any:
        self._settings.remove_site_permission(_str(params, "origin"), _str(params, "kind"))
        return self._settings_get({})

    def _site_permissions_clear(self, params: Params) -> Any:
        self._settings.clear_site_permissions(_str(params, "origin"))
        return self._settings_get({})

    def _site_permissions_reset(self, params: Params) -> Any:
        self._settings.reset_site_permissions()
        return self._settings_get({})

    def _top_sites_restore(self, params: Params) -> Any:
        self._settings.restore_top_sites()
        return self._settings_get({})

    def _home_resolve(self, params: Params) -> Any:
        url = resolve_input(_str(params, "q"), self._settings.current.search_engine)
        if not url:
            raise ApiError("nothing to open")
        return {"url": url}

    # -- settings ----------------------------------------------------------------
    def _settings_get(self, params: Params) -> Any:
        return {
            "settings": self._settings.current.to_dict(),
            "engines": [{"id": e.id, "name": e.name} for e in ENGINES.values()],
            "themes": list(THEMES),
            "languages": LANGUAGES,
            "fonts": _font_families(),
            "zoom_levels": list(ZOOM_LEVELS),
            "permission_kinds": list(perms.KINDS),
            "language": resolve_language(self._settings.current.language),
            "lilblock": {
                "rules": self._lilblock.engine.rule_count,
                "lists": list(self._lilblock.engine.sources),
                "extra_lists_dir": str(self._lilblock.extra_lists_dir),
                "log": self._lilblock.log.summary(),
            },
            "default_download_dir": str(default_download_dir()),
            "download_dir": self._current_download_dir(),
            "storage": {
                "data_dir": str(self._paths.data_dir),
                "encrypted": self._storage.encrypted,
                "history_entries": self._history.count(),
                "cookies": self._data.cookie_count(),
                "cookie_sites": self._data.site_count(),
            },
            "unsupported_site_data": list(UNSUPPORTED_SITE_DATA),
            "about": {
                "lilx": __version__,
                "pyside": pyside_version,
                "qtwebengine": qWebEngineVersion(),
                "chromium": qWebEngineChromiumVersion(),
            },
        }

    def _settings_set(self, params: Params) -> Any:
        key = _str(params, "key")
        value = params.get("value")
        if key in ("font_standard", "font_fixed") and value and value not in _font_families():
            raise ApiError(f"font is not installed: {value}")
        self._settings.set(key, params.get("value"))
        return self._settings_get({})

    def _site_rule_set(self, params: Params) -> Any:
        host = normalize_host(_str(params, "host"))
        if not host:
            raise ApiError("enter a site like example.com")
        rule = SiteRule(
            host=host,
            clear_cookies=bool(params.get("clear_cookies", True)),
            clear_site_data=bool(params.get("clear_site_data", True)),
            clear_history=bool(params.get("clear_history", True)),
        )
        if not (rule.clear_cookies or rule.clear_site_data or rule.clear_history):
            raise ApiError("choose at least one kind of data")
        self._settings.set_site_rule(rule)
        return self._settings_get({})

    def _site_rule_remove(self, params: Params) -> Any:
        self._settings.remove_site_rule(_str(params, "host"))
        return self._settings_get({})

    def _clear_data(self, params: Params) -> Any:
        if params.get("history"):
            self._data.clear_history()
        if params.get("cookies"):
            self._data.clear_cookies()
        if params.get("cache"):
            self._data.clear_cache()
        if params.get("downloads"):
            self._downloads.clear_finished()
        return self._settings_get({})

    def _adblock_allow(self, params: Params) -> Any:
        self._settings.allow_ads(_str(params, "host"))
        return self._settings_get({})

    def _adblock_block(self, params: Params) -> Any:
        self._settings.block_ads(_str(params, "host"))
        return self._settings_get({})

    def _adblock_clear_allowlist(self, params: Params) -> Any:
        self._settings.clear_allowlist()
        return self._settings_get({})

    def _adblock_clear_log(self, params: Params) -> Any:
        self._lilblock.log.clear()
        return self._settings_get({})

    def _reset(self, params: Params) -> Any:
        if params.get("confirm") != "reset":
            raise ApiError("reset needs confirmation")
        self._request_reset()
        return {"restarting": True}

    # -- history -------------------------------------------------------------------
    def _history_list(self, params: Params) -> Any:
        entries = self._history.search(
            _str(params, "q"), limit=_int(params, "limit", 200), offset=_int(params, "offset", 0)
        )
        return {
            "enabled": self._settings.current.history_enabled,
            "entries": [e.to_dict() for e in entries],
        }

    def _history_delete(self, params: Params) -> Any:
        self._history.delete_entry(_int(params, "id"))

    def _history_delete_host(self, params: Params) -> Any:
        return {"removed": self._history.delete_host(_str(params, "host"))}

    def _history_clear(self, params: Params) -> Any:
        self._data.clear_history()

    def _current_download_dir(self) -> str:
        try:
            return str(self._downloads.target_directory())
        except OSError:
            return self._settings.current.download_dir or str(default_download_dir())

    # -- downloads -----------------------------------------------------------------
    def _downloads_list(self, params: Params) -> Any:
        return {
            "directory": self._current_download_dir(),
            "custom": bool(self._settings.current.download_dir),
            "items": [r.to_dict() for r in self._downloads.records()],
        }
