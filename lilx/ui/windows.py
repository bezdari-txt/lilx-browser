"""Browser windows: normal windows share the persistent profile, private windows share
one off-the-record profile that lives only while at least one private window is open.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEngineProfile

from lilx.context import BrowserContext
from lilx.engine.lilblock import ProfileBlocker
from lilx.engine.profile import create_private_profile
from lilx.engine.scheme import SCHEME_BYTES, internal_url

if TYPE_CHECKING:
    from lilx.ui.main_window import MainWindow

log = logging.getLogger(__name__)


class PrivateSession(QObject):
    """One off-the-record QWebEngineProfile and everything wired to it.

    Nothing of it is written to disk: Qt keeps cookies, storage, cache and visited links
    in memory; lilx keeps private permission decisions and download entries in memory.
    """

    def __init__(self, ctx: BrowserContext) -> None:
        super().__init__()
        self._ctx = ctx
        self.profile: QWebEngineProfile = create_private_profile(ctx.settings)
        # lilBlock blocks as usual but writes nothing to its saved history.
        self.profile.setUrlRequestInterceptor(ProfileBlocker(ctx.lilblock, self.profile, record=False))
        self.profile.downloadRequested.connect(ctx.downloads.handle_private_request)
        if ctx.scheme_handler_factory is not None:
            handler = ctx.scheme_handler_factory(True, self.profile)
            self.profile.installUrlSchemeHandler(SCHEME_BYTES, handler)
        log.info("Private session started")

    def close(self) -> None:
        """Delete the profile (all its pages must already be gone) and forget the session."""
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        if shiboken6.isValid(self.profile):
            shiboken6.delete(self.profile)
        self._ctx.permissions.clear_private()
        self._ctx.downloads.forget_private()
        log.info("Private session ended")


class WindowManager(QObject):
    def __init__(self, ctx: BrowserContext) -> None:
        super().__init__()
        self._ctx = ctx
        self.windows: list[MainWindow] = []
        self._private: PrivateSession | None = None

    # -- windows ---------------------------------------------------------------------------
    def create_window(self, private: bool = False) -> MainWindow:
        """A new, not yet shown window without tabs."""
        from lilx.ui.main_window import MainWindow

        profile = self.private_profile() if private else self._ctx.profile
        window = MainWindow(self._ctx, private=private, profile=profile, manager=self)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.destroyed.connect(lambda *_: self._on_destroyed(window))
        self.windows.append(window)
        return window

    def open_window(self, private: bool = False, url: QUrl | None = None) -> MainWindow:
        window = self.create_window(private)
        if url is None:
            url = internal_url("home") if private else QUrl(self._ctx.settings.current.homepage)
        window.new_tab(url)
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def private_windows(self) -> list[MainWindow]:
        return [w for w in self.windows if w.private and shiboken6.isValid(w)]

    def private_profile(self) -> QWebEngineProfile:
        if self._private is None:
            self._private = PrivateSession(self._ctx)
        return self._private.profile

    @property
    def private_session_active(self) -> bool:
        return self._private is not None

    def _on_destroyed(self, window: MainWindow) -> None:
        self.windows = [w for w in self.windows if w is not window]
        if self._private is not None and not self.private_windows():
            # After the window's destruction has finished (its pages are gone), end the session.
            QTimer.singleShot(0, self._end_private_session)

    def _end_private_session(self) -> None:
        if self._private is not None and not self.private_windows():
            session, self._private = self._private, None
            session.close()
            session.deleteLater()

    # -- application end -------------------------------------------------------------------------
    def delete_all(self) -> None:
        """Delete every window and the private session (before the normal profile goes)."""
        for window in list(self.windows):
            if shiboken6.isValid(window):
                shiboken6.delete(window)
        self.windows = []
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        if self._private is not None:
            session, self._private = self._private, None
            session.close()
