"""Browser extensions (Manifest V3) through Qt's own QWebEngineExtensionManager (Qt ≥ 6.10).

lilx does not implement any extension API itself: installing, loading, enabling and
the chrome.* APIs are all Qt WebEngine's. This module only adds what Qt leaves to the
application (behaviour verified against PySide6 6.11):

* Qt installs an extension (folder or .zip) into ``<profile>/Extensions`` and loads
  installed extensions again on the next start, but always *disabled* and it does not
  remember the enabled state. lilx stores it in ``extensions.json`` and re-applies it
  on ``loadFinished``. A freshly installed extension is enabled.
* ``installFinished`` reports failures as an info with an empty id and ``error()``;
  the source path is not included, so pending installs are tracked in order.
* Installing the same extension again creates a second copy with a new id; lilx
  treats it as an update and uninstalls the older copy (keeping its enabled state).
* ``extensions()`` also lists Qt's built-in components (PDF viewer, ...); only
  installed extensions are shown to the user.

Manifest fields Qt does not expose (version, icons, permissions, options page) are
read from the extension's own ``manifest.json`` for display only.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWebEngineCore import QWebEngineExtensionInfo, QWebEngineProfile

from lilx.core.storage import StorageBackend, StorageError

log = logging.getLogger(__name__)

EXTENSIONS_DOCUMENT = "extensions.json"
_MAX_ERRORS = 10
_ICON_TYPES = {".png": "image/png", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class ExtensionError(ValueError):
    """Message safe to show to the user."""


def read_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads((path / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _icon_file(path: Path, manifest: dict[str, Any], wanted: int = 48) -> Path | None:
    """Best icon near ``wanted`` px from "icons" or action.default_icon."""
    candidates: dict[int, str] = {}
    for source in (manifest.get("icons"), (manifest.get("action") or {}).get("default_icon")):
        if isinstance(source, dict):
            for size, file in source.items():
                if str(size).isdigit() and isinstance(file, str):
                    candidates.setdefault(int(size), file)
        elif isinstance(source, str):
            candidates.setdefault(wanted, source)
    if not candidates:
        return None
    size = min(candidates, key=lambda s: (s < wanted, abs(s - wanted)))
    file = (path / candidates[size].lstrip("/")).resolve()
    # Never read outside the extension's folder.
    return file if file.is_relative_to(path.resolve()) and file.is_file() else None


class ExtensionService(QObject):
    changed = Signal()

    def __init__(self, profile: QWebEngineProfile, storage: StorageBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = profile.extensionManager()
        self._storage = storage
        self._enabled: dict[str, bool] = {}
        self._pending: deque[str] = deque()  # source names of installs in progress, in order
        self._replacing: dict[str, str] = {}  # new id -> old id being replaced
        self.errors: list[dict[str, Any]] = []
        self.last_event: dict[str, Any] = {"seq": 0}
        self._load_state()
        self._manager.installFinished.connect(self._on_install_finished)
        self._manager.loadFinished.connect(self._on_load_finished)
        self._manager.uninstallFinished.connect(self._on_uninstall_finished)
        for info in self.installed():  # already loaded before we connected
            self._apply_enabled(info)

    # -- persistence ---------------------------------------------------------------
    def _load_state(self) -> None:
        try:
            data = self._storage.read_json(EXTENSIONS_DOCUMENT) or {}
        except StorageError as exc:
            log.error("Extension state is unreadable: %s", exc)
            return
        enabled = data.get("enabled", {}) if isinstance(data, dict) else {}
        self._enabled = {str(k): bool(v) for k, v in enabled.items()} if isinstance(enabled, dict) else {}

    def _save_state(self) -> None:
        try:
            self._storage.write_json(EXTENSIONS_DOCUMENT, {"enabled": self._enabled})
        except StorageError as exc:
            log.error("Could not save extension state: %s", exc)

    # -- queries -------------------------------------------------------------------
    @property
    def install_path(self) -> str:
        return self._manager.installPath()

    def installed(self) -> list[QWebEngineExtensionInfo]:
        """Extensions the user installed (Qt's built-in components are left out)."""
        return [info for info in self._manager.extensions() if info.isInstalled()]

    def find(self, extension_id: str) -> QWebEngineExtensionInfo | None:
        return next((i for i in self.installed() if i.id() == extension_id), None)

    def enabled_extensions(self) -> list[QWebEngineExtensionInfo]:
        return sorted((i for i in self.installed() if i.isEnabled()), key=lambda i: i.name().casefold())

    def icon(self, info: QWebEngineExtensionInfo) -> QIcon:
        path = Path(info.path())
        file = _icon_file(path, read_manifest(path), 32)
        return QIcon(QPixmap(str(file))) if file else QIcon()

    def describe(self, info: QWebEngineExtensionInfo) -> dict[str, Any]:
        path = Path(info.path())
        manifest = read_manifest(path)
        icon = _icon_file(path, manifest)
        icon_url = ""
        if icon is not None and icon.suffix.lower() in _ICON_TYPES:
            try:
                icon_url = f"data:{_ICON_TYPES[icon.suffix.lower()]};base64," + base64.b64encode(icon.read_bytes()).decode()
            except OSError:
                pass
        options = (manifest.get("options_ui") or {}).get("page") or manifest.get("options_page") or ""
        permissions = [p for p in manifest.get("permissions", []) if isinstance(p, str)]
        hosts = [h for h in manifest.get("host_permissions", []) if isinstance(h, str)]
        scripts = manifest.get("content_scripts") or []
        for script in scripts if isinstance(scripts, list) else []:
            hosts += [m for m in (script.get("matches") or []) if isinstance(m, str)]
        return {
            "id": info.id(),
            "name": info.name(),
            "description": info.description(),
            "version": str(manifest.get("version", "")),
            "manifest_version": manifest.get("manifest_version"),
            "enabled": info.isEnabled(),
            "loaded": info.isLoaded(),
            "error": info.error(),
            "path": info.path(),
            "popup": info.actionPopupUrl().toString() if info.actionPopupUrl().isValid() else "",
            "options": f"chrome-extension://{info.id()}/{options.lstrip('/')}" if options else "",
            "permissions": permissions,
            "hosts": sorted(set(hosts)),
            "icon": icon_url,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "extensions": [self.describe(i) for i in sorted(self.installed(), key=lambda i: i.name().casefold())],
            "errors": list(self.errors),
            "last_event": dict(self.last_event),
            "install_path": self.install_path,
        }

    # -- actions ---------------------------------------------------------------------
    def install(self, source: str) -> None:
        """Install from an unpacked folder or a .zip (Manifest V3). The result arrives later.

        Problems found before Qt is involved are raised *and* listed with the other
        installation errors, whichever way the install was started.
        """
        path = Path(source).expanduser()
        try:
            self._check_source(path)
        except ExtensionError as exc:
            self._record_error(path.name, str(exc))
            raise
        self._pending.append(path.name)
        log.info("Installing extension from %s", path)
        self._manager.installExtension(str(path))

    def _record_error(self, source: str, message: str) -> None:
        log.warning("Extension install failed (%s): %s", source, message)
        self.errors.insert(0, {"time": time.time(), "source": source, "message": message})
        del self.errors[_MAX_ERRORS:]
        self._event("error", source)
        self.changed.emit()

    @staticmethod
    def _check_source(path: Path) -> None:
        if not path.exists():
            raise ExtensionError(f"not found: {path}")
        if path.is_dir() and not (path / "manifest.json").is_file():
            raise ExtensionError("this folder has no manifest.json")
        if path.is_file() and path.suffix.lower() != ".zip":
            raise ExtensionError("choose a .zip file or an unpacked extension folder (.crx is not supported)")
        if path.is_dir():
            version = read_manifest(path).get("manifest_version")
            if version != 3:
                raise ExtensionError(f"only Manifest V3 extensions are supported (this one is v{version})")

    def set_enabled(self, extension_id: str, enabled: bool) -> None:
        info = self.find(extension_id)
        if info is None:
            raise ExtensionError("extension not found")
        self._manager.setExtensionEnabled(info, enabled)
        self._enabled[extension_id] = enabled
        self._save_state()
        self.changed.emit()

    def uninstall(self, extension_id: str) -> None:
        info = self.find(extension_id)
        if info is None:
            raise ExtensionError("extension not found")
        self._manager.uninstallExtension(info)

    def dismiss_errors(self) -> None:
        self.errors.clear()
        self.changed.emit()

    # -- Qt signals --------------------------------------------------------------------
    def _event(self, kind: str, name: str) -> None:
        self.last_event = {"seq": self.last_event["seq"] + 1, "kind": kind, "name": name}

    def _on_install_finished(self, info: QWebEngineExtensionInfo) -> None:
        source = self._pending.popleft() if self._pending else ""
        if not info.id() or info.error():
            self._record_error(source, info.error() or "unknown error")
            return
        version = read_manifest(Path(info.path())).get("manifest_version")
        if version != 3:  # a .zip can only be checked once Qt has unpacked it
            self._replacing[info.id()] = info.id()  # silent removal, not an "uninstalled" event
            self._manager.uninstallExtension(info)
            self._record_error(source, f"only Manifest V3 extensions are supported (this one is v{version})")
            return
        # Same extension installed again: treat it as an update of the older copy.
        older = [i for i in self.installed() if i.id() != info.id() and i.name() == info.name()]
        enabled = True
        for old in older:
            enabled = self._enabled.pop(old.id(), True)
            self._replacing[info.id()] = old.id()
            self._manager.uninstallExtension(old)
        self._enabled[info.id()] = enabled
        self._manager.setExtensionEnabled(info, enabled)
        self._save_state()
        self._event("updated" if older else "installed", info.name())
        log.info("Extension installed: %s (%s)", info.name(), info.id())
        self.changed.emit()

    def _on_load_finished(self, info: QWebEngineExtensionInfo) -> None:
        if info.isInstalled():
            self._apply_enabled(info)
            self.changed.emit()

    def _apply_enabled(self, info: QWebEngineExtensionInfo) -> None:
        wanted = self._enabled.get(info.id(), True)
        if info.isEnabled() != wanted:
            self._manager.setExtensionEnabled(info, wanted)

    def _on_uninstall_finished(self, info: QWebEngineExtensionInfo) -> None:
        if info.error():
            self.errors.insert(0, {"time": time.time(), "source": info.name(), "message": info.error()})
        if info.id() not in self._replacing.values():
            self._event("uninstalled", info.name())
        self._enabled.pop(info.id(), None)
        self._replacing = {k: v for k, v in self._replacing.items() if v != info.id()}
        self._save_state()
        self.changed.emit()
