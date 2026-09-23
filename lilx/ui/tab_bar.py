"""The tab strip, painted by lilx itself.

Each tab (background, favicon, title, close button) is drawn as one unit, so all of
it always moves together. Positions and widths follow their targets with a
critically damped spring updated at display rate: opening, closing, reordering
and resizing can interrupt each other at any moment without jumps. The tab being
dragged is glued to the cursor; the others slide out of its way.

The public API mirrors the subset of QTabBar that the main window uses.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QElapsedTimer, QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QMouseEvent, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolTip, QWidget

from lilx.i18n import tr
from lilx.ui import animations, icons
from lilx.ui.animations import AnimatedButton
from lilx.ui.theme import LIGHT, Palette

_MAX_W = 220.0
_MIN_W = 44.0  # below this only the icon is shown
_TEXT_MIN_W = 88.0
_H = 30.0
_TOP = 6.0
_GAP = 2.0
_RADIUS = 9.0
_PAD_LEFT = 10.0
_ICON = 16.0
_CLOSE = 18.0
_CLOSE_PAD = 6.0  # inner padding between the close button and the tab edge
_DRAG_THRESHOLD = 5
_TAU = 0.055  # spring time constant, seconds (≈ 95% settled after 165 ms)
_HOVER_TAU = 0.06


@dataclass(eq=False)
class _Tab:
    text: str = ""
    icon: QIcon = field(default_factory=QIcon)
    tooltip: str = ""
    data: object = None
    x: float = 0.0
    w: float = 0.0
    target_w: float = 0.0
    hover: float = 0.0  # 0..1, animated
    close_hover: float = 0.0
    closing: bool = False
    on_closed: Callable[[], None] | None = None


def _approach(value: float, target: float, dt: float, tau: float) -> float:
    if not animations.enabled():
        return target
    return target + (value - target) * math.exp(-dt / tau)


class TabBar(QWidget):
    currentChanged = Signal(int)  # noqa: N815 (QTabBar-compatible name)
    close_requested = Signal(int)
    new_tab_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._tabs: list[_Tab] = []  # visible order, includes tabs that are closing
        self._current: _Tab | None = None
        self._history: list[_Tab] = []  # previously selected tabs, for selection after close
        self._palette: Palette = LIGHT
        self._close_icon = QIcon()
        self._hovered: _Tab | None = None
        self._hover_close = False
        self._press: tuple[_Tab, float, float] | None = None  # (tab, press x, grab offset)
        self._dragging = False
        self._drag_x = 0.0
        self._close_pressed = False  # left button went down on a close button

        self.new_tab_button = AnimatedButton(self)
        self.new_tab_button.setIconSize(QSize(16, 16))
        self.new_tab_button.setFixedSize(28, 28)
        self.new_tab_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.new_tab_button.clicked.connect(self.new_tab_requested)
        self._plus_x = 0.0

        self._clock = QElapsedTimer()
        self._ticker = QTimer(self)
        self._ticker.setTimerType(Qt.TimerType.PreciseTimer)
        self._ticker.setInterval(8)
        self._ticker.timeout.connect(self._tick)
        self.retranslate()

    # ------------------------------------------------------------------ QTabBar-like API
    def _alive(self) -> list[_Tab]:
        return [t for t in self._tabs if not t.closing]

    def count(self) -> int:
        return len(self._alive())

    def _at(self, index: int) -> _Tab | None:
        alive = self._alive()
        return alive[index] if 0 <= index < len(alive) else None

    def _index(self, tab: _Tab | None) -> int:
        return self._alive().index(tab) if tab is not None and tab in self._alive() else -1

    def insertTab(self, index: int, icon: QIcon, text: str) -> int:  # noqa: N802
        alive = self._alive()
        index = max(0, min(index, len(alive)))
        tab = _Tab(text=text, icon=icon, tooltip=text)
        # Insert into the visual list right before the alive tab currently at `index`.
        position = self._tabs.index(alive[index]) if index < len(alive) else len(self._tabs)
        self._tabs.insert(position, tab)
        self._relayout(new_tab=tab)
        if self._current is None:
            self._set_current(tab)
        else:
            self.currentChanged.emit(self._index(self._current))  # indices after it shifted
        return index

    def removeTab(self, index: int) -> None:  # noqa: N802
        tab = self._at(index)
        if tab is None:
            return
        self._tabs.remove(tab)
        self._forget(tab)
        self._relayout()

    def animate_close(self, index: int, done: Callable[[], None]) -> None:
        """Shrink the tab away, then call ``done`` (which normally calls removeTab)."""
        tab = self._at(index)
        if tab is None:
            done()
            return
        if not animations.enabled():
            done()
            return
        tab.on_closed = done
        tab.closing = True
        self._forget(tab)
        self._relayout()

    def _forget(self, tab: _Tab) -> None:
        """Drop ``tab`` from selection state; select another tab if it was current."""
        self._history = [t for t in self._history if t is not tab]
        if self._press and self._press[0] is tab:
            self._press, self._dragging = None, False
        if self._hovered is tab:
            self._hovered = None
        if self._current is tab:
            alive = self._alive()  # `tab` is already removed or marked as closing
            previous = next((t for t in reversed(self._history) if t in alive), None)
            self._current = None
            if previous is None and alive:
                previous = alive[0]
            self._set_current(previous, remember=False)
        else:
            self.currentChanged.emit(self._index(self._current))

    def currentIndex(self) -> int:  # noqa: N802
        return self._index(self._current)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        tab = self._at(index)
        if tab is not None:
            self._set_current(tab)

    def _set_current(self, tab: _Tab | None, remember: bool = True) -> None:
        if tab is self._current:
            return
        if remember and self._current is not None:
            self._history.append(self._current)
            del self._history[:-50]
        self._current = tab
        self.update()
        self.currentChanged.emit(self._index(tab))

    def setTabText(self, index: int, text: str) -> None:  # noqa: N802
        if tab := self._at(index):
            tab.text = text
            self.update()

    def tabText(self, index: int) -> str:  # noqa: N802
        tab = self._at(index)
        return tab.text if tab else ""

    def setTabIcon(self, index: int, icon: QIcon) -> None:  # noqa: N802
        if tab := self._at(index):
            tab.icon = icon
            self.update()

    def setTabToolTip(self, index: int, text: str) -> None:  # noqa: N802
        if tab := self._at(index):
            tab.tooltip = text

    def setTabData(self, index: int, data: object) -> None:  # noqa: N802
        if tab := self._at(index):
            tab.data = data

    def tabData(self, index: int) -> object:  # noqa: N802
        tab = self._at(index)
        return tab.data if tab else None

    def tabRect(self, index: int) -> QRect:  # noqa: N802
        tab = self._at(index)
        return QRectF(tab.x, _TOP, tab.w, _H).toAlignedRect() if tab else QRect()

    def tabAt(self, pos: QPointF) -> int:  # noqa: N802
        tab = self._tab_at(QPointF(pos))
        return self._index(tab)

    # ------------------------------------------------------------------ appearance
    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self._close_icon = icons.icon("close", palette.muted, 12, 2.0)
        self.new_tab_button.setIcon(icons.icon("plus", palette.muted, 16))
        self.new_tab_button.set_colors(palette.hover, palette.border)
        self.update()

    def retranslate(self) -> None:
        self.new_tab_button.setToolTip(tr("New tab"))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(400, int(_H + 2 * _TOP))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(100, int(_H + 2 * _TOP))

    # ------------------------------------------------------------------ layout & animation
    def _slot_width(self) -> float:
        n = max(len(self._alive()), 1)
        available = self.width() - self.new_tab_button.width() - 8
        return max(_MIN_W, min(_MAX_W, (available - _GAP * n) / n))

    def _targets(self) -> dict[_Tab, float]:
        """Target x of every tab (closing tabs keep their place while they shrink)."""
        result: dict[_Tab, float] = {}
        x = 0.0
        for tab in self._tabs:
            result[tab] = x
            x += tab.target_w + (_GAP if tab.target_w > 0 else 0)
        return result

    def _relayout(self, new_tab: _Tab | None = None) -> None:
        width = self._slot_width()
        for tab in self._tabs:
            tab.target_w = 0.0 if tab.closing else width
        if new_tab is not None:
            # A new tab grows from nothing at its final position.
            new_tab.x = self._targets()[new_tab]
            new_tab.w = 0.0 if animations.enabled() else width
        self._start()

    def _start(self) -> None:
        if not self._ticker.isActive():
            self._clock.start()
            self._ticker.start()
        self._tick()

    def _tick(self) -> None:
        dt = min(self._clock.restart() / 1000.0, 0.05) or 0.008
        targets = self._targets()
        moving = False
        for tab in list(self._tabs):
            if self._dragging and self._press and tab is self._press[0]:
                tab.x = self._drag_x  # glued to the cursor
            else:
                tab.x = _approach(tab.x, targets[tab], dt, _TAU)
            tab.w = _approach(tab.w, tab.target_w, dt, _TAU)
            hover = 1.0 if tab is self._hovered else 0.0
            tab.hover = _approach(tab.hover, hover, dt, _HOVER_TAU)
            close_hover = 1.0 if tab is self._hovered and self._hover_close else 0.0
            tab.close_hover = _approach(tab.close_hover, close_hover, dt, _HOVER_TAU)
            if tab.closing and tab.w < 1.0:
                self._tabs.remove(tab)
                if tab.on_closed is not None:
                    callback, tab.on_closed = tab.on_closed, None
                    callback()
                continue
            if (abs(tab.x - targets[tab]) > 0.3 or abs(tab.w - tab.target_w) > 0.3
                    or abs(tab.hover - hover) > 0.01 or abs(tab.close_hover - close_hover) > 0.01):
                moving = True
        end = max((t.x + t.w for t in self._tabs), default=0.0) + 4
        target_plus = min(end, self.width() - self.new_tab_button.width())
        self._plus_x = _approach(self._plus_x, target_plus, dt, _TAU) if self._tabs else target_plus
        moving = moving or abs(self._plus_x - target_plus) > 0.3
        self.new_tab_button.move(int(round(self._plus_x)), int((self.height() - self.new_tab_button.height()) / 2))
        self.update()
        if not moving and not self._dragging:
            self._ticker.stop()

    def resizeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        width = self._slot_width()
        for tab in self._tabs:
            if not tab.closing:
                tab.target_w = tab.w = width  # window resizes follow immediately
        for tab, x in self._targets().items():
            tab.x = x
        self._start()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        dragged = self._press[0] if self._dragging and self._press else None
        # Current and dragged tabs are painted last so they sit on top while moving.
        order = sorted(self._tabs, key=lambda t: (t is dragged, t is self._current))
        for tab in order:
            self._paint_tab(painter, tab)
        painter.end()

    def _paint_tab(self, painter: QPainter, tab: _Tab) -> None:
        if tab.w < 2:
            return
        p = self._palette
        rect = QRectF(tab.x, _TOP, tab.w, _H)
        full = max(tab.target_w, tab.w, 1.0)
        opacity = min(1.0, tab.w / min(full, 60.0)) if tab.closing or tab.w < full - 0.5 else 1.0
        painter.save()
        painter.setOpacity(opacity)
        path = QPainterPath()
        path.addRoundedRect(rect, _RADIUS, _RADIUS)
        painter.setClipPath(path)  # icon, title and close button never leave their tab

        selected = tab is self._current
        if selected:
            painter.fillPath(path, QColor(p.toolbar))
        elif tab.hover > 0.01:
            color = QColor(p.hover)
            color.setAlphaF(color.alphaF() * tab.hover)
            painter.fillPath(path, color)

        compact = rect.width() < _TEXT_MIN_W
        show_close = not compact or selected
        icon_x = rect.x() + (_PAD_LEFT if not compact or show_close else (rect.width() - _ICON) / 2)
        if compact and selected:
            icon_x = rect.x() + 8
        icon_rect = QRectF(icon_x, rect.y() + (_H - _ICON) / 2, _ICON, _ICON)
        if not tab.icon.isNull():
            tab.icon.paint(painter, icon_rect.toAlignedRect())

        close_rect = self._close_rect(tab)
        if show_close:
            if tab.close_hover > 0.01:
                color = QColor(p.border if tab is self._hovered and self._close_pressed else p.hover)
                color.setAlphaF(color.alphaF() * tab.close_hover)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawRoundedRect(close_rect, 5, 5)
            self._close_icon.paint(painter, close_rect.adjusted(3, 3, -3, -3).toAlignedRect())

        if not compact:
            left = icon_rect.right() + 8
            right = (close_rect.left() - 4) if show_close else rect.right() - 8
            text_rect = QRectF(left, rect.y(), max(right - left, 0), _H)
            font = QFont(self.font())
            painter.setFont(font)
            painter.setPen(QColor(p.text if selected or tab.hover > 0.5 else p.muted))
            metrics = QFontMetricsF(font)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             metrics.elidedText(tab.text, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()

    @staticmethod
    def _close_rect(tab: _Tab) -> QRectF:
        return QRectF(tab.x + tab.w - _CLOSE_PAD - _CLOSE, _TOP + (_H - _CLOSE) / 2, _CLOSE, _CLOSE)

    # ------------------------------------------------------------------ mouse
    def _tab_at(self, pos: QPointF) -> _Tab | None:
        if not (_TOP <= pos.y() <= _TOP + _H):
            return None
        dragged = self._press[0] if self._dragging and self._press else None
        if dragged and dragged.x <= pos.x() <= dragged.x + dragged.w:
            return dragged
        for tab in self._alive():
            if tab.x <= pos.x() <= tab.x + tab.w:
                return tab
        return None

    def _on_close_button(self, tab: _Tab, pos: QPointF) -> bool:
        compact = tab.w < _TEXT_MIN_W
        if compact and tab is not self._current:
            return False
        return self._close_rect(tab).adjusted(-2, -2, 2, 2).contains(pos)

    def _set_hover(self, pos: QPointF | None) -> None:
        tab = self._tab_at(pos) if pos is not None else None
        on_close = bool(tab and pos is not None and self._on_close_button(tab, pos))
        if tab is not self._hovered or on_close != self._hover_close:
            self._hovered, self._hover_close = tab, on_close
            self._start()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        tab = self._tab_at(pos)
        if tab is None:
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            if self._on_close_button(tab, pos):
                self._close_pressed = True
                self.update()
                return
            self._set_current(tab)
            self._press = (tab, pos.x(), pos.x() - tab.x)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        if self._press and event.buttons() & Qt.MouseButton.LeftButton:
            tab, press_x, grab = self._press
            if not self._dragging and abs(pos.x() - press_x) >= _DRAG_THRESHOLD and len(self._alive()) > 1:
                self._dragging = True
            if self._dragging:
                limit = max(sum(t.target_w + _GAP for t in self._alive()) - tab.target_w - _GAP, 0)
                self._drag_x = min(max(pos.x() - grab, 0.0), limit)
                self._reorder_while_dragging(tab)
                self._start()
                return
        self._set_hover(pos)
        if self._hovered is None:
            QToolTip.hideText()

    def _reorder_while_dragging(self, dragged: _Tab) -> None:
        """Move the dragged tab to the slot under its own center (all open tabs share one width)."""
        alive = self._alive()
        slot = dragged.target_w + _GAP
        target = int((self._drag_x + slot / 2) // slot) if slot > 0 else 0
        target = max(0, min(target, len(alive) - 1))
        if alive.index(dragged) == target:
            return
        others = [t for t in alive if t is not dragged]
        others.insert(target, dragged)
        # Closing tabs keep their neighbours; append them at the end of the visual list.
        self._tabs = others + [t for t in self._tabs if t.closing]
        self.currentChanged.emit(self._index(self._current))  # indices changed

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        if event.button() == Qt.MouseButton.MiddleButton:
            tab = self._tab_at(pos)
            if tab is not None:
                self.close_requested.emit(self._index(tab))
            return
        if self._close_pressed:
            self._close_pressed = False
            tab = self._tab_at(pos)
            if tab is not None and self._on_close_button(tab, pos):
                self.close_requested.emit(self._index(tab))
            self.update()
            return
        if self._dragging and self._press:
            tab = self._press[0]
            tab.x = self._drag_x  # continue smoothly from where it was dropped
        self._press, self._dragging = None, False
        self._start()
        self._set_hover(pos)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._tab_at(event.position()) is None and event.button() == Qt.MouseButton.LeftButton:
            self.new_tab_requested.emit()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self._set_hover(None)
        super().leaveEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            pos = QPointF(event.pos())
            tab = self._tab_at(pos)
            if tab is None:
                QToolTip.hideText()
            elif self._on_close_button(tab, pos):
                QToolTip.showText(event.globalPos(), tr("Close tab"), self)
            else:
                QToolTip.showText(event.globalPos(), tab.tooltip or tab.text, self)
            return True
        return super().event(event)


class TabStrip(QWidget):
    """Container of the tab bar (which also owns the "+" button)."""

    new_tab_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tabStrip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.tabs = TabBar(self)
        self.tabs.new_tab_requested.connect(self.new_tab_requested)
        self.new_tab_button = self.tabs.new_tab_button
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(0)
        layout.addWidget(self.tabs)

    def set_palette(self, palette: Palette) -> None:
        self.tabs.set_palette(palette)

    def retranslate(self) -> None:
        self.tabs.retranslate()
