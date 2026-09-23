"""The browser window: tab strip, navigation bar and a stack of web views."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial

import shiboken6
from PySide6.QtCore import QPoint, QPropertyAnimation, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QGuiApplication, QIcon
from PySide6.QtWebEngineCore import QWebEngineFullScreenRequest, QWebEnginePage, QWebEngineProfile
from PySide6.QtWidgets import QApplication, QMainWindow, QProgressBar, QStackedWidget, QVBoxLayout, QWidget

from lilx import APP_NAME
from lilx.branding import app_icon
from lilx.context import BrowserContext
from lilx.core.search import resolve_input
from lilx.engine.scheme import INTERNAL_PAGES, internal_url, is_internal
from lilx.i18n import tr
from lilx.ui import animations, icons
from lilx.ui.animations import AnimatedMenu
from lilx.ui.browser_view import NEW_TAB_TITLE, BrowserView, display_url
from lilx.ui.extension_popup import ExtensionPopup
from lilx.ui.navigation_bar import NavigationBar
from lilx.ui.overlays import OverlayLabel
from lilx.ui.permission_bar import PermissionBar
from lilx.ui.shortcuts import key_sequences
from lilx.ui.tab_bar import TabStrip
from lilx.ui.theme import Palette, palette_for, stylesheet

log = logging.getLogger(__name__)

_MAX_CLOSED_TABS = 25
_WebWindowType = QWebEnginePage.WebWindowType


class MainWindow(QMainWindow):
    def __init__(
        self,
        ctx: BrowserContext,
        private: bool = False,
        profile: QWebEngineProfile | None = None,
        manager: object | None = None,
    ) -> None:
        super().__init__()
        self._ctx = ctx
        #: private windows use the off-the-record profile and never write history
        self.private = private
        self._profile = profile if profile is not None else ctx.profile
        self._manager = manager  # lilx.ui.windows.WindowManager
        self._closed_tabs: list[QUrl] = []
        self._palette: Palette = self._current_palette()
        self._was_maximized = False
        self._page_fullscreen = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)
        self.setMinimumSize(480, 360)

        self._actions: dict[str, QAction] = {}
        self._create_actions()
        self._build_ui()
        self.apply_theme()
        self._update_window_title()  # "lilx (Private)" from the start in private windows

        ctx.settings.changed.connect(self._on_setting_changed)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_theme_changed)
        ctx.downloads.started.connect(self._on_download_started)
        ctx.downloads.changed.connect(self._on_downloads_changed)
        ctx.lilblock.changed.connect(self._update_lilblock_button)
        ctx.bookmarks.changed.connect(self._on_bookmarks_changed)
        ctx.extensions.changed.connect(self._notify_internal_pages)  # lilx://extensions refreshes

    # ------------------------------------------------------------------ setup
    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("chrome")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tab_strip = TabStrip(central)
        self._tabs = self._tab_strip.tabs
        self._tab_strip.new_tab_requested.connect(lambda: self.new_tab())
        self._tabs.currentChanged.connect(self._on_current_changed)
        self._tabs.close_requested.connect(self.close_tab)

        self._nav = NavigationBar(self._build_menu(), central)
        self._nav.back_clicked.connect(lambda: self._with_view(lambda v: v.back()))
        self._nav.forward_clicked.connect(lambda: self._with_view(lambda v: v.forward()))
        self._nav.reload_clicked.connect(lambda: self._with_view(lambda v: v.reload()))
        self._nav.stop_clicked.connect(lambda: self._with_view(lambda v: v.stop()))
        self._nav.home_clicked.connect(self.go_home)
        self._nav.downloads_clicked.connect(lambda: self.open_internal("downloads"))
        self._nav.address.submitted.connect(self.navigate)
        self._nav.lilblock_clicked.connect(self._show_lilblock_menu)
        self._nav.bookmarks_clicked.connect(self._show_bookmarks_menu)
        self._nav.extensions_clicked.connect(self._show_extensions_menu)

        self._progress = QProgressBar(central)
        self._progress.setObjectName("loadProgress")
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(2)
        self._progress_animation = QPropertyAnimation(self._progress, b"value", self)
        self._progress_animation.setDuration(animations.NORMAL_MS)
        self._progress_animation.setEasingCurve(animations.EASING)
        self._progress_finishing = False

        self._permission_bar = PermissionBar(central)
        self._permission_bar.answered.connect(self._on_permission_answered)

        self._stack = QStackedWidget(central)
        self._link_preview = OverlayLabel(self._stack, "linkPreview")
        self._toast = OverlayLabel(self._stack, "toast", align_right=True)

        layout.addWidget(self._tab_strip)
        layout.addWidget(self._nav)
        layout.addWidget(self._progress)
        layout.addWidget(self._permission_bar)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

    def _create_actions(self) -> None:
        handlers: dict[str, tuple[str, Callable[[], None]]] = {
            "new_tab": ("New tab", lambda: self.new_tab()),
            "new_window": ("New window", lambda: self._open_window(private=False)),
            "new_private_window": ("New private window", lambda: self._open_window(private=True)),
            "close_tab": ("Close tab", lambda: self.close_tab(self._tabs.currentIndex())),
            "reopen_tab": ("Reopen closed tab", self.reopen_closed_tab),
            "next_tab": ("Next tab", lambda: self._cycle_tab(1)),
            "prev_tab": ("Previous tab", lambda: self._cycle_tab(-1)),
            "focus_address": ("Focus address bar", self.focus_address_bar),
            "back": ("Back", lambda: self._with_view(lambda v: v.back())),
            "forward": ("Forward", lambda: self._with_view(lambda v: v.forward())),
            "reload": ("Reload", lambda: self._with_view(lambda v: v.reload())),
            "hard_reload": ("Reload without cache", lambda: self._with_view(
                lambda v: v.triggerPageAction(QWebEnginePage.WebAction.ReloadAndBypassCache))),
            "home": ("Home", self.go_home),
            "history": ("History", lambda: self.open_internal("history")),
            "downloads": ("Downloads", lambda: self.open_internal("downloads")),
            "settings": ("Settings", lambda: self.open_internal("settings")),
            "bookmark_page": ("Bookmark this page", self.toggle_bookmark),
            "extensions": ("Extensions", lambda: self.open_internal("extensions")),
            "zoom_in": ("Zoom in", lambda: self._with_view(lambda v: v.zoom(1))),
            "zoom_out": ("Zoom out", lambda: self._with_view(lambda v: v.zoom(-1))),
            "zoom_reset": ("Actual size", lambda: self._with_view(lambda v: v.zoom(0, self._default_zoom()))),
            "fullscreen": ("Full screen", self.toggle_fullscreen),
            "quit": ("Quit lilx", QApplication.closeAllWindows),
            **{f"tab_{i}": ("Tab {n}", partial(self._select_tab, i - 1)) for i in range(1, 9)},
            "tab_last": ("Last tab", lambda: self._select_tab(self._tabs.count() - 1)),
        }
        self._action_texts: dict[str, tuple[str, dict[str, object]]] = {}
        for action_id, (text, handler) in handlers.items():
            values: dict[str, object] = {"n": action_id.removeprefix("tab_")} if "{n}" in text else {}
            self._action_texts[action_id] = (text, values)
            action = QAction(tr(text, **values), self)
            action.setShortcuts(key_sequences(action_id))
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            action.triggered.connect(handler)
            self.addAction(action)
            self._actions[action_id] = action
        self._actions["quit"].setMenuRole(QAction.MenuRole.QuitRole)

    def _build_menu(self) -> AnimatedMenu:
        menu = AnimatedMenu(self)
        groups = (
            ("new_tab", "new_window", "new_private_window", "reopen_tab"),
            ("bookmark_page", "history", "downloads", "extensions", "settings"),
            ("zoom_in", "zoom_out", "zoom_reset", "fullscreen"),
            ("quit",),
        )
        for i, group in enumerate(groups):
            if i:
                menu.addSeparator()
            for action_id in group:
                menu.addAction(self._actions[action_id])
        return menu

    # ------------------------------------------------------------------ theme
    def apply_theme(self) -> None:
        self._palette = self._current_palette()
        self.setStyleSheet(stylesheet(self._palette))
        self._tab_strip.set_palette(self._palette)
        self._nav.set_palette(self._palette)
        self._nav.set_private(self.private)
        self._permission_bar.set_palette(self._palette)
        self._update_nav_state()
        for index in range(self._tabs.count()):
            view = self._view_at(index)
            if view is not None and view.icon().isNull():
                self._tabs.setTabIcon(index, self._fallback_icon())

    def _current_palette(self) -> Palette:
        current = self._ctx.settings.current
        return palette_for(current.theme, current.accent_mode, current.accent_color)

    def _fallback_icon(self) -> QIcon:
        return icons.icon("globe", self._palette.muted, 16, 1.5)

    def _on_setting_changed(self, key: str) -> None:
        if key == "theme":
            self.apply_theme()
            self._reload_internal_pages()
        elif key in ("accent_mode", "accent_color"):
            # Pages pick the new accent up from theme.css without a reload (see lilx.js),
            # so dragging the color sliders in Settings stays smooth.
            self.apply_theme()
            self._notify_internal_pages()
        elif key == "language":
            self.retranslate()
            self._reload_internal_pages()
        else:
            if key == "default_zoom":
                for index in range(self._tabs.count()):
                    view = self._view_at(index)
                    if view is not None:
                        view.set_zoom(self._default_zoom())
            self._notify_internal_pages()

    def _notify_internal_pages(self) -> None:
        """Let open lilx:// pages refresh their data (e.g. after the folder dialog)."""
        for index in range(self._tabs.count()):
            view = self._view_at(index)
            if view is not None and is_internal(view.url()):
                view.page().runJavaScript("window.dispatchEvent(new Event('lilx:settings-changed'))")

    def retranslate(self) -> None:
        for action_id, (text, values) in self._action_texts.items():
            self._actions[action_id].setText(tr(text, **values))
        self._tab_strip.retranslate()
        self._nav.retranslate()
        for index in range(self._tabs.count()):
            view = self._view_at(index)
            if view is not None:
                self._tabs.setTabText(index, view.display_title())
        self._update_window_title()
        self._update_lilblock_button()
        self._update_bookmark_button()

    def _on_system_theme_changed(self) -> None:
        if self._ctx.settings.current.theme == "system":
            self.apply_theme()
            self._reload_internal_pages()

    def _reload_internal_pages(self) -> None:
        # Internal pages get the theme from the server side, so they need a reload.
        for index in range(self._tabs.count()):
            view = self._view_at(index)
            if view is not None and is_internal(view.url()):
                view.reload()

    # ------------------------------------------------------------------ tabs
    def _view_at(self, index: int) -> BrowserView | None:
        data = self._tabs.tabData(index) if 0 <= index < self._tabs.count() else None
        return data if isinstance(data, BrowserView) else None

    def _index_of(self, view: BrowserView) -> int:
        for index in range(self._tabs.count()):
            if self._tabs.tabData(index) is view:
                return index
        return -1

    def current_view(self) -> BrowserView | None:
        return self._view_at(self._tabs.currentIndex())

    def _with_view(self, action: Callable[[BrowserView], object]) -> None:
        view = self.current_view()
        if view is not None:
            action(view)

    def new_tab(
        self, url: QUrl | None = None, *, background: bool = False, after_current: bool = False
    ) -> BrowserView:
        view = BrowserView(self._profile, self._ctx.settings, self._ctx.lilblock, self._create_window, self._stack,
                           permissions=self._ctx.permissions)
        view.set_zoom(self._default_zoom())
        self._stack.addWidget(view)

        index = self._tabs.currentIndex() + 1 if after_current and self._tabs.count() else self._tabs.count()
        index = self._tabs.insertTab(index, self._fallback_icon(), tr(NEW_TAB_TITLE))
        self._tabs.setTabData(index, view)

        view.titleChanged.connect(partial(self._on_title_changed, view))
        view.urlChanged.connect(partial(self._on_url_changed, view))
        view.iconChanged.connect(partial(self._on_icon_changed, view))
        view.loadStarted.connect(partial(self._on_load_state, view))
        view.loadProgress.connect(partial(self._on_load_state, view))
        view.loadFinished.connect(partial(self._on_load_finished, view))
        page = view.page()
        page.linkHovered.connect(self._on_link_hovered)
        page.fullScreenRequested.connect(self._on_fullscreen_requested)
        page.renderProcessTerminated.connect(partial(self._on_render_terminated, view))
        page.blocker.count_changed.connect(partial(self._on_blocked_changed, view))
        page.permission_requests_changed.connect(partial(self._on_permission_requests_changed, view))

        view.load(url if url is not None else internal_url("home"))
        if not background:
            self._tabs.setCurrentIndex(index)
            if url is None:
                self.focus_address_bar()
        return view

    def _create_window(self, opener: QWebEnginePage, window_type: _WebWindowType) -> QWebEnginePage | None:
        """target=_blank links and window.open(): open a tab right after the opener."""
        background = window_type == _WebWindowType.WebBrowserBackgroundTab
        for index in range(self._tabs.count()):
            view = self._view_at(index)
            if view is not None and view.page() is opener:
                if not background:
                    self._tabs.setCurrentIndex(index)
                break
        tab = self.new_tab(QUrl("about:blank"), background=background, after_current=True)
        return tab.page()

    def close_tab(self, index: int) -> None:
        view = self._view_at(index)
        if view is None or view.property("closing"):
            return
        view.setProperty("closing", True)
        url = view.url()
        if url.isValid() and not url.isEmpty() and url.toString() != "about:blank":
            self._closed_tabs.append(url)
            del self._closed_tabs[:-_MAX_CLOSED_TABS]
        if self._tabs.count() == 1:
            self.new_tab()  # never leave the window without a tab
        view.stop()
        self._tabs.animate_close(self._index_of(view), lambda: self._remove_tab(view))

    def _remove_tab(self, view: BrowserView) -> None:
        if not shiboken6.isValid(view):
            return  # already removed (e.g. the window closed during the close animation)
        index = self._index_of(view)
        if index >= 0:
            self._tabs.removeTab(index)
        self._stack.removeWidget(view)
        view.deleteLater()

    def reopen_closed_tab(self) -> None:
        if self._closed_tabs:
            self.new_tab(self._closed_tabs.pop())

    def _cycle_tab(self, step: int) -> None:
        count = self._tabs.count()
        if count:
            self._tabs.setCurrentIndex((self._tabs.currentIndex() + step) % count)

    def _select_tab(self, index: int) -> None:
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def open_internal(self, page: str, section: str = "") -> None:
        """Switch to a tab showing lilx://<page>, or open one. ``section`` scrolls to an anchor."""
        if page not in INTERNAL_PAGES:
            return
        url = internal_url(page)
        if section:
            url.setFragment(section)
        for index in range(self._tabs.count()):
            view = self._view_at(index)
            if view is not None and is_internal(view.url()) and view.url().host() == page:
                self._tabs.setCurrentIndex(index)
                view.load(url)
                return
        current = self.current_view()
        if current is not None and current.url().host() == "home" and is_internal(current.url()):
            current.load(url)  # reuse an empty new-tab page
        else:
            self.new_tab(url)

    # ------------------------------------------------------------------ navigation
    def navigate(self, text: str) -> None:
        url = resolve_input(text, self._ctx.settings.current.search_engine)
        if not url:
            return
        view = self.current_view() or self.new_tab()
        view.load(QUrl(url))
        view.setFocus()

    def go_home(self) -> None:
        homepage = self._ctx.settings.current.homepage
        self._with_view(lambda v: v.load(QUrl(homepage)))

    def focus_address_bar(self) -> None:
        self._nav.address.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._nav.address.selectAll()

    def open_url(self, text: str) -> None:
        """Open a URL from the command line in a new tab."""
        url = resolve_input(text, self._ctx.settings.current.search_engine)
        if url:
            self.new_tab(QUrl(url))

    # ------------------------------------------------------------------ view events
    def _on_current_changed(self, index: int) -> None:
        view = self._view_at(index)
        if view is None:
            return
        self._stack.setCurrentWidget(view)
        self._nav.address.show_url(display_url(view.url()))
        self._link_preview.hide()
        self._update_nav_state()
        self._update_window_title()
        self._update_lilblock_button()
        self._update_bookmark_button()
        self._update_permission_bar()
        if not self._nav.address.hasFocus():
            view.setFocus()

    def _on_title_changed(self, view: BrowserView, title: str) -> None:
        index = self._index_of(view)
        if index < 0:
            return
        text = view.display_title()
        self._tabs.setTabText(index, text)
        self._tabs.setTabToolTip(index, text)
        if index == self._tabs.currentIndex():
            self._update_window_title()
        url = view.url()
        if self._records_history() and url.scheme() in ("http", "https"):
            self._ctx.history.update_title(url.toString(), title)

    def _on_url_changed(self, view: BrowserView, url: QUrl) -> None:
        if view is self.current_view():
            self._nav.address.show_url(display_url(url))
            self._update_nav_state()
            self._update_lilblock_button()
            self._update_bookmark_button()

    def _on_icon_changed(self, view: BrowserView, icon: QIcon) -> None:
        index = self._index_of(view)
        if index >= 0:
            self._tabs.setTabIcon(index, self._fallback_icon() if icon.isNull() else icon)

    def _on_load_state(self, view: BrowserView, *_: object) -> None:
        if view is self.current_view():
            self._update_nav_state()

    def _on_load_finished(self, view: BrowserView, ok: bool) -> None:
        self._on_load_state(view)
        url = view.url()
        if ok and self._records_history() and url.scheme() in ("http", "https"):
            self._ctx.history.add_visit(url.toString(), view.title())

    def _update_nav_state(self) -> None:
        view = self.current_view()
        if view is None:
            return
        history = view.history()
        self._nav.set_navigation_state(history.canGoBack(), history.canGoForward())
        self._nav.set_loading(view.is_loading)
        if view.is_loading:
            self._progress_finishing = False
            self._set_progress(max(view.progress, 8))
        elif self._progress.value() > 0 and not self._progress_finishing:
            # Run the bar to the end, then hide it.
            self._progress_finishing = True
            self._set_progress(100)
            QTimer.singleShot(animations.NORMAL_MS + 120, self._reset_progress)

    def _set_progress(self, value: int) -> None:
        self._progress_animation.stop()
        if not animations.enabled() or value < self._progress.value():
            self._progress.setValue(value)
            return
        self._progress_animation.setStartValue(self._progress.value())
        self._progress_animation.setEndValue(value)
        self._progress_animation.start()

    def _reset_progress(self) -> None:
        if self._progress_finishing:
            self._progress_animation.stop()
            self._progress.setValue(0)
            self._progress_finishing = False

    def _update_window_title(self) -> None:
        view = self.current_view()
        title = view.display_title() if view is not None else ""
        name = f"{APP_NAME} ({tr('Private')})" if self.private else APP_NAME
        self.setWindowTitle(f"{title} — {name}" if title and title != tr(NEW_TAB_TITLE) else name)

    def _on_link_hovered(self, url: str) -> None:
        self._link_preview.show_text(url)

    def _on_render_terminated(self, view: BrowserView, status: object, exit_code: int) -> None:
        log.warning("Render process of %s terminated (%s, code %d)", view.url().toString(), status, exit_code)
        index = self._index_of(view)
        if index >= 0:
            self._tabs.setTabText(index, tr("Page crashed — reload"))

    # ------------------------------------------------------------------ fullscreen
    def _on_fullscreen_requested(self, request: QWebEngineFullScreenRequest) -> None:
        request.accept()
        self._page_fullscreen = request.toggleOn()
        for widget in (self._tab_strip, self._nav, self._progress):
            widget.setVisible(not request.toggleOn())
        if request.toggleOn():
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        elif self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()

    def toggle_fullscreen(self) -> None:
        if self._page_fullscreen:
            self._with_view(lambda v: v.triggerPageAction(QWebEnginePage.WebAction.ExitFullScreen))
        elif self.isFullScreen():
            self.showMaximized() if self._was_maximized else self.showNormal()
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()

    # ------------------------------------------------------------------ downloads
    def _on_download_started(self, file_name: str, private: bool = False) -> None:
        if private == self.private:  # a private download is announced in private windows only
            self._toast.show_text(tr("Downloading {name}", name=file_name), 3500)

    def _on_downloads_changed(self) -> None:
        self._nav.set_downloads_active(self._ctx.downloads.active_count() > 0)

    # ------------------------------------------------------------------ lilBlock
    @staticmethod
    def _site_host(view: BrowserView) -> str:
        url = view.url()
        return url.host() if url.scheme() in ("http", "https") else ""

    def _on_blocked_changed(self, view: BrowserView, count: int) -> None:
        if view is self.current_view():
            self._update_lilblock_button()

    def _lilblock_status(self, view: BrowserView) -> tuple[bool, str]:
        lilblock = self._ctx.lilblock
        host = self._site_host(view)
        if not lilblock.enabled:
            return False, tr("lilBlock is off")
        if host and lilblock.is_allowed_site(host):
            return False, tr("lilBlock is off on {host}", host=host)
        return True, tr("lilBlock: {count} blocked on this page", count=view.blocked_count)

    def _update_lilblock_button(self) -> None:
        view = self.current_view()
        if view is not None:
            active, text = self._lilblock_status(view)
            self._nav.set_lilblock_state(active, view.blocked_count, text)

    def _show_lilblock_menu(self) -> None:
        view = self.current_view()
        if view is None:
            return
        lilblock = self._ctx.lilblock
        host = self._site_host(view)
        menu = AnimatedMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        header = menu.addAction(self._lilblock_status(view)[1])
        header.setEnabled(False)
        menu.addSeparator()
        site = menu.addAction(tr("Block ads on {host}", host=host) if host else tr("Not available on this page"))
        site.setCheckable(True)
        site.setChecked(bool(host) and not lilblock.is_allowed_site(host))
        site.setEnabled(bool(host) and lilblock.enabled)
        site.toggled.connect(lambda on: self._set_site_blocking(host, on))
        enabled = menu.addAction(tr("Enable lilBlock"))
        enabled.setCheckable(True)
        enabled.setChecked(lilblock.enabled)
        enabled.toggled.connect(self._set_lilblock_enabled)
        menu.addSeparator()
        menu.addAction(tr("lilBlock settings…"), lambda: self.open_internal("settings", "lilblock"))

        button = self._nav.lilblock
        menu.popup(button.mapToGlobal(QPoint(button.width() - menu.sizeHint().width(), button.height() + 4)))

    def _set_site_blocking(self, host: str, block: bool) -> None:
        if block:
            self._ctx.settings.block_ads(host)
        else:
            self._ctx.settings.allow_ads(host)
        self._with_view(lambda v: v.reload())  # apply to the page right away

    def _set_lilblock_enabled(self, on: bool) -> None:
        self._ctx.settings.set("adblock_enabled", on)
        view = self.current_view()
        if view is not None and self._site_host(view):
            view.reload()

    # ------------------------------------------------------------------ bookmarks
    def _default_zoom(self) -> float:
        return self._ctx.settings.current.default_zoom / 100

    @staticmethod
    def _bookmarkable(view: BrowserView) -> bool:
        return view.url().scheme() in ("http", "https")

    def _update_bookmark_button(self) -> None:
        view = self.current_view()
        bookmarked = view is not None and self._ctx.bookmarks.find(view.url().toString()) is not None
        self._nav.set_bookmarked(bookmarked)
        self._actions["bookmark_page"].setText(tr("Remove bookmark" if bookmarked else "Bookmark this page"))

    def _on_bookmarks_changed(self) -> None:
        self._update_bookmark_button()
        self._notify_internal_pages()  # the home page shows bookmarks

    def toggle_bookmark(self) -> None:
        view = self.current_view()
        if view is None:
            return
        if not self._bookmarkable(view):
            self._toast.show_text(tr("This page cannot be bookmarked"), 2500)
            return
        url = view.url().toString()
        if self._ctx.bookmarks.remove_url(url):
            self._toast.show_text(tr("Bookmark removed"), 2000)
        else:
            self._ctx.bookmarks.add(url, view.title())
            self._toast.show_text(tr("Bookmark added"), 2000)

    def _show_bookmarks_menu(self) -> None:
        view = self.current_view()
        if view is None:
            return
        menu = AnimatedMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        toggle = menu.addAction(self._actions["bookmark_page"].text(), self.toggle_bookmark)
        toggle.setShortcut(self._actions["bookmark_page"].shortcut())
        toggle.setEnabled(self._bookmarkable(view))
        menu.addSeparator()
        bookmarks = self._ctx.bookmarks.all()
        if not bookmarks:
            menu.addAction(tr("No bookmarks yet")).setEnabled(False)
        for bookmark in bookmarks[:30]:
            title = menu.fontMetrics().elidedText(bookmark.title or bookmark.url, Qt.TextElideMode.ElideRight, 320)
            action = menu.addAction(icons.icon("star", self._palette.muted, 14), title)
            action.setToolTip(bookmark.url)
            action.triggered.connect(partial(self._open_bookmark, bookmark.url))
        menu.addSeparator()
        menu.addAction(tr("Show bookmarks on the home page"), lambda: self.open_internal("home"))
        button = self._nav.bookmarks
        menu.popup(button.mapToGlobal(QPoint(button.width() - menu.sizeHint().width(), button.height() + 4)))

    def _open_bookmark(self, url: str) -> None:
        view = self.current_view() or self.new_tab()
        view.load(QUrl(url))
        view.setFocus()

    # ------------------------------------------------------------------ extensions
    def _show_extensions_menu(self) -> None:
        if self.private:
            # Extensions run in the normal profile only; the private profile has none loaded.
            self._toast.show_text(tr("Extensions are off in private windows"), 2500)
            return
        service = self._ctx.extensions
        menu = AnimatedMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        installed = service.installed()
        enabled = service.enabled_extensions()
        if not installed:
            menu.addAction(tr("No extensions installed")).setEnabled(False)
        elif not enabled:
            menu.addAction(tr("No enabled extensions")).setEnabled(False)
        for info in enabled:
            action = menu.addAction(service.icon(info), info.name())
            if info.actionPopupUrl().isValid():
                action.triggered.connect(partial(self.open_extension_popup, info.id()))
            else:
                action.setToolTip(tr("{name} has no popup", name=info.name()))
                action.triggered.connect(
                    partial(self._toast.show_text, tr("{name} has no popup", name=info.name()), 2500))
        menu.addSeparator()
        menu.addAction(tr("Manage extensions…"), lambda: self.open_internal("extensions"))
        button = self._nav.extensions
        menu.popup(button.mapToGlobal(QPoint(button.width() - menu.sizeHint().width(), button.height() + 4)))

    def open_extension_popup(self, extension_id: str) -> ExtensionPopup | None:
        """Open the extension's action popup (QWebEngineExtensionInfo.actionPopupUrl())."""
        info = self._ctx.extensions.find(extension_id)
        if info is None or not info.actionPopupUrl().isValid():
            return None
        popup = ExtensionPopup(self._ctx.profile, info.actionPopupUrl(),
                               lambda: self.new_tab(QUrl("about:blank"), after_current=True).page(), self)
        popup.show_below(self._nav.extensions)
        return popup

    # ------------------------------------------------------------------ windows & privacy
    def _open_window(self, private: bool) -> None:
        if self._manager is not None:
            self._manager.open_window(private=private)

    def _records_history(self) -> bool:
        return not self.private and self._ctx.settings.current.history_enabled

    # ------------------------------------------------------------------ site permissions
    def _on_permission_requests_changed(self, view: BrowserView) -> None:
        if view is self.current_view():
            self._update_permission_bar()

    def _update_permission_bar(self) -> None:
        view = self.current_view()
        self._permission_bar.show_request(view.page().next_permission_request() if view is not None else None)

    def _on_permission_answered(self, request: object, allow: bool, remember: bool) -> None:
        for index in range(self._tabs.count()):
            view = self._view_at(index)
            if view is not None and request in view.page().pending_permissions:
                view.page().answer_permission(request, allow, remember)
                break
        self._update_permission_bar()

    # ------------------------------------------------------------------ shutdown
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt API)
        # "Forget sites on close" runs when lilx quits (lilx.app.shutdown), not per window.
        while self._tabs.count():
            view = self._view_at(0)
            self._tabs.removeTab(0)
            if view is not None:
                self._stack.removeWidget(view)
                view.deleteLater()
        event.accept()
