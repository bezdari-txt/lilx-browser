"""Application bootstrap: wiring of services, the main window and orderly shutdown.

Startup order matters for QtWebEngine:
1. register the lilx:// scheme (before QApplication);
2. run offline per-site cleanup (before the profile opens its files);
3. create the profile, services and window.

Shutdown mirrors it: close the window (runtime cleanup), delete pages, delete
the profile so Chromium flushes its files, then run the offline cleanup again.
"""

from __future__ import annotations

import argparse
import faulthandler
import logging
import os
import signal
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QLibraryInfo, Qt, QTimer, QUrl
from PySide6.QtWidgets import QApplication, QFileDialog

# QtWebEngineWidgets must be imported before QApplication is created.
from PySide6 import QtWebEngineWidgets  # noqa: F401

from lilx import APP_NAME, __version__
from lilx.branding import app_icon, prepare_platform
from lilx.context import BrowserContext
from lilx.core.bookmarks import BookmarkStore
from lilx.core.downloads import DownloadManager
from lilx.core.history import HistoryStore
from lilx.core.network import NetworkMode, apply_network_mode
from lilx.core.privacy import run_offline_cleanup
from lilx.core.reset import factory_reset, finish_pending_reset
from lilx.core.settings import SettingsManager
from lilx.core.storage import PlainFileStorage, StorageError
from lilx.engine.api import InternalApi
from lilx.engine.browser_data import BrowserDataManager
from lilx.engine.extensions import ExtensionError, ExtensionService
from lilx.engine.lilblock import LilBlock, ProfileBlocker
from lilx.engine.permissions import PermissionService
from lilx.engine.profile import create_profile
from lilx.engine.scheme import SCHEME_BYTES, LilxSchemeHandler, register_scheme
from lilx.i18n import resolve_language, set_language, tr
from lilx.paths import AppPaths
from lilx.ui.main_window import MainWindow
from lilx.ui.windows import WindowManager
from lilx.ui.theme import page_css, resolve_theme

log = logging.getLogger("lilx")


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="lilx — minimalist, privacy-first browser")
    parser.add_argument("urls", nargs="*", help="addresses or search terms to open")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--profile", default="", metavar="NAME",
                        help="use a separate named profile (own settings, history, cookies, storage)")
    # Unknown options are passed to Qt/Chromium (e.g. --remote-debugging-port=9222).
    args, _ = parser.parse_known_args(argv)
    return args


def _configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug or os.environ.get("LILX_DEBUG") else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args = _parse_args(argv[1:])
    _configure_logging(args.debug)
    # A crash inside Qt then prints the Python stack instead of a bare "segmentation fault".
    faulthandler.enable()
    try:
        app, window, ctx = build(argv, AppPaths.default(args.profile))
    except (StartupError, ValueError) as exc:
        print(f"lilx: {exc}", file=sys.stderr)
        return 1

    if args.urls:
        for url in args.urls:
            window.open_url(url)
    else:
        window.new_tab(QUrl(ctx.settings.current.homepage))
    window.show()
    log.info("lilx %s started (data: %s)", __version__, ctx.paths.data_dir)

    _install_signal_handlers(app)
    exit_code = app.exec()
    shutdown(window, ctx)
    if ctx.reset_requested:
        _reset_and_restart(ctx.paths)
    return exit_code


def _reset_and_restart(paths: AppPaths) -> None:
    """Wipe all browser data (engine already stopped) and start a fresh lilx."""
    _, errors = factory_reset(paths)
    if errors:
        log.error("Reset finished with %d error(s); see messages above", len(errors))
    try:
        # Same interpreter (sys.executable is the virtualenv's python; on macOS sys.orig_argv[0]
        # can be the base framework python) with the original arguments (-m lilx, the lilx script, ...).
        # The new process waits for this one to exit and finishes the reset if files were still busy.
        env = {**os.environ, "LILX_WAIT_FOR_PID": str(os.getpid())}
        subprocess.Popen([sys.executable, *sys.orig_argv[1:]], env=env, start_new_session=True)
        log.info("lilx restarted after reset")
    except OSError as exc:
        log.error("Could not restart lilx after reset: %s", exc)


def _choose_download_dir(ctx: BrowserContext) -> None:
    """Ask for a new download folder with the native dialog.

    Deferred with a timer: the request comes from the lilx:// scheme handler, which must
    reply first; running a modal dialog inside it would nest event loops in Chromium code.
    """
    def run() -> None:
        try:
            current = str(ctx.downloads.target_directory())
        except OSError:
            current = str(Path.home())
        folder = QFileDialog.getExistingDirectory(QApplication.activeWindow(), tr("Choose download folder"), current)
        if folder:
            try:
                ctx.settings.set("download_dir", folder)
            except ValueError as exc:
                log.error("Cannot use %s for downloads: %s", folder, exc)

    QTimer.singleShot(0, run)


def _choose_extension(ctx: BrowserContext, kind: str) -> None:
    """Pick an extension .zip or unpacked folder and install it (deferred, like the folder dialog)."""
    def run() -> None:
        parent = QApplication.activeWindow()
        if kind == "zip":
            path, _ = QFileDialog.getOpenFileName(parent, tr("Install extension from .zip"), str(Path.home()),
                                                  tr("Extension archive (*.zip)"))
        else:
            path = QFileDialog.getExistingDirectory(parent, tr("Install unpacked extension (folder)"), str(Path.home()))
        if not path:
            return
        try:
            ctx.extensions.install(path)
        except ExtensionError:
            pass  # already listed on lilx://extensions

    QTimer.singleShot(0, run)


def _request_reset(ctx: BrowserContext) -> None:
    ctx.reset_requested = True
    # Let the API reply reach the page first, then close normally.
    QTimer.singleShot(200, QApplication.closeAllWindows)


def _install_signal_handlers(app: QApplication) -> None:
    """Ctrl+C / SIGTERM close all windows normally, so forget-on-close cleanup still runs."""
    def request_close(*_: object) -> None:
        QApplication.closeAllWindows()

    signal.signal(signal.SIGINT, request_close)
    signal.signal(signal.SIGTERM, request_close)
    # Python only runs signal handlers when the interpreter gets control; wake it up regularly.
    timer = QTimer(app)
    timer.timeout.connect(lambda: None)
    timer.start(300)


def _check_qt_plugins() -> None:
    """Fail with a clear message instead of Qt's cryptic "no platform plugin" abort.

    Recent macOS versions set the BSD "hidden" flag on files inside dot-directories
    (such as ``.venv``); Qt skips hidden files when looking for plugins.
    """
    if sys.platform != "darwin":
        return
    plugin = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "platforms" / "libqcocoa.dylib"
    try:
        hidden = bool(plugin.stat().st_flags & stat.UF_HIDDEN)
    except (OSError, AttributeError):
        return
    if hidden:
        raise StartupError(
            f"Qt plugins are marked hidden by macOS ({plugin.parent}).\n"
            "This happens when the virtualenv lives in a dot-directory such as .venv. "
            "Create it without a leading dot (python3 -m venv venv) or run: "
            f"chflags -R nohidden '{plugin.parents[2]}'"
        )


class StartupError(Exception):
    pass


def build(argv: list[str], paths: AppPaths) -> tuple[QApplication, MainWindow, BrowserContext]:
    """Create the application, all services and the (not yet shown) main window."""
    try:
        finish_pending_reset(paths)
        paths.ensure()
        storage = PlainFileStorage(paths.data_dir)
    except (OSError, StorageError) as exc:
        raise StartupError(f"cannot use data directory {paths.data_dir}: {exc}") from exc

    _check_qt_plugins()
    register_scheme()
    apply_network_mode(NetworkMode.DIRECT)
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    prepare_platform()

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(APP_NAME)  # Wayland app_id / X11 hint: matches packaging/linux/lilx.desktop

    settings = SettingsManager(storage)
    set_language(resolve_language(settings.current.language))
    settings.changed.connect(
        lambda key: set_language(resolve_language(settings.current.language)) if key == "language" else None
    )
    try:
        history = HistoryStore(paths.history_db)
    except (sqlite3.Error, OSError) as exc:
        log.error("History database unavailable (%s); history is kept in memory for this session", exc)
        history = HistoryStore(Path(":memory:"))

    # Sites marked "forget on close" may still have data on disk if lilx crashed.
    run_offline_cleanup(paths.webengine_dir, settings.current.forget_on_close)

    profile = create_profile(paths, settings)
    downloads = DownloadManager(storage, settings)
    profile.downloadRequested.connect(downloads.handle_request)
    data = BrowserDataManager(profile, history, settings)
    lilblock = LilBlock(settings, storage, paths.filters_dir)
    profile.setUrlRequestInterceptor(ProfileBlocker(lilblock, profile))
    bookmarks = BookmarkStore(storage)
    permissions = PermissionService(settings)
    # Same profile as every tab: extensions see and act on the user's normal browsing.
    extensions = ExtensionService(profile, storage)

    ctx = BrowserContext(
        paths=paths, storage=storage, settings=settings, history=history,
        downloads=downloads, profile=profile, data=data, lilblock=lilblock, bookmarks=bookmarks,
        extensions=extensions, permissions=permissions,
    )
    api = InternalApi(
        paths=paths, storage=storage, settings=settings, history=history, downloads=downloads,
        data=data, lilblock=lilblock, bookmarks=bookmarks, extensions=extensions,
        choose_extension=lambda kind: _choose_extension(ctx, kind), request_reset=lambda: _request_reset(ctx),
        choose_download_dir=lambda: _choose_download_dir(ctx),
    )

    def scheme_handler(private: bool, parent: object) -> LilxSchemeHandler:
        return LilxSchemeHandler(
            api,
            theme=lambda: resolve_theme(settings.current.theme),
            language=lambda: resolve_language(settings.current.language),
            theme_css=lambda: page_css(settings.current.theme, settings.current.accent_mode,
                                       settings.current.accent_color),
            parent=parent,
            private=private,
        )

    ctx.scheme_handler_factory = scheme_handler
    profile.installUrlSchemeHandler(SCHEME_BYTES, scheme_handler(False, app))
    app.setWindowIcon(app_icon())  # logo.png: windows, Dock, Cmd/Alt+Tab, taskbars

    ctx.windows = WindowManager(ctx)
    return app, ctx.windows.create_window(private=False), ctx


def shutdown(window: MainWindow, ctx: BrowserContext) -> None:
    # "Forget sites on close" needs the running profile (cookie store) to delete cookies.
    try:
        ctx.data.forget_sites_on_close()
    except Exception:
        log.exception("Forget-on-close cleanup failed")
    ctx.downloads.save()
    ctx.lilblock.log.save()
    # Delete views/pages before their profiles, otherwise Chromium keeps a profile alive.
    if ctx.windows is not None:
        ctx.windows.delete_all()  # every window, then the private (off-the-record) profile
    if shiboken6.isValid(window):
        shiboken6.delete(window)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)  # tab views
    if shiboken6.isValid(ctx.profile):
        shiboken6.delete(ctx.profile)
    run_offline_cleanup(ctx.paths.webengine_dir, ctx.settings.current.forget_on_close)
    ctx.history.close()
    log.info("lilx stopped")
