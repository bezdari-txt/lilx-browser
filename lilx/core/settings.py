"""User settings: model, validation and persistence."""

from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from lilx.core import permissions as perms
from lilx.core.hosts import normalize_host
from lilx.core.search import DEFAULT_ENGINE, ENGINES
from lilx.core.storage import StorageBackend, StorageError

log = logging.getLogger(__name__)

SETTINGS_DOCUMENT = "settings.json"
THEMES = ("system", "light", "dark")
ACCENT_MODES = ("default", "custom")
_HEX_COLOR = re.compile(r"^#[0-9a-f]{6}$")
INT_RANGES = {"font_size": (9, 32), "default_zoom": (50, 200)}
ZOOM_LEVELS = (50, 67, 75, 80, 90, 100, 110, 125, 150, 175, 200)
LANGUAGE_CODES = ("", "en", "ru")  # "" = follow the system locale (first run)


@dataclass
class SiteRule:
    """Data of this site (and its subdomains) is removed when lilx closes."""

    host: str
    clear_cookies: bool = True
    clear_site_data: bool = True
    clear_history: bool = True

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SiteRule | None:
        host = normalize_host(str(data.get("host", "")))
        if not host:
            return None
        return cls(
            host=host,
            clear_cookies=bool(data.get("clear_cookies", True)),
            clear_site_data=bool(data.get("clear_site_data", True)),
            clear_history=bool(data.get("clear_history", True)),
        )


@dataclass
class Settings:
    search_engine: str = DEFAULT_ENGINE
    homepage: str = "lilx://home"
    theme: str = "system"
    accent_mode: str = "default"  # "default" (lilx lilac) or "custom"
    accent_color: str = "#3b82f6"  # used when accent_mode == "custom"
    history_enabled: bool = True
    block_third_party_cookies: bool = True
    send_privacy_signals: bool = True  # Sec-GPC and DNT headers
    javascript_enabled: bool = True
    download_dir: str = ""  # empty: the system Downloads folder
    language: str = ""
    adblock_enabled: bool = True
    # home page
    show_home_bookmarks: bool = True
    show_top_sites: bool = True
    # web content
    font_standard: str = ""  # "" = engine default
    font_fixed: str = ""
    font_size: int = 16
    default_zoom: int = 100  # percent
    smooth_scrolling: bool = True
    block_autoplay: bool = True  # media with sound needs a click to start
    forget_on_close: list[SiteRule] = field(default_factory=list)
    adblock_allowlist: list[str] = field(default_factory=list)  # sites where lilBlock is off
    top_sites_hidden: list[str] = field(default_factory=list)  # removed from "Frequently visited"
    # site permissions: global default per kind (ask/allow/block) and per-origin rules (allow/block)
    permission_defaults: dict[str, str] = field(default_factory=lambda: dict(perms.DEFAULTS))
    site_permissions: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["forget_on_close"] = [rule.to_dict() for rule in self.forget_on_close]
        data["adblock_allowlist"] = list(self.adblock_allowlist)
        data["top_sites_hidden"] = list(self.top_sites_hidden)
        data["permission_defaults"] = dict(self.permission_defaults)
        data["site_permissions"] = {origin: dict(rules) for origin, rules in self.site_permissions.items()}
        return data


# Keys that may be changed with SettingsManager.set(); site rules have dedicated methods.
_LIST_KEYS = {"forget_on_close", "adblock_allowlist", "top_sites_hidden", "permission_defaults", "site_permissions"}
_SIMPLE_KEYS = {f.name for f in dataclasses.fields(Settings)} - _LIST_KEYS


class SettingsError(ValueError):
    """Invalid settings value."""


def _validate(key: str, value: Any) -> Any:
    if key not in _SIMPLE_KEYS:
        raise SettingsError(f"unknown setting: {key}")
    default = getattr(Settings(), key)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise SettingsError(f"{key} must be true or false")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise SettingsError(f"{key} must be a number")
        try:
            number = int(value)
        except ValueError:
            raise SettingsError(f"{key} must be a number") from None
        low, high = INT_RANGES[key]
        if not low <= number <= high:
            raise SettingsError(f"{key} must be between {low} and {high}")
        return number
    if not isinstance(value, str):
        raise SettingsError(f"{key} must be a string")
    value = value.strip()
    if key == "search_engine" and value not in ENGINES:
        raise SettingsError(f"unknown search engine: {value}")
    if key == "theme" and value not in THEMES:
        raise SettingsError(f"unknown theme: {value}")
    if key == "accent_mode" and value not in ACCENT_MODES:
        raise SettingsError(f"unknown accent mode: {value}")
    if key == "accent_color":
        value = value.lower()
        if not _HEX_COLOR.match(value):
            raise SettingsError("color must look like #3b82f6")
    if key == "language" and value not in LANGUAGE_CODES:
        raise SettingsError(f"unknown language: {value}")
    if key == "homepage":
        if not value:
            value = "lilx://home"
        if not value.startswith(("http://", "https://", "lilx://")):
            raise SettingsError("homepage must start with https://, http:// or lilx://")
    if key == "download_dir" and value:
        path = Path(value).expanduser()
        if not path.is_dir():
            raise SettingsError(f"folder does not exist: {path}")
        value = str(path.resolve())
    return value


class SettingsManager(QObject):
    """Owns the current :class:`Settings` and persists every change.

    ``changed`` is emitted with the name of the changed key
    (``"forget_on_close"`` for site rule changes).
    """

    changed = Signal(str)

    def __init__(self, storage: StorageBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._storage = storage
        self._settings = self._load()

    @property
    def current(self) -> Settings:
        return self._settings

    def _load(self) -> Settings:
        try:
            data = self._storage.read_json(SETTINGS_DOCUMENT)
        except StorageError as exc:
            log.error("Settings are unreadable, using defaults: %s", exc)
            self._backup_corrupted()
            return Settings()
        if not isinstance(data, dict):
            return Settings()

        settings = Settings()
        for key in _SIMPLE_KEYS:
            if key in data:
                try:
                    setattr(settings, key, _validate(key, data[key]))
                except SettingsError as exc:
                    log.warning("Ignoring stored setting %s: %s", key, exc)
        rules = data.get("forget_on_close", [])
        if isinstance(rules, list):
            parsed = (SiteRule.from_dict(r) for r in rules if isinstance(r, dict))
            settings.forget_on_close = [r for r in parsed if r is not None]
        allowlist = data.get("adblock_allowlist", [])
        if isinstance(allowlist, list):
            hosts = (normalize_host(h) for h in allowlist if isinstance(h, str))
            settings.adblock_allowlist = sorted({h for h in hosts if h})
        hidden = data.get("top_sites_hidden", [])
        if isinstance(hidden, list):
            settings.top_sites_hidden = sorted({h.strip().lower() for h in hidden if isinstance(h, str) and h.strip()})
        # Older settings files have no permission fields: the safe defaults (ask) apply.
        settings.permission_defaults = perms.clean_defaults(data.get("permission_defaults"))
        settings.site_permissions = perms.clean_site_rules(data.get("site_permissions"))
        return settings

    def _backup_corrupted(self) -> None:
        """Keep the unreadable file for inspection instead of silently overwriting it."""
        try:
            raw = self._storage.read(SETTINGS_DOCUMENT)
            if raw is not None:
                self._storage.write(f"{SETTINGS_DOCUMENT}.corrupted", raw)
        except StorageError as exc:
            log.error("Could not back up corrupted settings: %s", exc)

    def save(self) -> None:
        try:
            self._storage.write_json(SETTINGS_DOCUMENT, self._settings.to_dict())
        except StorageError as exc:
            log.error("Could not save settings: %s", exc)

    def set(self, key: str, value: Any) -> None:
        value = _validate(key, value)
        if getattr(self._settings, key) == value:
            return
        setattr(self._settings, key, value)
        self.save()
        self.changed.emit(key)

    def set_site_rule(self, rule: SiteRule) -> None:
        rules = [r for r in self._settings.forget_on_close if r.host != rule.host]
        rules.append(rule)
        rules.sort(key=lambda r: r.host)
        self._settings.forget_on_close = rules
        self.save()
        self.changed.emit("forget_on_close")

    def remove_site_rule(self, host: str) -> None:
        host = normalize_host(host)
        rules = [r for r in self._settings.forget_on_close if r.host != host]
        if len(rules) != len(self._settings.forget_on_close):
            self._settings.forget_on_close = rules
            self.save()
            self.changed.emit("forget_on_close")

    # -- lilBlock exceptions -------------------------------------------------------
    def allow_ads(self, host: str) -> str:
        """Turn lilBlock off for ``host``. Returns the normalized host."""
        host = normalize_host(host)
        if not host:
            raise SettingsError("enter a site like example.com")
        if host not in self._settings.adblock_allowlist:
            self._settings.adblock_allowlist = sorted([*self._settings.adblock_allowlist, host])
            self.save()
            self.changed.emit("adblock_allowlist")
        return host

    def block_ads(self, host: str) -> None:
        """Turn lilBlock back on for ``host``."""
        host = normalize_host(host)
        if host in self._settings.adblock_allowlist:
            self._settings.adblock_allowlist = [h for h in self._settings.adblock_allowlist if h != host]
            self.save()
            self.changed.emit("adblock_allowlist")

    def clear_allowlist(self) -> None:
        if self._settings.adblock_allowlist:
            self._settings.adblock_allowlist = []
            self.save()
            self.changed.emit("adblock_allowlist")

    # -- "Frequently visited" on the home page ---------------------------------------
    def hide_top_site(self, host: str) -> None:
        """Keep ``host`` out of "Frequently visited" (its history stays untouched)."""
        host = host.strip().lower()
        if host and host not in self._settings.top_sites_hidden:
            self._settings.top_sites_hidden = sorted([*self._settings.top_sites_hidden, host])
            self.save()
            self.changed.emit("top_sites_hidden")

    def restore_top_sites(self) -> None:
        if self._settings.top_sites_hidden:
            self._settings.top_sites_hidden = []
            self.save()
            self.changed.emit("top_sites_hidden")

    # -- site permissions ---------------------------------------------------------------
    def set_permission_default(self, kind: str, value: str) -> None:
        perms.validate_default(kind, value)
        if self._settings.permission_defaults.get(kind) != value:
            self._settings.permission_defaults = {**self._settings.permission_defaults, kind: value}
            self.save()
            self.changed.emit("permissions")

    def set_site_permission(self, origin: str, kind: str, value: str) -> str:
        """Persistent rule for one origin. Returns the normalized origin."""
        perms.validate_site_rule(kind, value)
        origin = perms.normalize_origin(origin)
        rules = {o: dict(r) for o, r in self._settings.site_permissions.items()}
        rules.setdefault(origin, {})[kind] = value
        self._settings.site_permissions = rules
        self.save()
        self.changed.emit("permissions")
        return origin

    def remove_site_permission(self, origin: str, kind: str) -> None:
        origin = perms.normalize_origin(origin)
        rules = {o: dict(r) for o, r in self._settings.site_permissions.items()}
        if kind in rules.get(origin, {}):
            del rules[origin][kind]
            if not rules[origin]:
                del rules[origin]
            self._settings.site_permissions = rules
            self.save()
            self.changed.emit("permissions")

    def clear_site_permissions(self, origin: str) -> None:
        origin = perms.normalize_origin(origin)
        if origin in self._settings.site_permissions:
            self._settings.site_permissions = {o: r for o, r in self._settings.site_permissions.items() if o != origin}
            self.save()
            self.changed.emit("permissions")

    def reset_site_permissions(self) -> None:
        if self._settings.site_permissions:
            self._settings.site_permissions = {}
            self.save()
            self.changed.emit("permissions")
