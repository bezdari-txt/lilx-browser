"""The ``lilx://`` URL scheme: internal pages, their assets and their JSON API.

URL layout (host syntax, each page is its own origin):

    lilx://home/                 -> resources/pages/home.html
    lilx://home/assets/lilx.css  -> resources/pages/assets/lilx.css
    lilx://home/api/home.data?p={json}  -> InternalApi (same-origin fetch)

Security model:
* web content cannot navigate to or embed lilx:// pages
  (:meth:`lilx.engine.page.BrowserPage.acceptNavigationRequest` and the
  ``frame-ancestors 'none'`` policy);
* API calls are accepted only when the initiator is the same lilx:// page,
  and state-changing methods require POST;
* pages carry a strict Content-Security-Policy (no inline scripts or styles,
  no remote resources).

The scheme is intentionally *not* registered as ``LocalScheme``: Chromium gives
local schemes an opaque ("null") origin, like file://, which would make every
page cross-origin to its own API.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QUrl, QUrlQuery
from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler

from lilx.branding import logo_png
from lilx.engine.api import InternalApi
from lilx.paths import PAGES_DIR

log = logging.getLogger(__name__)

SCHEME = "lilx"
SCHEME_BYTES = QByteArray(SCHEME.encode())
INTERNAL_PAGES = ("home", "settings", "history", "downloads", "extensions")

_ASSETS_DIR = PAGES_DIR / "assets"
_CONTENT_TYPES = {
    ".html": b"text/html; charset=utf-8",
    ".css": b"text/css; charset=utf-8",
    ".js": b"text/javascript; charset=utf-8",
    ".svg": b"image/svg+xml",
    ".json": b"application/json",
    ".png": b"image/png",
    ".woff2": b"font/woff2",
}
# Header values are lists: PySide maps QMultiMap<QByteArray, QByteArray> to dict[key, list].
_RESPONSE_HEADERS = {
    QByteArray(b"Content-Security-Policy"): [QByteArray(b"frame-ancestors 'none'")],
    QByteArray(b"X-Frame-Options"): [QByteArray(b"DENY")],
    QByteArray(b"Cache-Control"): [QByteArray(b"no-store")],
    QByteArray(b"X-Content-Type-Options"): [QByteArray(b"nosniff")],
}

_Error = QWebEngineUrlRequestJob.Error


def register_scheme() -> None:
    """Register lilx://. Must be called before QApplication is created."""
    scheme = QWebEngineUrlScheme(SCHEME_BYTES)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.FetchApiAllowed)
    QWebEngineUrlScheme.registerScheme(scheme)


def internal_url(page: str) -> QUrl:
    return QUrl(f"{SCHEME}://{page}/")


def is_internal(url: QUrl) -> bool:
    return url.scheme() == SCHEME


class LilxSchemeHandler(QWebEngineUrlSchemeHandler):
    def __init__(
        self,
        api: InternalApi,
        theme: Callable[[], str],
        language: Callable[[], str],
        theme_css: Callable[[], str],
        parent: QObject | None = None,
        private: bool = False,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._theme = theme
        self._language = language
        self._theme_css = theme_css
        self._private = private  # handler of the private profile: pages show that they are private

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802 (Qt API)
        try:
            self._handle(job)
        except Exception:
            log.exception("lilx:// request failed: %s", job.requestUrl().toString())
            job.fail(_Error.RequestFailed)

    def _handle(self, job: QWebEngineUrlRequestJob) -> None:
        url = job.requestUrl()
        page = url.host()
        path = url.path() or "/"

        if page not in INTERNAL_PAGES:
            job.fail(_Error.UrlNotFound)
        elif path.startswith("/api/"):
            self._handle_api(job, page, path.removeprefix("/api/"))
        elif path.startswith("/assets/"):
            self._serve_asset(job, path.removeprefix("/assets/"))
        elif path in ("/", "/index.html"):
            self._serve_page(job, page)
        else:
            job.fail(_Error.UrlNotFound)

    # -- responses -------------------------------------------------------------
    @staticmethod
    def _reply(job: QWebEngineUrlRequestJob, content_type: bytes, data: bytes) -> None:
        buffer = QBuffer(job)  # owned by the job, freed with it
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.setAdditionalResponseHeaders(_RESPONSE_HEADERS)
        job.reply(QByteArray(content_type), buffer)

    def _serve_page(self, job: QWebEngineUrlRequestJob, page: str) -> None:
        html = (PAGES_DIR / f"{page}.html").read_text(encoding="utf-8")
        html = (html.replace("{{theme}}", self._theme()).replace("{{lang}}", self._language())
                .replace("{{private}}", "1" if self._private else "0"))
        self._reply(job, _CONTENT_TYPES[".html"], html.encode("utf-8"))

    def _serve_asset(self, job: QWebEngineUrlRequestJob, relative: str) -> None:
        if relative == "logo.png":  # tab icon of lilx:// pages, from the project's logo.png
            data = logo_png(64)
            if data:
                self._reply(job, _CONTENT_TYPES[".png"], data)
            else:
                job.fail(_Error.UrlNotFound)
            return
        if relative == "theme.css":  # generated: the user's accent color
            self._reply(job, _CONTENT_TYPES[".css"], self._theme_css().encode("utf-8"))
            return
        target = (_ASSETS_DIR / relative).resolve()
        content_type = _CONTENT_TYPES.get(target.suffix)
        if not target.is_relative_to(_ASSETS_DIR.resolve()) or not target.is_file() or content_type is None:
            job.fail(_Error.UrlNotFound)
            return
        self._reply(job, content_type, Path(target).read_bytes())

    def _handle_api(self, job: QWebEngineUrlRequestJob, page: str, name: str) -> None:
        initiator = job.initiator()
        if initiator.scheme() != SCHEME or initiator.host() != page:
            log.warning("Blocked lilx API call %s from %s", name, initiator.toString() or "<browser>")
            job.fail(_Error.RequestDenied)
            return

        http_method = bytes(job.requestMethod().data()).decode("ascii", "replace").upper()
        query = QUrlQuery(job.requestUrl())
        # Arguments arrive as JSON in the "p" query parameter. Request bodies are not
        # used on purpose: QWebEngineUrlRequestJob.requestBody() crashes in PySide6 6.11.
        raw = query.queryItemValue("p", QUrl.ComponentFormattingOption.FullyDecoded)
        params: dict[str, object] = {}
        if raw:
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                decoded = None
            if not isinstance(decoded, dict):
                self._reply(job, b"application/json", b'{"ok": false, "error": "invalid parameters"}')
                return
            params = decoded

        result = self._api.call(page, name, params, http_method)
        self._reply(job, b"application/json", json.dumps(result, ensure_ascii=False).encode("utf-8"))
