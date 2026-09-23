"""QWebEnginePage subclass with lilx policies (popups, permissions, JS toggle)."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEnginePermission, QWebEngineProfile, QWebEngineSettings

from lilx.core.settings import SettingsManager
from lilx.engine.lilblock import SCRIPT_PREFIX, LilBlock, PageBlocker
from lilx.engine.scheme import is_internal

log = logging.getLogger(__name__)

# Called with (opener page, window type); returns the page that should host the new window.
NewWindowHandler = Callable[[QWebEnginePage, QWebEnginePage.WebWindowType], "QWebEnginePage | None"]


class BrowserPage(QWebEnginePage):
    def __init__(
        self,
        profile: QWebEngineProfile,
        settings: SettingsManager,
        lilblock: LilBlock,
        new_window: NewWindowHandler,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(profile, parent)
        self._settings = settings
        self._new_window = new_window
        self._lilblock = lilblock
        # Replaces the profile interceptor for this page's requests (it adds the privacy headers too).
        self.blocker = PageBlocker(lilblock, self)
        self.setUrlRequestInterceptor(self.blocker)
        self._install_scriptlets()
        lilblock.changed.connect(self._install_scriptlets)
        self.loadStarted.connect(lambda: self.blocker.reset(self.requestedUrl().host()))
        self.permissionRequested.connect(self._on_permission_requested)

    def createWindow(self, window_type: QWebEnginePage.WebWindowType) -> QWebEnginePage | None:  # noqa: N802
        # target=_blank links and window.open() open as tabs.
        return self._new_window(self, window_type)

    def acceptNavigationRequest(  # noqa: N802
        self, url: QUrl, nav_type: QWebEnginePage.NavigationType, is_main_frame: bool
    ) -> bool:
        if is_internal(url) and not self._may_open_internal(nav_type, is_main_frame):
            log.warning("Blocked navigation to %s from %s", url.toString(), self.url().toString())
            return False
        if is_main_frame:
            # Internal pages always need JavaScript, even if the user disabled it for the web.
            # Extension pages (options, tabs opened by an extension) are part of the extension.
            enabled = is_internal(url) or url.scheme() == "chrome-extension" or self._settings.current.javascript_enabled
            self.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, enabled)
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def _install_scriptlets(self) -> None:
        """(Re)install lilBlock page scripts; they take effect from the next navigation."""
        scripts = self.scripts()
        for script in scripts.toList():
            if script.name().startswith(SCRIPT_PREFIX):
                scripts.remove(script)
        scripts.insert(self._lilblock.page_scripts())

    def _may_open_internal(self, nav_type: QWebEnginePage.NavigationType, is_main_frame: bool) -> bool:
        """lilx:// pages may be opened by the user or by other lilx:// pages, never by websites."""
        if not is_main_frame:
            return False
        if nav_type in (QWebEnginePage.NavigationType.NavigationTypeTyped,
                        QWebEnginePage.NavigationType.NavigationTypeBackForward,
                        QWebEnginePage.NavigationType.NavigationTypeReload):
            return True
        current = self.url()
        return is_internal(current)

    def _on_permission_requested(self, permission: QWebEnginePermission) -> None:
        # Privacy-first default until a permission prompt UI exists: deny everything.
        log.info("Denied %s permission for %s", permission.permissionType().name, permission.origin().toString())
        permission.deny()

    def javaScriptConsoleMessage(  # noqa: N802
        self, level: QWebEnginePage.JavaScriptConsoleMessageLevel, message: str, line: int, source: str
    ) -> None:
        if source.startswith("lilx:"):
            is_error = level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel
            log.log(logging.WARNING if is_error else logging.DEBUG, "[%s:%d] %s", source, line, message)
