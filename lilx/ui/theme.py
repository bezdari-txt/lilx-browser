"""Colors and the Qt stylesheet of the browser chrome.

The same color tokens are mirrored in ``resources/pages/assets/lilx.css`` so the
chrome and the internal pages look like one product.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication


@dataclass(frozen=True)
class Palette:
    name: str
    window: str  # tab strip background
    toolbar: str  # active tab + navigation bar
    field: str  # address bar
    field_focus: str
    text: str
    muted: str
    border: str
    hover: str
    accent: str
    accent_text: str


LIGHT = Palette(
    name="light",
    window="#ecebf0",
    toolbar="#fbfbfd",
    field="#efeef3",
    field_focus="#ffffff",
    text="#1c1b22",
    muted="#6d6b78",
    border="#dcdae3",
    hover="#e2e0e9",
    accent="#7456e8",
    accent_text="#ffffff",
)

DARK = Palette(
    name="dark",
    window="#131217",
    toolbar="#1e1d24",
    field="#2a2932",
    field_focus="#302f39",
    text="#ecebf2",
    muted="#9a98a6",
    border="#2f2e37",
    hover="#2d2c35",
    accent="#a48bff",
    accent_text="#131217",
)


def resolve_theme(setting: str) -> str:
    """Map the "system" setting to "light" or "dark"."""
    if setting in ("light", "dark"):
        return setting
    scheme = QGuiApplication.styleHints().colorScheme()
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _mix(color: str, other: str, amount: float) -> str:
    """``amount`` of ``other`` mixed into ``color``."""
    a, b = _rgb(color), _rgb(other)
    return _hex(tuple(x + (y - x) * amount for x, y in zip(a, b)))


def _luminance(color: str) -> float:
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


@dataclass(frozen=True)
class Accent:
    color: str  # buttons, switches, focus rings, highlights
    text: str  # text on top of `color`
    soft: str  # subtle tinted backgrounds (selected choices, avatars)


def accent_for(mode: str, color: str, dark: bool) -> Accent | None:
    """Colors for a custom accent, adapted to the theme. None means the built-in lilac."""
    if mode != "custom":
        return None
    if dark:
        # Dark backgrounds need a lighter tone of the same hue to stay readable.
        color = _mix(color, "#ffffff", 0.25) if _luminance(color) < 0.35 else color
        soft = _mix("#1e1d24", color, 0.22)
    else:
        soft = _mix("#ffffff", color, 0.12)
    return Accent(color, _readable_text(color), soft)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _readable_text(background: str) -> str:
    """White while it keeps at least 3:1 contrast (WCAG, UI components), otherwise near-black."""
    return "#ffffff" if _contrast(background, "#ffffff") >= 3.0 else "#131217"


def palette_for(setting: str, accent_mode: str = "default", accent_color: str = "") -> Palette:
    dark = resolve_theme(setting) == "dark"
    base = DARK if dark else LIGHT
    accent = accent_for(accent_mode, accent_color, dark) if accent_color else None
    return base if accent is None else replace(base, accent=accent.color, accent_text=accent.text)


def page_css(setting: str, accent_mode: str, accent_color: str) -> str:
    """Stylesheet served as lilx://<page>/assets/theme.css (after lilx.css)."""
    accent = accent_for(accent_mode, accent_color, resolve_theme(setting) == "dark")
    if accent is None:
        return "/* built-in accent */\n"
    # Same specificity as the theme rules in lilx.css and loaded later, so it wins in both themes.
    return (
        ':root[data-theme="light"], :root[data-theme="dark"] {\n'
        f"  --accent: {accent.color};\n  --accent-text: {accent.text};\n  --accent-soft: {accent.soft};\n}}\n"
    )


def stylesheet(p: Palette) -> str:
    return f"""
    QMainWindow, #chrome {{ background: {p.window}; }}
    QWidget {{ color: {p.text}; font-size: 13px; }}
    QToolTip {{
        background: {p.toolbar}; color: {p.text}; border: 1px solid {p.border};
        padding: 4px 8px; border-radius: 6px;
    }}

    /* ---- tab strip ---- */
    #tabStrip {{ background: {p.window}; }}
    /* tabs themselves are painted by lilx.ui.tab_bar.TabBar */

    /* ---- navigation bar ---- */
    #navBar {{ background: {p.toolbar}; border-bottom: 1px solid {p.border}; }}
    QToolButton {{
        background: transparent; border: none; border-radius: 8px;
        padding: 5px; min-width: 20px; min-height: 20px;
    }}
    QToolButton:hover {{ background: {p.hover}; }}
    QToolButton:pressed {{ background: {p.border}; }}
    /* AnimatedButton paints its own fading hover/press background */
    QToolButton[animated="true"]:hover, QToolButton[animated="true"]:pressed {{ background: transparent; }}
    QToolButton:disabled {{ background: transparent; }}
    QToolButton::menu-indicator {{ image: none; width: 0; }}
    #lilblockButton {{ color: {p.accent}; font-size: 12px; font-weight: 600; padding: 5px 6px; }}
    #lilblockButton[off="true"] {{ color: {p.muted}; }}
    #downloadsButton[active="true"] {{ background: {p.hover}; }}

    #addressBar {{
        background: {p.field}; color: {p.text};
        border: 1px solid transparent; border-radius: 10px;
        padding: 6px 12px; font-size: 14px;
        selection-background-color: {p.accent}; selection-color: {p.accent_text};
    }}
    #addressBar:focus {{ background: {p.field_focus}; border: 1px solid {p.accent}; }}

    #loadProgress {{ background: transparent; border: none; max-height: 2px; min-height: 2px; }}
    #loadProgress::chunk {{ background: {p.accent}; }}

    /* ---- overlays ---- */
    #linkPreview, #toast {{
        background: {p.toolbar}; color: {p.muted};
        border: 1px solid {p.border}; border-radius: 8px;
        padding: 4px 10px; font-size: 12px;
    }}
    #toast {{ color: {p.text}; padding: 8px 14px; font-size: 13px; }}

    #extensionPopup {{ background: {p.toolbar}; border: 1px solid {p.border}; }}

    /* ---- menus ---- */
    QMenu {{
        background: {p.toolbar}; border: 1px solid {p.border}; border-radius: 10px; padding: 6px;
    }}
    QMenu::item {{ padding: 6px 28px 6px 12px; border-radius: 6px; }}
    QMenu::item:checked {{ color: {p.text}; }}
    QMenu::indicator {{ width: 14px; height: 14px; margin-left: 6px; }}
    QMenu::item:selected {{ background: {p.hover}; }}
    QMenu::item:disabled {{ color: {p.muted}; }}
    QMenu::separator {{ height: 1px; background: {p.border}; margin: 5px 8px; }}
    """
