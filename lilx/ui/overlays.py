"""Small floating labels over the page: hovered-link preview and toasts."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

from lilx.ui import animations

_MARGIN = 8


class OverlayLabel(QLabel):
    def __init__(self, host: QWidget, object_name: str, *, align_right: bool = False) -> None:
        super().__init__(host)
        self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self._align_right = align_right
        self._hide_timer = QTimer(self, singleShot=True, timeout=lambda: animations.fade(self, False))
        host.installEventFilter(self)
        self.hide()

    def show_text(self, text: str, timeout_ms: int = 0) -> None:
        if not text:
            if self.isVisible():
                animations.fade(self, False, animations.FAST_MS)
            return
        max_width = max(self.parentWidget().width() // 2, 200)
        self.setText(self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, max_width))
        self.adjustSize()
        self._reposition()
        self.raise_()
        animations.fade(self, True, animations.FAST_MS)
        if timeout_ms:
            self._hide_timer.start(timeout_ms)
        else:
            self._hide_timer.stop()

    def _reposition(self) -> None:
        host = self.parentWidget()
        y = host.height() - self.height() - _MARGIN
        x = host.width() - self.width() - _MARGIN if self._align_right else _MARGIN
        self.move(max(x, 0), max(y, 0))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.Resize and self.isVisible():
            self._reposition()
        return False
