"""In-window prompt for a site permission request ("example.com wants to use your camera")."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from lilx.engine.permissions import PermissionRequest
from lilx.i18n import tr
from lilx.ui import animations, icons
from lilx.ui.theme import Palette

_TEXTS: dict[tuple[str, ...], str] = {
    ("camera",): "{origin} wants to use your camera",
    ("microphone",): "{origin} wants to use your microphone",
    ("camera", "microphone"): "{origin} wants to use your camera and microphone",
    ("geolocation",): "{origin} wants to know your location",
    ("notifications",): "{origin} wants to show notifications",
    ("clipboard",): "{origin} wants to read and change your clipboard",
}


def request_text(request: PermissionRequest) -> str:
    template = _TEXTS.get(request.kinds, "{origin} wants a permission")
    return tr(template, origin=request.origin)


class PermissionBar(QFrame):
    """Shows the first pending request of the current tab. The origin is always visible."""

    answered = Signal(object, bool, bool)  # request, allow, remember

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("permissionBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._request: PermissionRequest | None = None
        self._icon = QLabel(self)
        self._text = QLabel(self)
        self._text.setObjectName("permissionText")
        self._text.setTextFormat(Qt.TextFormat.PlainText)  # the origin comes from the web
        self._text.setWordWrap(True)
        self._remember = QCheckBox(self)
        self._remember.setChecked(True)
        self._block = QPushButton(self)
        self._block.setObjectName("permissionBlock")
        self._allow = QPushButton(self)
        self._allow.setObjectName("permissionAllow")
        for button in (self._block, self._allow):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._block.clicked.connect(lambda: self._answer(False))
        self._allow.clicked.connect(lambda: self._answer(True))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 10, 8)
        layout.setSpacing(10)
        layout.addWidget(self._icon)
        layout.addWidget(self._text, 1)
        layout.addWidget(self._remember)
        layout.addWidget(self._block)
        layout.addWidget(self._allow)
        self.hide()

    def set_palette(self, palette: Palette) -> None:
        self._icon.setPixmap(icons.icon("shield", palette.accent, 18).pixmap(18, 18))

    def show_request(self, request: PermissionRequest | None) -> None:
        if request is None:
            self._request = None
            if self.isVisible():
                animations.fade(self, False, animations.FAST_MS)
            return
        if request is self._request and self.isVisible():
            return
        self._request = request
        self._text.setText(request_text(request))
        self._remember.setText(tr("Remember until private windows close") if request.private
                               else tr("Remember for this site"))
        self._block.setText(tr("Block"))
        self._allow.setText(tr("Allow"))
        animations.fade(self, True, animations.FAST_MS)

    def current_request(self) -> PermissionRequest | None:
        return self._request if self.isVisible() else None

    def _answer(self, allow: bool) -> None:
        if self._request is not None:
            request, self._request = self._request, None
            self.answered.emit(request, allow, self._remember.isChecked())
