"""The popup of an extension's toolbar action (manifest "action.default_popup").

The page is the one Qt reports in QWebEngineExtensionInfo.actionPopupUrl(), loaded in a
QWebEngineView on the *same* profile as the tabs, so the extension's own chrome.* APIs
(provided by Qt WebEngine) work inside it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QSize, QSizeF, Qt, QUrl
from PySide6.QtGui import QScreen
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

# Chrome's limits for action popups.
_MIN = QSize(25, 25)
_MAX = QSize(800, 600)
_INITIAL = QSize(320, 220)

NewTabOpener = Callable[[], "QWebEnginePage | None"]


class _PopupPage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, open_tab: NewTabOpener, parent: QWidget) -> None:
        super().__init__(profile, parent)
        self._open_tab = open_tab

    def createWindow(self, window_type: QWebEnginePage.WebWindowType) -> QWebEnginePage | None:  # noqa: N802
        # Links with target=_blank / window.open() from a popup open as normal tabs.
        return self._open_tab()


class ExtensionPopup(QFrame):
    def __init__(self, profile: QWebEngineProfile, url: QUrl, open_tab: NewTabOpener, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("extensionPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._anchor = QPoint()
        self.view = QWebEngineView(self)
        page = _PopupPage(profile, open_tab, self.view)
        self.view.setPage(page)
        page.contentsSizeChanged.connect(self._fit)
        page.windowCloseRequested.connect(self.close)  # window.close() in the popup
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self.view)
        self.resize(_INITIAL)
        self.view.load(url)

    def show_below(self, anchor: QWidget) -> None:
        """Right-aligned under the toolbar button, kept on screen."""
        self._anchor = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 4))
        self._place()
        self.show()
        self.view.setFocus()

    def _fit(self, size: QSizeF) -> None:
        wanted = QSize(int(size.width()) + 2, int(size.height()) + 2)
        self.resize(wanted.expandedTo(_MIN).boundedTo(_MAX))
        self._place()

    def _place(self) -> None:
        if self._anchor.isNull():
            return
        x = self._anchor.x() - self.width()
        y = self._anchor.y()
        screen: QScreen | None = self.screen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
            y = max(area.top() + 4, min(y, area.bottom() - self.height() - 4))
        self.move(x, y)
