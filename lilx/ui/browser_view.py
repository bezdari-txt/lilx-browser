"""One browser tab: a QWebEngineView driving a :class:`BrowserPage`."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget

from lilx.core.settings import SettingsManager
from lilx.engine.lilblock import LilBlock
from lilx.engine.page import BrowserPage, NewWindowHandler
from lilx.engine.scheme import SCHEME
from lilx.i18n import tr

NEW_TAB_TITLE = "New tab"
_ZOOM_STEPS = (0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)


def display_url(url: QUrl) -> str:
    """Text shown in the address bar for ``url`` (empty for the home page)."""
    if url.scheme() == SCHEME:
        if url.host() == "home":
            return ""
        return f"{SCHEME}://{url.host()}"
    if url.isEmpty() or url.toString() == "about:blank":
        return ""
    return url.toDisplayString()


class BrowserView(QWebEngineView):
    def __init__(
        self,
        profile: QWebEngineProfile,
        settings: SettingsManager,
        lilblock: LilBlock,
        new_window: NewWindowHandler,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setPage(BrowserPage(profile, settings, lilblock, new_window, self))
        self.is_loading = False
        self.progress = 0
        self._zoom = 1.0  # wanted zoom factor, re-applied after navigations
        self.loadStarted.connect(self._on_load_started)
        self.loadProgress.connect(self._on_load_progress)
        self.loadFinished.connect(self._on_load_finished)

    def _on_load_started(self) -> None:
        self.is_loading = True
        self.progress = 0

    def _on_load_progress(self, progress: int) -> None:
        self.progress = progress

    def _on_load_finished(self, ok: bool) -> None:
        self.is_loading = False
        self.progress = 100
        if abs(self.zoomFactor() - self._zoom) > 0.001:
            self.setZoomFactor(self._zoom)

    def set_zoom(self, factor: float) -> None:
        self._zoom = factor
        self.setZoomFactor(factor)

    def display_title(self) -> str:
        title = self.title().strip()
        url = self.url()
        if title and title != url.toString() and not title.startswith(f"{SCHEME}://"):
            return title
        if url.scheme() in ("http", "https") and url.host():
            return url.host()
        return tr(NEW_TAB_TITLE)

    @property
    def blocked_count(self) -> int:
        return self.page().blocker.count

    def zoom(self, direction: int, default: float = 1.0) -> None:
        """direction: +1 zoom in, -1 zoom out, 0 back to the default zoom."""
        if direction == 0:
            self.set_zoom(default)
            return
        current = self.zoomFactor()
        if direction > 0:
            candidates = [z for z in _ZOOM_STEPS if z > current + 0.001]
            self.set_zoom(candidates[0] if candidates else _ZOOM_STEPS[-1])
        else:
            candidates = [z for z in _ZOOM_STEPS if z < current - 0.001]
            self.set_zoom(candidates[-1] if candidates else _ZOOM_STEPS[0])
