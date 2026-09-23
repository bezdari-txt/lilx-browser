"""End-to-end smoke test: starts lilx with a throwaway profile and drives it.

    QT_QPA_PLATFORM=offscreen python scripts/smoke_test.py [--screenshots DIR]

Uses a local HTTP server only (no internet access needed).
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Checks count tabs right after closing them; skip the close animation.
os.environ.setdefault("LILX_NO_ANIMATIONS", "1")
# Virtual camera/microphone so media permission requests can be exercised headless.
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
                                          + " --use-fake-device-for-media-stream").strip()

import shiboken6  # noqa: E402
from PySide6.QtCore import QEventLoop, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lilx.app import build, shutdown  # noqa: E402
from lilx.core.reset import factory_reset  # noqa: E402
from lilx.core.settings import SiteRule  # noqa: E402
from lilx.paths import AppPaths  # noqa: E402

PAYLOAD = os.urandom(256 * 1024)
SLOW_PAYLOAD = os.urandom(6 * 1024 * 1024)
REQUESTS: list[tuple[str, dict[str, str]]] = []  # (path, headers) seen by the test server


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        REQUESTS.append((self.path, dict(self.headers)))
        if self.path.startswith("/slow.bin"):  # ~3 s download, so it can be paused and cancelled
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="slow.bin"')
            self.send_header("Content-Length", str(len(SLOW_PAYLOAD)))
            self.end_headers()
            try:
                for start in range(0, len(SLOW_PAYLOAD), 65536):
                    self.wfile.write(SLOW_PAYLOAD[start:start + 65536])
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass  # the browser cancelled the download
            return
        if self.path.startswith("/page"):  # plain page for windows / permission checks
            self._send(b"<!doctype html><title>Page</title><input id=i>page", "text/html; charset=utf-8")
            return
        if self.path.startswith("/ads"):
            if self.path.startswith("/adsbygoogle.js"):
                self._send(b"window.adLoaded = true;", "text/javascript")
                return
            self._send(
                b"<!doctype html><title>Ad page</title><p>content</p>"
                b'<script src="/adsbygoogle.js"></script>'
                b'<img src="https://pagead2.googlesyndication.com/pagead/banner.png">'
                b'<img src="https://tracker.doubleclick.net/pixel.gif">',
                "text/html; charset=utf-8",
            )
            return
        if self.path.startswith("/file.bin"):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", 'attachment; filename="file.bin"')
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
            return
        body = (
            "<!doctype html><title>Local Test</title><h1>hello</h1>"
            '<a id="pop" href="/other" target="_blank">popup</a>'
            '<a id="internal" href="lilx://settings/">settings</a>'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", "session=abc; Max-Age=86400; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


class Smoke:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.failures: list[str] = []

    def wait(self, condition: Callable[[], bool], timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            if condition():
                return True
        return False

    def js(self, view: Any, script: str, timeout: float = 10.0) -> Any:
        box: dict[str, Any] = {}
        view.page().runJavaScript(script, 0, lambda result: box.setdefault("r", result))
        self.wait(lambda: "r" in box, timeout)
        return box.get("r")

    def js_async(self, view: Any, expression: str, timeout: float = 10.0) -> Any:
        """Evaluate a JS expression that may return a promise and wait for its value."""
        self.js(view, "window.__smoke = undefined; Promise.resolve().then(() => " + expression + ")"
                      ".then(v => { window.__smoke = {v}; }, e => { window.__smoke = {e: String(e)}; });")
        box: dict[str, Any] = {}

        def done() -> bool:
            # PySide cannot convert JS objects, so the result travels as a JSON string.
            result = self.js(view, "window.__smoke === undefined ? null : JSON.stringify(window.__smoke)")
            if result:
                box["r"] = json.loads(result)
            return "r" in box

        self.wait(done, timeout)
        result = box.get("r") or {}
        return result.get("v", result.get("e"))

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}{' — ' + detail if detail and not ok else ''}")
        if not ok:
            self.failures.append(name)

    def load(self, view: Any, url: str) -> bool:
        done: list[bool] = []
        view.loadFinished.connect(done.append)
        view.load(QUrl(url))
        finished = self.wait(lambda: bool(done))
        view.loadFinished.disconnect(done.append)
        return finished and done[-1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"127.0.0.1:{server.server_address[1]}"

    tmp = tempfile.TemporaryDirectory(prefix="lilx-smoke-")
    root = Path(tmp.name)
    downloads_dir = root / "dl"
    downloads_dir.mkdir()
    paths = AppPaths(data_dir=root / "data", cache_dir=root / "cache")

    app, window, ctx = build([sys.argv[0]], paths)
    s = Smoke(app)
    window.resize(1200, 800)
    window.show()
    view = window.new_tab()
    s.wait(lambda: view.url().host() == "home" and not view.is_loading)

    print("internal pages")
    checks = {
        "home": "document.querySelector('#search-input').placeholder.includes('DuckDuckGo')",
        "settings": "document.querySelectorAll('#engines input').length === 3 && "
                    "document.querySelector('#about').textContent.includes('Chromium')",
        "history": "document.querySelector('#entries').children.length > 0",
        "downloads": "document.querySelector('#directory').textContent !== '…'",
    }
    for page, condition in checks.items():
        loaded = s.load(view, f"lilx://{page}/")
        rendered = loaded and s.wait(lambda: s.js(view, condition) is True, 10)
        s.check(f"lilx://{page} renders", bool(rendered))
        if args.screenshots:
            args.screenshots.mkdir(parents=True, exist_ok=True)
            s.wait(lambda: False, 0.6)
            window.grab().save(str(args.screenshots / f"{page}.png"))

    print("settings API")
    s.load(view, "lilx://settings/")
    s.wait(lambda: s.js(view, "document.querySelectorAll('#engines input').length === 3") is True)
    s.js_async(view, "lilx.post('settings.set', {key: 'search_engine', value: 'google'})")
    s.check("settings.set changes engine", s.wait(lambda: ctx.settings.current.search_engine == "google", 5))
    bad = s.js_async(view, "lilx.post('settings.set', {key: 'theme', value: 'neon'}).then(() => 'accepted', e => e.message)")
    s.check("invalid setting rejected", isinstance(bad, str) and "theme" in bad, str(bad))
    cross = s.js_async(view, "lilx.get('history.list').then(() => 'allowed', e => 'denied')")
    s.check("page cannot call another page's namespace", cross == "denied", str(cross))
    ctx.settings.set("download_dir", str(downloads_dir))
    ctx.settings.set("search_engine", "duckduckgo")

    print("download folder")
    from PySide6.QtWidgets import QFileDialog

    picked = root / "picked"
    picked.mkdir()
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(picked))  # stand-in for the native dialog
    s.load(view, "lilx://downloads/")
    s.wait(lambda: s.js(view, "document.getElementById('directory').textContent") == str(downloads_dir.resolve()), 5)
    s.js(view, "document.getElementById('change-dir').click()")
    s.check("folder chosen from the downloads page is saved",
            s.wait(lambda: ctx.settings.current.download_dir == str(picked.resolve()), 5), ctx.settings.current.download_dir)
    s.check("downloads page updates without reload",
            s.wait(lambda: s.js(view, "document.getElementById('directory').textContent") == str(picked.resolve()), 5))
    s.load(view, "lilx://settings/")
    s.wait(lambda: s.js(view, "document.getElementById('download-dir-current').textContent") == str(picked.resolve()), 5)
    s.js(view, "document.getElementById('download-dir-default').click()")
    s.check("'Use default' resets the folder", s.wait(lambda: ctx.settings.current.download_dir == "", 5))
    ctx.settings.set("download_dir", str(downloads_dir))
    s.check("settings page follows external changes", s.wait(
        lambda: s.js(view, "document.getElementById('download-dir-current').textContent") == str(downloads_dir.resolve()), 5))

    print("navigation and history")
    window.navigate(base)
    s.wait(lambda: view.title() == "Local Test")
    s.check("address resolved to local http", view.url().toString().startswith(f"http://{base}"), view.url().toString())
    s.check("visit recorded in history", s.wait(lambda: ctx.history.count() == 1, 5), str(ctx.history.count()))
    s.check("title stored", ctx.history.search()[0].title == "Local Test" if ctx.history.count() else False)
    blocked = s.js_async(view, "fetch('lilx://settings/api/settings.get').then(r => 'reachable', e => 'blocked')")
    s.check("web page cannot reach lilx:// API", blocked == "blocked", str(blocked))
    s.js(view, "document.getElementById('internal').click()")
    s.wait(lambda: False, 1.5)
    s.check("web page cannot open lilx:// pages", view.url().scheme() == "http", view.url().toString())
    framed = s.js_async(view, "new Promise(ok => { const f = document.createElement('iframe'); "
                              "f.src = 'lilx://settings/'; f.onload = () => ok(f.contentDocument ? 'loaded' : 'blocked'); "
                              "document.body.append(f); setTimeout(() => ok('blocked'), 2000); })")
    s.check("web page cannot frame lilx:// pages", framed == "blocked", str(framed))
    s.check("cookie recorded", s.wait(lambda: ctx.data.cookie_count() >= 1, 5), str(ctx.data.cookie_count()))
    root_request = next((h for path, h in REQUESTS if path == "/"), {})
    s.check("Sec-GPC header sent (profile interceptor still active)", root_request.get("Sec-GPC") == "1",
            str(root_request.get("Sec-GPC")))

    print("lilBlock")
    REQUESTS.clear()
    s.load(view, f"http://{base}/ads")
    s.wait(lambda: view.blocked_count >= 3, 5)
    s.check("ad requests blocked on the page", view.blocked_count == 3, str(view.blocked_count))
    s.check("blocked script never reached the server", not any(p.startswith("/adsbygoogle") for p, _ in REQUESTS))
    s.check("page itself still loads", view.title() == "Ad page", view.title())
    s.check("block history recorded", ctx.lilblock.log.total >= 3, str(ctx.lilblock.log.total))
    nav = window._nav  # noqa: SLF001
    s.check("counter shown next to the address bar", nav.lilblock.text() == "3", repr(nav.lilblock.text()))
    ctx.settings.allow_ads("127.0.0.1")
    REQUESTS.clear()
    s.load(view, f"http://{base}/ads")
    s.wait(lambda: any(p.startswith("/adsbygoogle") for p, _ in REQUESTS), 5)
    s.check("site exception disables blocking", view.blocked_count == 0 and nav.lilblock.text() == "",
            f"count={view.blocked_count} text={nav.lilblock.text()!r}")
    ctx.settings.clear_allowlist()
    ctx.settings.set("adblock_enabled", False)
    s.load(view, f"http://{base}/ads")
    s.check("global switch disables blocking", view.blocked_count == 0, str(view.blocked_count))
    ctx.settings.set("adblock_enabled", True)
    s.load(view, f"http://{base}/ads")
    s.check("blocking back on", s.wait(lambda: view.blocked_count == 3, 5), str(view.blocked_count))

    print("animations")
    os.environ.pop("LILX_NO_ANIMATIONS", None)  # exercise the real animation code paths
    for _ in range(4):
        window._on_link_hovered("lilx://settings")  # noqa: SLF001 (link preview fade in)
        s.wait(lambda: False, 0.3)
        window._on_link_hovered("")  # noqa: SLF001 (fade out)
        s.wait(lambda: False, 0.3)
    anim_tab = window.new_tab(QUrl(f"http://{base}/"))
    s.wait(lambda: False, 0.4)
    window.close_tab(window._index_of(anim_tab))  # noqa: SLF001
    s.wait(lambda: False, 0.4)
    window._nav.menu_button.menu().popup(window.mapToGlobal(window.rect().center()))  # noqa: SLF001
    s.wait(lambda: False, 0.4)
    window._nav.menu_button.menu().hide()  # noqa: SLF001
    s.check("hover, tab and menu animations run without crashing", True)
    os.environ["LILX_NO_ANIMATIONS"] = "1"

    print("bookmarks")
    s.load(view, f"http://{base}/")
    window.toggle_bookmark()
    s.check("star adds the current page", ctx.bookmarks.find(view.url().toString()) is not None)
    s.check("star is filled", window._nav._bookmarked)  # noqa: SLF001
    s.load(view, "lilx://home/")
    s.wait(lambda: s.js(view, "document.querySelectorAll('#bookmarks .tile-wrap').length") == 1, 5)
    s.check("home page shows the bookmark", s.js(view, "document.querySelectorAll('#bookmarks .tile-wrap').length") == 1)
    s.js(view, "document.querySelector('#bookmarks .add-tile').click();"
               "document.getElementById('bm-url').value = 'example.org';"
               "document.getElementById('bm-title').value = 'Example';"
               "document.getElementById('bookmark-form').requestSubmit()")
    s.check("bookmark added on the home page", s.wait(lambda: ctx.bookmarks.find("https://example.org") is not None, 5),
            str([b.url for b in ctx.bookmarks.all()]))
    s.wait(lambda: s.js(view, "document.querySelectorAll('#bookmarks .tile-wrap').length") == 2, 5)
    s.js(view, "document.querySelector('#bookmarks .tile-wrap .tile-remove').click()")
    s.check("bookmark removed on the home page", s.wait(lambda: len(ctx.bookmarks.all()) == 1, 5))
    bad = s.js_async(view, "lilx.post('home.bookmark_add', {url: 'just some words'}).then(() => 'added', e => 'rejected')")
    s.check("searches are not accepted as bookmarks", bad == "rejected", str(bad))
    ctx.settings.set("show_home_bookmarks", False)
    s.check("bookmarks can be hidden on the home page",
            s.wait(lambda: s.js(view, "document.getElementById('bookmarks-section').hidden") is True, 5))
    ctx.settings.set("show_home_bookmarks", True)

    print("frequently visited")
    s.load(view, "lilx://home/")
    s.wait(lambda: (s.js(view, "document.querySelectorAll('#tiles .tile-wrap').length") or 0) >= 1, 5)
    top_host = s.js(view, "document.querySelector('#tiles .tile .name').textContent")
    s.check("frequently visited shown on home", bool(top_host), str(top_host))
    s.js(view, "document.querySelector('#tiles .tile-wrap .tile-remove').click()")
    s.check("site removed from frequently visited", s.wait(lambda: len(ctx.settings.current.top_sites_hidden) == 1, 5))
    s.check("home no longer shows it", s.wait(
        lambda: s.js(view, "document.getElementById('top-section').hidden") is True, 5))
    s.check("its history is kept", ctx.history.count() > 0)
    ctx.settings.restore_top_sites()
    s.check("restored site is back", s.wait(
        lambda: s.js(view, "document.getElementById('top-section').hidden") is False, 5))
    s.js(view, "document.getElementById('hide-top-sites').click()")
    s.check("'Hide' turns the section off", s.wait(lambda: ctx.settings.current.show_top_sites is False, 5))
    ctx.settings.set("show_top_sites", True)

    print("search")
    s.load(view, "lilx://settings/")
    s.wait(lambda: (s.js(view, "document.querySelectorAll('#content-selects select').length") or 0) == 4, 5)
    def visible_cards(query: str) -> Any:
        s.js(view, f"{{ const q = document.getElementById('settings-query'); q.value = {query!r};"
                   "q.dispatchEvent(new Event('input')); }")
        return s.js(view, "[...document.querySelectorAll('main > .card')].filter(c => !c.hidden)"
                          ".map(c => c.id || c.querySelector('h2').textContent).join(',')")
    found = visible_cards("шрифт")
    s.check("settings search finds fonts (ru)", found == "content", str(found))
    found = visible_cards("font")
    s.check("settings search works in the other language too", found == "content", str(found))
    found = visible_cards("zzzz")
    s.check("settings search shows 'nothing found'", found == "" and
            s.js(view, "document.getElementById('settings-no-match').hidden") is False, str(found))
    visible_cards("")

    print("accent color")
    s.load(view, "lilx://settings/")
    s.wait(lambda: (s.js(view, "document.querySelectorAll('#accent-modes input').length") or 0) == 2, 5)
    s.check("accent editor hidden for the default color", s.js(view, "document.getElementById('accent-editor').hidden") is True)
    s.js(view, "document.querySelector('#accent-modes input[value=custom]').click()")
    s.check("custom accent mode saved", s.wait(lambda: ctx.settings.current.accent_mode == "custom", 5))
    s.check("editor shown", s.wait(lambda: s.js(view, "document.getElementById('accent-editor').hidden") is False, 5))
    s.js(view, "document.querySelector('.swatch[title=\"#22c55e\"]').click()")
    s.check("preset color saved", s.wait(lambda: ctx.settings.current.accent_color == "#22c55e", 5))
    s.check("browser chrome uses it", window._palette.accent == "#22c55e", window._palette.accent)  # noqa: SLF001
    page_accent = "getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()"
    s.check("open page switches accent without reload", s.wait(lambda: s.js(view, page_accent) == "#22c55e", 5),
            str(s.js(view, page_accent)))
    s.js(view, "{ const r = document.querySelector('.ch-r input[type=range]'); r.value = '200';"
               "r.dispatchEvent(new Event('change')); }")
    s.check("RGB slider changes the color", s.wait(lambda: ctx.settings.current.accent_color == "#c8c55e", 5),
            ctx.settings.current.accent_color)
    s.js(view, "{ const x = document.getElementById('accent-hex'); x.value = '3b82f6'; x.dispatchEvent(new Event('change')); }")
    s.check("HEX field works without '#'", s.wait(lambda: ctx.settings.current.accent_color == "#3b82f6", 5))
    if args.screenshots:
        s.js(view, "document.getElementById('accent-editor').scrollIntoView({block: 'center'})")
        s.wait(lambda: False, 0.6)
        window.grab().save(str(args.screenshots / "accent.png"))
    s.js(view, "document.querySelector('#accent-modes input[value=default]').click()")
    s.check("back to the default accent", s.wait(lambda: window._palette.accent == "#7456e8", 5),  # noqa: SLF001
            window._palette.accent)  # noqa: SLF001

    print("content settings")
    s.load(view, "lilx://settings/")
    s.wait(lambda: (s.js(view, "document.querySelectorAll('#content-selects select').length") or 0) == 4, 5)
    fonts = s.js(view, "document.querySelectorAll('#content-selects select')[0].options.length")
    s.check("system fonts listed", isinstance(fonts, (int, float)) and fonts > 5, str(fonts))
    s.js(view, "const z = document.querySelectorAll('#content-selects select')[3]; z.value = '125';"
               "z.dispatchEvent(new Event('change'))")
    s.check("default zoom saved", s.wait(lambda: ctx.settings.current.default_zoom == 125, 5))
    zoomed = window.new_tab(QUrl(f"http://{base}/"))
    s.wait(lambda: not zoomed.is_loading, 5)
    s.check("new tabs use the default zoom", abs(zoomed.zoomFactor() - 1.25) < 0.01, str(zoomed.zoomFactor()))
    window.close_tab(window._index_of(zoomed))  # noqa: SLF001
    ctx.settings.set("default_zoom", 100)
    ctx.settings.set("font_size", 18)
    web = ctx.profile.settings()
    from PySide6.QtWebEngineCore import QWebEngineSettings

    s.check("font size applied to the engine",
            web.fontSize(QWebEngineSettings.FontSize.DefaultFontSize) == 18)
    ctx.settings.set("font_size", 16)
    window._tabs.setCurrentIndex(0)  # noqa: SLF001

    print("extensions")
    import shutil
    import zipfile

    fixture = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "extensions" / "hello-mv3"
    ext_zip = root / "hello-mv3.zip"
    with zipfile.ZipFile(ext_zip, "w") as archive:
        for file in fixture.iterdir():
            archive.write(file, file.name)
    mv2 = root / "old-mv2"
    mv2.mkdir()
    (mv2 / "manifest.json").write_text('{"manifest_version": 2, "name": "Old", "version": "1"}')
    broken = root / "broken"
    broken.mkdir()
    (broken / "manifest.json").write_text('{"manifest_version": 3, "name": "Broken", "version": "1",'
                                          '"content_scripts": [{"matches": ["<all_urls>"], "js": ["missing.js"]}]}')
    marker = "document.documentElement.dataset.helloLilx || 'none'"

    def install(path: Path) -> Any:
        return s.js_async(view, f"lilx.post('extensions.install_path', {{path: {str(path)!r}}}).then(() => 'ok', e => e.message)")

    def extension_rows() -> Any:
        return s.js(view, "document.querySelectorAll('#list .ext').length")

    s.load(view, "lilx://extensions/")
    s.check("lilx://extensions renders", s.wait(
        lambda: s.js(view, "!!document.querySelector('#list .empty')") is True, 5))
    s.check("install from folder accepted", install(fixture) == "ok")
    s.check("installed extension listed", s.wait(lambda: extension_rows() == 1, 10), str(extension_rows()))
    info = ctx.extensions.installed()[0] if ctx.extensions.installed() else None
    s.check("new extension is enabled", info is not None and info.isEnabled())
    s.check("extension uses the tabs' profile", ctx.profile.extensionManager() is not None and info is not None)
    s.load(view, f"http://{base}/")
    s.check("content script runs in normal tabs", s.wait(lambda: s.js(view, marker) == "active", 5),
            str(s.js(view, marker)))
    popup = window.open_extension_popup(info.id()) if info else None
    popup_text = "document.getElementById('out') && document.getElementById('out').textContent"
    s.check("action popup opens from actionPopupUrl()", popup is not None and s.wait(
        lambda: str(popup.view.page().url().toString()).startswith("chrome-extension://")
        and (s.js(popup.view, popup_text) or "").startswith("runtime id: "), 8),
        str(popup and s.js(popup.view, popup_text)))
    if popup is not None:
        popup.close()
    s.load(view, "lilx://extensions/")
    s.wait(lambda: extension_rows() == 1, 5)
    s.js(view, "document.querySelector('#list .ext .switch input').click()")
    s.check("disable from lilx://extensions", s.wait(lambda: not ctx.extensions.installed()[0].isEnabled(), 5))
    saved = json.loads((paths.data_dir / "extensions.json").read_text())
    s.check("enabled state saved", saved["enabled"].get(info.id()) is False, str(saved))
    s.load(view, f"http://{base}/")
    s.check("disabled extension does not run", s.wait(lambda: s.js(view, marker) == "none", 5))
    s.load(view, "lilx://extensions/")
    s.wait(lambda: extension_rows() == 1, 5)
    s.check("install from .zip accepted", install(ext_zip) == "ok")
    s.check("reinstalling updates instead of duplicating", s.wait(
        lambda: len(ctx.extensions.installed()) == 1 and ctx.extensions.installed()[0].id() != info.id(), 10),
        str([(i.id(), i.name()) for i in ctx.extensions.installed()]))
    s.check("update keeps the disabled state", not ctx.extensions.installed()[0].isEnabled())
    install(broken)
    install(mv2)
    s.check("installation errors are shown", s.wait(
        lambda: (s.js(view, "document.querySelectorAll('#errors .error-row').length") or 0) >= 2, 10),
        str(ctx.extensions.errors))
    s.check("Manifest V2 is rejected with a clear message",
            any("Manifest V3" in e["message"] for e in ctx.extensions.errors), str(ctx.extensions.errors))
    s.js(view, "{ const b = document.querySelector('#list .ext .danger'); b.click(); b.click(); }")
    s.check("uninstall", s.wait(lambda: len(ctx.extensions.installed()) == 0, 10))
    s.check("list is empty again", s.wait(lambda: s.js(view, "!!document.querySelector('#list .empty')") is True, 5))
    install(fixture)  # left installed: the restart check in the reset section needs one
    s.wait(lambda: len(ctx.extensions.installed()) == 1, 10)

    print("windows")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence
    from PySide6.QtTest import QTest

    mac = sys.platform == "darwin"
    s.check("Control+N shortcut (physical Control key)", window._actions["new_window"].shortcut()  # noqa: SLF001
            == QKeySequence("Meta+N" if mac else "Ctrl+N"))
    s.check("Control+Shift+N shortcut", window._actions["new_private_window"].shortcut()  # noqa: SLF001
            == QKeySequence("Meta+Shift+N" if mac else "Ctrl+Shift+N"))
    control = Qt.KeyboardModifier.MetaModifier if mac else Qt.KeyboardModifier.ControlModifier
    before = len(ctx.windows.windows)
    window.activateWindow()
    s.wait(lambda: False, 0.3)
    QTest.keyClick(window, Qt.Key.Key_N, control)
    s.check("Control+N opens a normal window", s.wait(lambda: len(ctx.windows.windows) == before + 1, 5),
            str(len(ctx.windows.windows)))
    normal2 = ctx.windows.windows[-1]
    s.check("new window is normal and shares the profile", not normal2.private and normal2._profile is ctx.profile)  # noqa: SLF001
    normal2.activateWindow()
    s.wait(lambda: False, 0.3)
    QTest.keyClick(normal2, Qt.Key.Key_N, control | Qt.KeyboardModifier.ShiftModifier)
    s.check("Control+Shift+N opens a private window", s.wait(lambda: len(ctx.windows.windows) == before + 2, 5))
    private = ctx.windows.windows[-1]
    private_profile = private._profile  # noqa: SLF001
    s.check("private window uses an off-the-record profile",
            private.private and private_profile.isOffTheRecord() and private_profile is not ctx.profile)
    badge = private._nav.private_badge  # noqa: SLF001
    s.check("private window is marked", badge.isVisibleTo(private) and "(" in private.windowTitle(),
            f"badge={badge.isVisibleTo(private)} title={private.windowTitle()!r}")
    s.check("normal window is not marked", not normal2._nav.private_badge.isVisibleTo(normal2))  # noqa: SLF001
    private2 = ctx.windows.open_window(private=True)
    s.check("private windows share one private session", private2._profile is private_profile)  # noqa: SLF001

    # cookies / storage isolation and no history from private windows
    history_before = ctx.history.count()
    pview = private.current_view()
    s.load(pview, f"http://{base}/page?private-visit")
    s.js(pview, "document.cookie = 'privateonly=1; path=/'; localStorage.setItem('where', 'private')")
    nview = normal2.current_view()
    s.load(nview, f"http://{base}/page?normal-visit")
    s.js(nview, "document.cookie = 'normalonly=1; path=/'; localStorage.setItem('where', 'normal')")
    s.load(pview, f"http://{base}/page?private-again")
    s.load(nview, f"http://{base}/page?normal-again")
    p_cookies, n_cookies = s.js(pview, "document.cookie"), s.js(nview, "document.cookie")
    s.check("private cookies isolated from normal", "normalonly" not in p_cookies and "privateonly" in p_cookies, p_cookies)
    s.check("normal cookies isolated from private", "privateonly" not in n_cookies and "normalonly" in n_cookies, n_cookies)
    s.check("localStorage isolated", s.js(pview, "localStorage.getItem('where')") == "private"
            and s.js(nview, "localStorage.getItem('where')") == "normal")
    s.check("private visits are not added to history",
            not any("private" in e.url for e in ctx.history.search("private")), str([e.url for e in ctx.history.search("private")]))
    s.check("normal window still records history", s.wait(lambda: ctx.history.count() > history_before, 5))
    private.close()
    s.wait(lambda: False, 0.5)
    s.check("session stays while a private window is open", ctx.windows.private_session_active)
    private2.close()
    s.check("private session ends with the last private window",
            s.wait(lambda: not ctx.windows.private_session_active, 5))
    fresh = ctx.windows.open_window(private=True)
    fview = fresh.current_view()
    s.load(fview, f"http://{base}/page?fresh")
    s.check("a new private session starts empty", "privateonly" not in (s.js(fview, "document.cookie") or ""),
            str(s.js(fview, "document.cookie")))
    fresh.close()
    s.wait(lambda: not ctx.windows.private_session_active, 5)
    normal2.close()
    s.wait(lambda: len(ctx.windows.windows) == before, 5)

    print("site permissions")
    origin = f"http://{base}"
    notify = "Notification.requestPermission()"
    bar = window._permission_bar  # noqa: SLF001

    def ask(win: Any, code: str = notify, path: str = "page") -> Any:
        v = win.current_view()
        s.load(v, f"http://{base}/{path}?{time.monotonic()}")
        s.js(v, "document.getElementById('i').focus()")
        return s.js_async(v, code)

    def ask_async(win: Any, code: str, path: str = "page") -> Any:
        v = win.current_view()
        s.load(v, f"http://{base}/{path}?{time.monotonic()}")
        s.js(v, "window.__perm = undefined; Promise.resolve().then(() => " + code + ")"
                ".then(r => window.__perm = 'ok:' + r, e => window.__perm = 'err:' + e.name)")
        return v

    def perm_result(v: Any) -> Any:
        s.wait(lambda: (s.js(v, "String(window.__perm)") or "undefined") != "undefined", 5)
        return s.js(v, "String(window.__perm)")

    window.activateWindow()
    v = ask_async(window, notify)
    s.check("Ask: prompt shown with the requesting origin", s.wait(lambda: bar.current_request() is not None, 5)
            and origin in bar._text.text(), bar._text.text())  # noqa: SLF001
    bar._allow.click()  # noqa: SLF001
    s.check("Ask: allowed from the prompt", perm_result(v) == "ok:granted", str(perm_result(v)))
    s.check("remembered decision stored for the origin",
            ctx.settings.current.site_permissions.get(origin) == {"notifications": "allow"},
            str(ctx.settings.current.site_permissions))
    v = ask_async(window, notify)
    s.check("per-site Allow: no prompt next time", perm_result(v) == "ok:granted" and bar.current_request() is None)
    ctx.settings.set_permission_default("notifications", "block")
    v = ask_async(window, notify)
    s.check("global Block wins over the site's Allow, without prompt",
            perm_result(v) == "ok:denied" and bar.current_request() is None, str(perm_result(v)))
    ctx.settings.set_permission_default("notifications", "allow")
    ctx.settings.set_site_permission(origin, "notifications", "block")
    v = ask_async(window, notify)
    s.check("per-site Block beats global Allow", perm_result(v) == "ok:denied")
    ctx.settings.remove_site_permission(origin, "notifications")
    v = ask_async(window, notify)
    s.check("global Allow after removing the site rule", perm_result(v) == "ok:granted" and bar.current_request() is None)
    ctx.settings.set_permission_default("notifications", "ask")
    ctx.settings.set_permission_default("camera", "ask")
    v = ask_async(window, "navigator.mediaDevices.getUserMedia({video: true}).then(() => 'stream')")
    s.check("camera prompt", s.wait(lambda: bar.current_request() is not None, 5)
            and "camera" in bar.current_request().kinds, bar._text.text())  # noqa: SLF001
    bar._remember.setChecked(False)  # noqa: SLF001
    bar._block.click()  # noqa: SLF001
    s.check("camera blocked from the prompt", (perm_result(v) or "").startswith("err:"), str(perm_result(v)))
    s.check("unremembered decision is not stored", "camera" not in ctx.settings.current.site_permissions.get(origin, {}))
    bar._remember.setChecked(True)  # noqa: SLF001
    ctx.settings.set_permission_default("geolocation", "block")
    v = ask_async(window, "new Promise((ok, no) => navigator.geolocation.getCurrentPosition(() => ok('pos'), e => no({name: 'geo' + e.code})))")
    s.check("geolocation globally blocked: denied without prompt",
            perm_result(v) == "err:geo1" and bar.current_request() is None, str(perm_result(v)))

    # settings page: remove site / reset
    ctx.settings.set_site_permission(origin, "camera", "allow")
    ctx.settings.set_site_permission("https://meet.example.org", "microphone", "allow")
    s.load(view, "lilx://settings/")
    s.wait(lambda: (s.js(view, "document.querySelectorAll('.perm-site').length") or 0) == 2, 5)
    s.check("settings list sites with rules", s.js(view, "document.querySelectorAll('.perm-site').length") == 2)
    s.js(view, "document.querySelector('.perm-site .perm-site-head button').click()")
    s.check("remove all rules of one site from settings", s.wait(lambda: len(ctx.settings.current.site_permissions) == 1, 5),
            str(ctx.settings.current.site_permissions))
    s.js(view, "{ const b = document.getElementById('reset-site-permissions'); b.click(); b.click(); }")
    s.check("reset all site permissions", s.wait(lambda: ctx.settings.current.site_permissions == {}, 5))
    s.js(view, "document.querySelector('#permission-defaults input[name=perm_geolocation][value=ask]').click()")
    s.check("global default changed from settings", s.wait(
        lambda: ctx.settings.current.permission_defaults["geolocation"] == "ask", 5))

    # private window: decisions stay in memory
    pwin = ctx.windows.open_window(private=True)
    pbar = pwin._permission_bar  # noqa: SLF001
    pwin.activateWindow()
    v = ask_async(pwin, notify)
    s.check("private window: prompt says it is remembered only for the session",
            s.wait(lambda: pbar.current_request() is not None, 5) and pbar.current_request().private)
    pbar._allow.click()  # noqa: SLF001
    s.check("private window: allowed", perm_result(v) == "ok:granted")
    s.check("private decision not written to settings", origin not in ctx.settings.current.site_permissions
            and origin.encode() not in (paths.data_dir / "settings.json").read_bytes())
    v = ask_async(pwin, notify)
    s.check("private decision reused within the session", perm_result(v) == "ok:granted" and pbar.current_request() is None)
    ctx.settings.set_permission_default("notifications", "block")
    v = ask_async(pwin, notify)
    s.check("private windows respect a global Block", perm_result(v) == "ok:denied")
    ctx.settings.set_permission_default("notifications", "ask")
    pwin.close()
    s.wait(lambda: not ctx.windows.private_session_active, 5)
    pwin = ctx.windows.open_window(private=True)
    pbar = pwin._permission_bar  # noqa: SLF001
    pwin.activateWindow()
    v = ask_async(pwin, notify)
    s.check("private decisions are gone after the session", s.wait(lambda: pbar.current_request() is not None, 5))
    pbar._block.click()  # noqa: SLF001
    perm_result(v)
    pwin.close()
    s.wait(lambda: not ctx.windows.private_session_active, 5)
    window.activateWindow()

    print("download controls")
    ctx.settings.set("download_dir", str(downloads_dir))

    def slow_download() -> Any:
        count = len(ctx.downloads.records())
        view.load(QUrl(f"http://{base}/slow.bin?{time.monotonic()}"))
        s.wait(lambda: len(ctx.downloads.records()) > count and ctx.downloads.records()[0].received > 0, 10)
        return ctx.downloads.records()[0]

    s.load(view, "lilx://downloads/")  # the download is started from another tab
    work = window.new_tab(QUrl("about:blank"), background=True)

    def slow_download() -> Any:
        count = len(ctx.downloads.records())
        work.load(QUrl(f"http://{base}/slow.bin?{time.monotonic()}"))
        s.wait(lambda: len(ctx.downloads.records()) > count and ctx.downloads.records()[0].received > 0, 10)
        return ctx.downloads.records()[0]

    record = slow_download()
    row = "document.querySelector('#items .list-item')"
    s.wait(lambda: (s.js(view, f"{row} ? {row}.textContent : ''") or "").count(".bin") > 0, 5)
    s.js(view, f"{row}.querySelector('[data-action=\"downloads.pause\"]').click()")
    s.check("Pause", s.wait(lambda: record.paused, 5), record.state)
    held = record.received
    s.wait(lambda: False, 1.0)
    s.check("paused download does not grow", record.received == held and record.state == "in_progress",
            f"{held} -> {record.received}")
    def row_says(key: str) -> bool:  # the page's own translation, whatever the UI language
        return s.js(view, f"{row}.textContent.includes(lilx.t('{key}'))") is True

    s.check("UI shows Paused", s.wait(lambda: row_says("downloads.paused"), 5), str(s.js(view, f"{row}.textContent")))
    s.check("partial data kept while paused", (downloads_dir / f"{record.file_name}.download").exists()
            or (downloads_dir / record.file_name).exists())
    s.js(view, f"{row}.querySelector('[data-action=\"downloads.resume\"]').click()")
    s.check("Resume continues the same download", s.wait(lambda: not record.paused, 5)
            and len([r for r in ctx.downloads.records() if r.file_name.startswith("slow")]) == 1)
    s.check("resumed download completes", s.wait(lambda: record.state == "completed", 30), record.state)
    s.check("completed file is intact", (downloads_dir / record.file_name).read_bytes() == SLOW_PAYLOAD)
    s.check("UI shows the completed download", s.wait(lambda: s.js(view, f"!!{row}.querySelector('[data-action=\"downloads.open\"]')") is True, 5))
    record2 = slow_download()
    ctx.downloads.pause(record2.id)
    s.wait(lambda: record2.paused, 5)
    ctx.downloads.cancel(record2.id)
    s.check("Cancel", s.wait(lambda: record2.state == "cancelled", 5), record2.state)
    s.check("no partial file left after cancel", s.wait(lambda: not (downloads_dir / f"{record2.file_name}.download").exists()
            and not (downloads_dir / record2.file_name).exists(), 5), str(sorted(p.name for p in downloads_dir.iterdir())))
    s.check("UI shows Cancelled", s.wait(lambda: row_says("downloads.cancelled"), 5), str(s.js(view, f"{row}.textContent")))
    pwin = ctx.windows.open_window(private=True)
    count = len(ctx.downloads.records())
    pwin.current_view().load(QUrl(f"http://{base}/file.bin?private"))
    s.wait(lambda: len(ctx.downloads.records()) > count and ctx.downloads.records()[0].state == "completed", 10)
    private_record = ctx.downloads.records()[0]
    s.check("private download marked private", private_record.private)
    ctx.downloads.save()
    s.check("private download not written to the download list",
            private_record.id not in (paths.data_dir / "downloads.json").read_text())
    pwin.close()
    s.wait(lambda: not ctx.windows.private_session_active, 5)
    s.check("private download entry forgotten, file kept", s.wait(
        lambda: all(r.id != private_record.id for r in ctx.downloads.records()), 5) and private_record.path.exists())
    window._tabs.setCurrentIndex(0)  # noqa: SLF001

    print("tabs")
    before = window._tabs.count()  # noqa: SLF001
    extra = window.new_tab(QUrl(f"http://{base}/second"))
    s.check("new tab", window._tabs.count() == before + 1)  # noqa: SLF001
    s.wait(lambda: not extra.is_loading)
    window.close_tab(window._tabs.currentIndex())  # noqa: SLF001
    s.check("close tab", window._tabs.count() == before)  # noqa: SLF001
    window.reopen_closed_tab()
    s.check("reopen closed tab", window._tabs.count() == before + 1)  # noqa: SLF001
    window.close_tab(window._tabs.currentIndex())  # noqa: SLF001
    window._tabs.setCurrentIndex(0)  # noqa: SLF001

    print("downloads")
    view.load(QUrl(f"http://{base}/file.bin"))
    finished = s.wait(lambda: any(r.state == "completed" for r in ctx.downloads.records()), 20)
    target = downloads_dir / "file.bin"
    s.check("download completes", finished, str([r.state for r in ctx.downloads.records()]))
    s.check("file saved intact", target.exists() and target.read_bytes() == PAYLOAD)
    s.load(view, "lilx://downloads/")
    s.wait(lambda: (s.js(view, "document.querySelectorAll('#items .list-item').length") or 0) >= 1, 5)
    s.js(view, "{ const q = document.getElementById('query'); q.value = 'file.bin'; q.dispatchEvent(new Event('input')); }")
    s.check("downloads search finds the file", s.js(view, "document.querySelectorAll('#items .list-item').length") == 1)
    s.js(view, "{ const q = document.getElementById('query'); q.value = 'nothing-like-this'; q.dispatchEvent(new Event('input')); }")
    s.check("downloads search filters out", s.js(view, "document.querySelectorAll('#items .list-item').length") == 0)

    print("language")
    s.load(view, "lilx://settings/")
    s.wait(lambda: s.js(view, "document.querySelectorAll('#languages input').length === 2") is True)
    # Start from English explicitly: with an empty setting the page follows the system locale.
    ctx.settings.set("language", "en")
    s.wait(lambda: s.js(view, "document.documentElement && document.documentElement.lang") == "en", 5)
    s.wait(lambda: s.js(view, "document.querySelectorAll('#languages input').length === 2") is True)
    s.check("settings page in English", s.js(view, "document.querySelector('h1').textContent") == "Settings")
    s.js(view, "document.querySelector('#languages input[value=ru]').click()")
    s.check("language saved", s.wait(lambda: ctx.settings.current.language == "ru", 5))
    s.wait(lambda: s.js(view, "document.documentElement && document.documentElement.lang") == "ru", 5)
    s.wait(lambda: s.js(view, "document.querySelector('h1').textContent") == "Настройки", 5)
    s.check("settings page in Russian", s.js(view, "document.querySelector('h1').textContent") == "Настройки",
            str(s.js(view, "document.querySelector('h1').textContent")))
    s.check("browser chrome in Russian", window._actions["settings"].text() == "Настройки",  # noqa: SLF001
            window._actions["settings"].text())  # noqa: SLF001
    if args.screenshots:
        s.wait(lambda: False, 0.8)
        window.grab().save(str(args.screenshots / "settings-ru.png"))
        s.js(view, "document.getElementById('lilblock').scrollIntoView()")
        s.wait(lambda: False, 0.5)
        window.grab().save(str(args.screenshots / "settings-lilblock.png"))

    print("reset")
    ctx.settings.set_site_rule(SiteRule(host="127.0.0.1"))
    s.wait(lambda: s.js(view, "!!document.getElementById('reset-button')") is True, 5)
    s.js(view, "document.getElementById('reset-button').click()")
    first = s.js(view, "document.getElementById('reset-button').textContent")
    s.js(view, "document.getElementById('reset-button').click()")
    second = s.js(view, "document.getElementById('reset-button').textContent")
    s.check("reset shows 1/3 and 2/3", str(first).startswith("1/3") and str(second).startswith("2/3"), f"{first} | {second}")
    s.check("no reset before the third press", not ctx.reset_requested)
    s.js(view, "document.getElementById('reset-button').click()")
    s.check("third press requests reset", s.wait(lambda: ctx.reset_requested, 5))
    # Windows are deleted when closed (WA_DeleteOnClose).
    s.check("window closes for reset", s.wait(lambda: not shiboken6.isValid(window) or not window.isVisible(), 5))
    shutdown(window, ctx)

    print("forget on close")
    cookie_db = next((p for p in (paths.webengine_dir / "Network" / "Cookies", paths.webengine_dir / "Cookies")
                      if p.exists()), None)
    remaining = -1
    if cookie_db is not None:
        remaining = sqlite3.connect(cookie_db).execute(
            "SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%127.0.0.1%'").fetchone()[0]
    s.check("cookies of forgotten site removed", remaining == 0, f"cookie db={cookie_db}, remaining={remaining}")
    from lilx.core.history import HistoryStore

    history = HistoryStore(paths.history_db)
    s.check("history of forgotten site removed", history.count() == 0, str(history.count()))
    history.close()

    print("factory reset")
    (paths.data_dir / "not-lilx.txt").write_text("keep")
    _, errors = factory_reset(paths)
    left = sorted(p.name for p in paths.data_dir.iterdir())
    s.check("all browser data removed", left == ["not-lilx.txt"] and not errors, f"{left} {errors}")
    s.check("downloaded files kept", target.exists())

    server.shutdown()
    # nothing of the private sessions reached the normal profile on disk
    cookie_files = [p for p in paths.webengine_dir.rglob("Cookies") if p.is_file()]
    leaked = [p for p in cookie_files if b"privateonly" in p.read_bytes()]
    s.check("private cookies never written to the normal profile", not leaked, str(leaked))
    print(f"\n{'FAILED: ' + ', '.join(s.failures) if s.failures else 'all smoke checks passed'}")
    tmp.cleanup()  # temporary profile, downloads and HOME-less data of this run
    return 1 if s.failures else 0


if __name__ == "__main__":
    sys.exit(main())
