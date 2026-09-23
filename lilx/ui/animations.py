"""Small, cheap UI animations for the browser chrome.

Qt stylesheets cannot animate, so the few animated pieces are done with
QVariantAnimation / QPropertyAnimation. Durations stay short (≤ 180 ms) and
nothing animates the web view itself, so the browser never feels slower.
Set ``LILX_NO_ANIMATIONS=1`` to turn them off (used by automated tests).
"""

from __future__ import annotations

import os

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QShowEvent
from PySide6.QtWidgets import QGraphicsOpacityEffect, QMenu, QToolButton, QWidget

FAST_MS = 120
NORMAL_MS = 170
EASING = QEasingCurve.Type.OutCubic


def enabled() -> bool:
    return not os.environ.get("LILX_NO_ANIMATIONS")


class AnimatedButton(QToolButton):
    """Tool button whose hover and press background fades in and out."""

    def __init__(self, parent: QWidget | None = None, radius: float = 8.0) -> None:
        super().__init__(parent)
        self.setProperty("animated", True)  # the stylesheet leaves hover painting to us
        self._radius = radius
        self._level = 0.0
        self._hover = QColor(0, 0, 0, 20)
        self._pressed = QColor(0, 0, 0, 36)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(FAST_MS)
        self._animation.setEasingCurve(EASING)
        self._animation.valueChanged.connect(self._set_level)

    def set_colors(self, hover: str, pressed: str) -> None:
        self._hover = QColor(hover)
        self._pressed = QColor(pressed)
        self.update()

    def _set_level(self, value: float) -> None:
        self._level = float(value)
        self.update()

    def _animate_to(self, target: float) -> None:
        if not enabled():
            self._set_level(target)
            return
        self._animation.stop()
        self._animation.setStartValue(self._level)
        self._animation.setEndValue(target)
        self._animation.start()

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        if self.isEnabled():
            self._animate_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        self._animate_to(0.0)
        super().leaveEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._animation.stop()
            self._set_level(0.0)
        super().changeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt API)
        level = 1.0 if self.isDown() else self._level
        if level > 0.01:
            color = QColor(self._pressed if self.isDown() else self._hover)
            color.setAlphaF(color.alphaF() * level)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(self.rect(), self._radius, self._radius)
            painter.end()
        super().paintEvent(event)


class AnimatedMenu(QMenu):
    """Menu that fades in while sliding a few pixels into place."""

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        if not enabled():
            return
        end = self.pos()
        opacity = QPropertyAnimation(self, b"windowOpacity", self)
        opacity.setDuration(FAST_MS)
        opacity.setStartValue(0.0)
        opacity.setEndValue(1.0)
        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(NORMAL_MS)
        slide.setEasingCurve(EASING)
        slide.setStartValue(end - QPoint(0, 6))
        slide.setEndValue(end)
        group = QParallelAnimationGroup(self)
        group.addAnimation(opacity)
        group.addAnimation(slide)
        self.setWindowOpacity(0.0)
        # No Python reference is kept: the group deletes itself (and its children) when done.
        group.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


_FADE_NAME = "lilxFadeAnimation"


def fade(widget: QWidget, show: bool, duration: int = NORMAL_MS) -> None:
    """Fade a child widget in or out (hidden at the end of a fade-out).

    Each widget owns one reusable animation (a Qt child found by name). It is never
    deleted while the widget lives, so no dangling pointer can be reached later.
    """
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(1.0 if widget.isVisible() else 0.0)
        widget.setGraphicsEffect(effect)
    animation = widget.findChild(QPropertyAnimation, _FADE_NAME)
    if animation is None:
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setObjectName(_FADE_NAME)
        animation.setEasingCurve(EASING)
        animation.finished.connect(lambda: widget.hide() if effect.opacity() < 0.01 else None)
    animation.stop()
    if not enabled():
        effect.setOpacity(1.0)
        widget.setVisible(show)
        return
    if show:
        widget.show()
    elif not widget.isVisible():
        return
    animation.setDuration(duration)
    animation.setStartValue(effect.opacity())
    animation.setEndValue(1.0 if show else 0.0)
    animation.start()
