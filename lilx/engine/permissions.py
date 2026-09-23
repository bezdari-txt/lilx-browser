"""Site permission requests from QtWebEngine, decided by lilx's own rules.

Profiles use ``PersistentPermissionsPolicy.AskEveryTime``, so Qt stores nothing in the
profile; this service answers each ``QWebEnginePermission`` from the rules in settings
(see :mod:`lilx.core.permissions`) or leaves it pending for the in-window prompt.

Chromium still remembers an answer *within a tab* (for the same origin) until the
permission is ``reset()``. Pages therefore reset every answer they gave when they
navigate and whenever the rules change (``rules_changed``), so lilx stays the only
source of decisions: a new global Block applies at once, also to open tabs. Decisions remembered in a private window live only in memory and are dropped
when the private session ends; nothing about sites visited privately reaches disk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal
from PySide6.QtWebEngineCore import QWebEnginePermission

from lilx.core import permissions as perms
from lilx.core.settings import SettingsManager

log = logging.getLogger(__name__)

_Type = QWebEnginePermission.PermissionType
# Qt has a single clipboard permission (read and write together); lilx maps it to "clipboard".
TYPE_KINDS: dict[QWebEnginePermission.PermissionType, tuple[str, ...]] = {
    _Type.MediaVideoCapture: ("camera",),
    _Type.MediaAudioCapture: ("microphone",),
    _Type.MediaAudioVideoCapture: ("camera", "microphone"),
    _Type.Geolocation: ("geolocation",),
    _Type.Notifications: ("notifications",),
    _Type.ClipboardReadWrite: ("clipboard",),
}


@dataclass(eq=False)
class PermissionRequest:
    permission: QWebEnginePermission
    kinds: tuple[str, ...]
    origin: str
    private: bool
    answered: bool = field(default=False)


class PermissionService(QObject):
    #: global defaults or site rules changed: pages drop the answers Chromium cached
    rules_changed = Signal()

    def __init__(self, settings: SettingsManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._private_rules: dict[str, dict[str, str]] = {}  # memory only, per private session
        settings.changed.connect(lambda key: self.rules_changed.emit() if key == "permissions" else None)

    # -- rules -------------------------------------------------------------------------
    def site_rules(self, private: bool) -> dict[str, dict[str, str]]:
        """Persistent rules; a private session adds its in-memory rules on top."""
        rules = {origin: dict(r) for origin, r in self._settings.current.site_permissions.items()}
        if private:
            for origin, extra in self._private_rules.items():
                rules.setdefault(origin, {}).update(extra)
        return rules

    def decide(self, kinds: tuple[str, ...], origin: str, private: bool) -> str:
        return perms.resolve_many(kinds, origin, self._settings.current.permission_defaults, self.site_rules(private))

    def clear_private(self) -> None:
        """End of the private session: forget every decision made in it."""
        self._private_rules.clear()
        self.rules_changed.emit()

    # -- QtWebEngine requests ----------------------------------------------------------------
    def evaluate(self, permission: QWebEnginePermission, private: bool) -> tuple[str, PermissionRequest | None]:
        """Returns ("allow" | "block" | "ask", request). Unsupported kinds are blocked."""
        kinds = TYPE_KINDS.get(permission.permissionType())
        if kinds is None:
            return "block", None  # screen capture, pointer lock, local fonts, ...: not offered yet
        try:
            origin = perms.normalize_origin(permission.origin().toString())
        except perms.PermissionRuleError:
            return "block", None  # only http(s) sites can be granted anything
        decision = self.decide(kinds, origin, private)
        return decision, PermissionRequest(permission, kinds, origin, private)

    def apply(self, permission: QWebEnginePermission, decision: str) -> None:
        if not permission.isValid():
            return
        if decision == "allow":
            permission.grant()
        else:
            permission.deny()

    def answer(self, request: PermissionRequest, allow: bool, remember: bool) -> None:
        """The user's answer in the prompt; ``remember`` stores it for this origin."""
        request.answered = True
        # The answer is re-checked: a global "block" set meanwhile still wins.
        decision = "allow" if allow else "block"
        if allow and self.decide(request.kinds, request.origin, request.private) == "block":
            decision = "block"
        self.apply(request.permission, decision)
        if not remember:
            return
        value = "allow" if allow else "block"
        for kind in request.kinds:
            if request.private:
                self._private_rules.setdefault(request.origin, {})[kind] = value
                self.rules_changed.emit()
            else:
                self._settings.set_site_permission(request.origin, kind, value)
        log.info("Permission %s for %s: %s%s", "+".join(request.kinds), request.origin, value,
                 " (private session)" if request.private else "")
