"""Keyboard shortcuts.

Qt maps ``Ctrl`` to ⌘ on macOS and ``Meta`` to the Control key, so most
sequences below are cross-platform. Only true platform differences are split.
"""

from __future__ import annotations

import sys

from PySide6.QtGui import QKeySequence

_Std = QKeySequence.StandardKey
_MAC = sys.platform == "darwin"

Keys = tuple["str | QKeySequence.StandardKey", ...]

SHORTCUTS: dict[str, Keys] = {
    "new_tab": ("Ctrl+T",),
    "close_tab": ("Ctrl+W", "Ctrl+F4") if not _MAC else ("Ctrl+W",),
    "reopen_tab": ("Ctrl+Shift+T",),
    "next_tab": ("Meta+Tab", "Ctrl+Alt+Right", "Ctrl+}") if _MAC else ("Ctrl+Tab", "Ctrl+PgDown"),
    "prev_tab": ("Meta+Shift+Tab", "Ctrl+Alt+Left", "Ctrl+{") if _MAC else ("Ctrl+Shift+Tab", "Ctrl+PgUp"),
    "focus_address": ("Ctrl+L", "Alt+D", "F6") if not _MAC else ("Ctrl+L",),
    "back": (_Std.Back,) if _MAC else ("Alt+Left",),
    "forward": (_Std.Forward,) if _MAC else ("Alt+Right",),
    "reload": ("Ctrl+R", "F5"),
    "hard_reload": ("Ctrl+Shift+R", "Shift+F5"),
    "home": ("Ctrl+Shift+H",) if _MAC else ("Alt+Home",),
    "history": ("Ctrl+Y",) if _MAC else ("Ctrl+H",),
    "downloads": ("Ctrl+Shift+J",) if _MAC else ("Ctrl+J",),
    "settings": ("Ctrl+,",),
    "bookmark_page": ("Ctrl+D",),
    "extensions": ("Ctrl+Shift+E",),
    "zoom_in": ("Ctrl+=", "Ctrl++"),
    "zoom_out": ("Ctrl+-",),
    "zoom_reset": ("Ctrl+0",),
    "fullscreen": (_Std.FullScreen,) if _MAC else ("F11",),
    "quit": ("Ctrl+Q",),
    **{f"tab_{i}": (f"Ctrl+{i}",) for i in range(1, 9)},
    "tab_last": ("Ctrl+9",),
}


def key_sequences(action_id: str) -> list[QKeySequence]:
    sequences: list[QKeySequence] = []
    for key in SHORTCUTS.get(action_id, ()):
        if isinstance(key, str):
            sequences.append(QKeySequence(key))
        else:
            sequences.extend(QKeySequence.keyBindings(key))
    return sequences
