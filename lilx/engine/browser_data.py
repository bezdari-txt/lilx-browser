"""Browser data management while the engine is running: clearing data, per-site cleanup."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEngineProfile

from lilx.core.history import HistoryStore
from lilx.core.hosts import host_matches
from lilx.core.privacy import CleanupReport
from lilx.core.settings import SettingsManager, SiteRule

log = logging.getLogger(__name__)

_CookieKey = tuple[bytes, str, str]


class BrowserDataManager(QObject):
    """Runtime side of data management.

    Keeps an in-memory index of cookies (Qt offers no "list cookies" call, only
    change notifications) so that cookies of individual sites can be deleted.
    """

    def __init__(
        self,
        profile: QWebEngineProfile,
        history: HistoryStore,
        settings: SettingsManager,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile = profile
        self._history = history
        self._settings = settings
        self._cookies: dict[_CookieKey, QNetworkCookie] = {}
        store = profile.cookieStore()
        store.cookieAdded.connect(self._on_cookie_added)
        store.cookieRemoved.connect(self._on_cookie_removed)
        store.loadAllCookies()

    @staticmethod
    def _key(cookie: QNetworkCookie) -> _CookieKey:
        return bytes(cookie.name().data()), cookie.domain(), cookie.path()

    def _on_cookie_added(self, cookie: QNetworkCookie) -> None:
        self._cookies[self._key(cookie)] = QNetworkCookie(cookie)

    def _on_cookie_removed(self, cookie: QNetworkCookie) -> None:
        self._cookies.pop(self._key(cookie), None)

    # -- queries -------------------------------------------------------------
    def cookie_count(self) -> int:
        return len(self._cookies)

    def site_count(self) -> int:
        return len({c.domain().lstrip(".") for c in self._cookies.values()})

    # -- clearing --------------------------------------------------------------
    def clear_history(self) -> None:
        self._history.clear()
        self._profile.clearAllVisitedLinks()

    def clear_cookies(self) -> None:
        self._profile.cookieStore().deleteAllCookies()

    def clear_cache(self) -> None:
        self._profile.clearHttpCache()

    def forget_sites(self, rules: list[SiteRule]) -> CleanupReport:
        """Delete history and cookies of the given sites through the running engine.

        Storage directories (IndexedDB, ...) are removed by the offline pass in
        :func:`lilx.core.privacy.run_offline_cleanup` once the engine has stopped.
        """
        report = CleanupReport()
        store = self._profile.cookieStore()
        for rule in rules:
            if rule.clear_history:
                report.history_removed += self._history.delete_host(rule.host)
            if rule.clear_cookies:
                for key, cookie in list(self._cookies.items()):
                    if host_matches(cookie.domain(), rule.host):
                        store.deleteCookie(cookie)
                        self._cookies.pop(key, None)
                        report.cookies_removed += 1
        if rules:
            log.info("Forgot %d site(s): %d history entries, %d cookies",
                     len(rules), report.history_removed, report.cookies_removed)
        return report

    def forget_sites_on_close(self) -> CleanupReport:
        return self.forget_sites(self._settings.current.forget_on_close)
