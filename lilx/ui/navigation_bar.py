"""Navigation bar: back / forward / reload / home, the address bar, lilBlock and the menu."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QMenu, QToolButton, QWidget

from lilx.i18n import tr
from lilx.ui import icons
from lilx.ui.animations import AnimatedButton
from lilx.ui.theme import Palette


class AddressBar(QLineEdit):
    """Line edit that selects everything on focus and restores the URL on Escape."""

    submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("addressBar")
        self.setClearButtonEnabled(False)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self._current_url = ""
        self._select_on_click = False
        self.returnPressed.connect(self._on_return)

    def show_url(self, text: str) -> None:
        self._current_url = text
        if not self.hasFocus() or not self.isModified():
            self.setText(text)
            self.setCursorPosition(0)
            self.setModified(False)

    def _on_return(self) -> None:
        text = self.text().strip()
        if text:
            self.setModified(False)
            self.submitted.emit(text)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 (Qt API)
        super().focusInEvent(event)
        # Select after Qt has processed the click that gave focus.
        self._select_on_click = event.reason() == Qt.FocusReason.MouseFocusReason
        QTimer.singleShot(0, self.selectAll)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt API)
        super().mouseReleaseEvent(event)
        if self._select_on_click and not self.hasSelectedText():
            self.selectAll()
        self._select_on_click = False

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 (Qt API)
        super().focusOutEvent(event)
        if not self.isModified():
            self.setText(self._current_url)
        self.setCursorPosition(0)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        if event.key() == Qt.Key.Key_Escape:
            self.setText(self._current_url)
            self.setModified(False)
            self.selectAll()
            return
        super().keyPressEvent(event)


class NavigationBar(QWidget):
    back_clicked = Signal()
    forward_clicked = Signal()
    reload_clicked = Signal()
    stop_clicked = Signal()
    home_clicked = Signal()
    downloads_clicked = Signal()
    lilblock_clicked = Signal()
    bookmarks_clicked = Signal()
    extensions_clicked = Signal()

    def __init__(self, menu: QMenu, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._palette: Palette | None = None
        self._loading = False
        self._lilblock_state: tuple[bool, int] = (True, 0)
        self._bookmarked = False

        self.back = self._button(self.back_clicked)
        self.forward = self._button(self.forward_clicked)
        self.reload = self._button(self._on_reload)
        self.home = self._button(self.home_clicked)
        self.address = AddressBar(self)
        # Shown only in private windows, left of the address bar.
        self.private_badge = QToolButton(self)
        self.private_badge.setObjectName("privateBadge")
        self.private_badge.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.private_badge.setIconSize(QSize(16, 16))
        self.private_badge.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.private_badge.hide()
        self.lilblock = self._button(self.lilblock_clicked)
        self.lilblock.setObjectName("lilblockButton")
        self.lilblock.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.extensions = self._button(self.extensions_clicked)
        self.extensions.setObjectName("extensionsButton")
        self.bookmarks = self._button(self.bookmarks_clicked)
        self.bookmarks.setObjectName("bookmarksButton")
        self.downloads = self._button(self.downloads_clicked)
        self.downloads.setObjectName("downloadsButton")
        self.menu_button = self._button(None)
        self.menu_button.setMenu(menu)
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        for button in (self.back, self.forward, self.reload, self.home):
            layout.addWidget(button)
        layout.addSpacing(6)
        layout.addWidget(self.private_badge)
        layout.addWidget(self.address, 1)
        layout.addSpacing(4)
        layout.addWidget(self.lilblock)
        layout.addSpacing(2)
        layout.addWidget(self.extensions)
        layout.addWidget(self.bookmarks)
        layout.addWidget(self.downloads)
        layout.addWidget(self.menu_button)
        self.retranslate()

    def _button(self, signal) -> AnimatedButton:
        button = AnimatedButton(self)
        button.setIconSize(QSize(18, 18))
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if signal is not None:
            button.clicked.connect(signal)
        return button

    def retranslate(self) -> None:
        self.back.setToolTip(tr("Back"))
        self.forward.setToolTip(tr("Forward"))
        self.reload.setToolTip(tr("Stop") if self._loading else tr("Reload"))
        self.home.setToolTip(tr("Home"))
        self.downloads.setToolTip(tr("Downloads"))
        self.bookmarks.setToolTip(tr("Bookmarks"))
        self.extensions.setToolTip(tr("Extensions"))
        self.private_badge.setText(tr("Private"))
        self.private_badge.setToolTip(tr("Private window: history, cookies and site data are not saved"))
        self.menu_button.setToolTip(tr("Menu"))
        self.address.setPlaceholderText(tr("Search or enter address"))
        self.set_lilblock_state(*self._lilblock_state)

    def _on_reload(self) -> None:
        (self.stop_clicked if self._loading else self.reload_clicked).emit()

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        for button in (self.back, self.forward, self.reload, self.home, self.lilblock,
                       self.extensions, self.bookmarks, self.downloads, self.menu_button):
            button.set_colors(palette.hover, palette.border)
        color = palette.text
        self.back.setIcon(icons.icon("back", color))
        self.forward.setIcon(icons.icon("forward", color))
        self.home.setIcon(icons.icon("home", color))
        self.menu_button.setIcon(icons.icon("menu", color))
        self.extensions.setIcon(icons.icon("puzzle", color))
        self.private_badge.setIcon(icons.icon("private", palette.accent))
        self._update_reload_icon()
        self.set_downloads_active(self.downloads.property("active") is True)
        self.set_lilblock_state(*self._lilblock_state)
        self.set_bookmarked(self._bookmarked)

    def set_private(self, private: bool) -> None:
        self.private_badge.setVisible(private)

    def set_bookmarked(self, bookmarked: bool) -> None:
        """Filled accent star when the current page is in the bookmarks."""
        self._bookmarked = bookmarked
        if self._palette is not None:
            name, color = ("star-filled", self._palette.accent) if bookmarked else ("star", self._palette.text)
            self.bookmarks.setIcon(icons.icon(name, color))

    def set_loading(self, loading: bool) -> None:
        self._loading = loading
        self.reload.setToolTip(tr("Stop") if loading else tr("Reload"))
        self._update_reload_icon()

    def _update_reload_icon(self) -> None:
        if self._palette is not None:
            self.reload.setIcon(icons.icon("stop" if self._loading else "reload", self._palette.text))

    def set_navigation_state(self, can_back: bool, can_forward: bool) -> None:
        self.back.setEnabled(can_back)
        self.forward.setEnabled(can_forward)
        if self._palette is not None:
            for button, name, enabled in ((self.back, "back", can_back), (self.forward, "forward", can_forward)):
                button.setIcon(icons.icon(name, self._palette.text if enabled else self._palette.border))

    def set_downloads_active(self, active: bool) -> None:
        self.downloads.setProperty("active", active)
        self.downloads.style().unpolish(self.downloads)
        self.downloads.style().polish(self.downloads)
        if self._palette is not None:
            color = self._palette.accent if active else self._palette.text
            self.downloads.setIcon(icons.icon("download", color))

    def set_lilblock_state(self, active: bool, count: int, tooltip: str = "") -> None:
        """active: lilBlock works on the current page; count: requests blocked on it."""
        self._lilblock_state = (active, count)
        self.lilblock.setText(str(count) if active and count else "")
        self.lilblock.setProperty("off", not active)
        self.lilblock.style().unpolish(self.lilblock)
        self.lilblock.style().polish(self.lilblock)
        if tooltip:
            self.lilblock.setToolTip(tooltip)
        if self._palette is not None:
            name, color = ("shield", self._palette.accent) if active else ("shield-off", self._palette.muted)
            self.lilblock.setIcon(icons.icon(name, color))
