"""Creation and configuration of the persistent QWebEngineProfile."""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import QObject
from PySide6.QtWebEngineCore import (
    QWebEngineCookieStore,
    QWebEngineProfile,
    QWebEngineProfileBuilder,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
)

from lilx.core.settings import SettingsManager
from lilx.paths import AppPaths

log = logging.getLogger(__name__)

PROFILE_NAME = "default"
_QTWEBENGINE_UA_TOKEN = re.compile(r" QtWebEngine/[\d.]+")

_Attr = QWebEngineSettings.WebAttribute
_Font = QWebEngineSettings.FontFamily
_Size = QWebEngineSettings.FontSize
# Engine defaults, captured once so that "" (system default) can be restored.
_DEFAULT_FONTS: dict[_Font, str] = {}


def apply_privacy_headers(info: QWebEngineUrlRequestInfo, settings: SettingsManager) -> None:
    """Global Privacy Control / Do Not Track. Called by both the profile and the page
    interceptors: a page with its own interceptor bypasses the profile one."""
    if settings.current.send_privacy_signals:
        info.setHttpHeader(b"Sec-GPC", b"1")
        info.setHttpHeader(b"DNT", b"1")


def _block_third_party(request: QWebEngineCookieStore.FilterRequest) -> bool:
    return not request.thirdParty


def create_profile(paths: AppPaths, settings: SettingsManager) -> QWebEngineProfile:
    builder = QWebEngineProfileBuilder()
    builder.setPersistentStoragePath(str(paths.webengine_dir))
    builder.setCachePath(str(paths.webengine_cache_dir))
    builder.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
    builder.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
    # Site permissions (camera, location, ...) are never written to disk.
    builder.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime)
    profile = builder.createProfile(PROFILE_NAME)

    # The default user agent advertises "QtWebEngine/x.y", which makes lilx
    # easier to fingerprint and breaks some sites. Remove just that token.
    profile.setHttpUserAgent(_QTWEBENGINE_UA_TOKEN.sub("", profile.httpUserAgent()))

    # The profile-wide request interceptor (privacy headers + lilBlock) is installed by
    # lilx.app once lilBlock exists: see lilx.engine.lilblock.ProfileBlocker.

    web = profile.settings()
    web.setAttribute(_Attr.DnsPrefetchEnabled, False)
    web.setAttribute(_Attr.HyperlinkAuditingEnabled, False)
    web.setAttribute(_Attr.WebRTCPublicInterfacesOnly, True)
    web.setAttribute(_Attr.FullScreenSupportEnabled, True)
    web.setAttribute(_Attr.PluginsEnabled, True)  # needed by the built-in PDF viewer
    web.setAttribute(_Attr.PdfViewerEnabled, True)
    web.setAttribute(_Attr.LocalContentCanAccessFileUrls, False)

    for family in (_Font.StandardFont, _Font.SansSerifFont, _Font.FixedFont):
        _DEFAULT_FONTS[family] = web.fontFamily(family)
    apply_settings(profile, settings)
    settings.changed.connect(lambda key: apply_settings(profile, settings))
    return profile


def apply_settings(profile: QWebEngineProfile, settings: SettingsManager) -> None:
    current = settings.current
    web = profile.settings()
    web.setAttribute(_Attr.JavascriptEnabled, current.javascript_enabled)
    web.setAttribute(_Attr.ScrollAnimatorEnabled, current.smooth_scrolling)
    web.setAttribute(_Attr.PlaybackRequiresUserGesture, current.block_autoplay)
    # Fonts: a site's own web fonts still win; these are used where a page does not choose.
    for family in (_Font.StandardFont, _Font.SansSerifFont):
        web.setFontFamily(family, current.font_standard or _DEFAULT_FONTS.get(family, ""))
    web.setFontFamily(_Font.FixedFont, current.font_fixed or _DEFAULT_FONTS.get(_Font.FixedFont, ""))
    web.setFontSize(_Size.DefaultFontSize, current.font_size)
    web.setFontSize(_Size.DefaultFixedFontSize, max(current.font_size - 3, 9))
    store = profile.cookieStore()
    store.setCookieFilter(_block_third_party if current.block_third_party_cookies else None)
