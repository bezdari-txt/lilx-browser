"""Services shared by the whole application, created once in :mod:`lilx.app`."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtWebEngineCore import QWebEngineProfile

from lilx.core.bookmarks import BookmarkStore
from lilx.core.downloads import DownloadManager
from lilx.core.history import HistoryStore
from lilx.core.settings import SettingsManager
from lilx.core.storage import StorageBackend
from lilx.engine.browser_data import BrowserDataManager
from lilx.engine.extensions import ExtensionService
from lilx.engine.lilblock import LilBlock
from lilx.paths import AppPaths


@dataclass
class BrowserContext:
    paths: AppPaths
    storage: StorageBackend
    settings: SettingsManager
    history: HistoryStore
    downloads: DownloadManager
    profile: QWebEngineProfile
    data: BrowserDataManager
    lilblock: LilBlock
    bookmarks: BookmarkStore
    extensions: ExtensionService
    #: Set by the "Reset lilx" button; app.main() wipes data and restarts after shutdown.
    reset_requested: bool = field(default=False)
