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

from PySide6.QtCore import QEventLoop, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lilx.app import build, shutdown  # noqa: E402
from lilx.core.reset import factory_reset  # noqa: E402
from lilx.core.settings import SiteRule  # noqa: E402
from lilx.paths import AppPaths  # noqa: E402

PAYLOAD = os.urandom(256 * 1024)
REQUESTS: list[tuple[str, dict[str, str]]] = []  # (path, headers) seen by the test server


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        REQUESTS.append((self.path, dict(self.headers)))
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
    s.check("window closes for reset", s.wait(lambda: not window.isVisible(), 5))
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
    print(f"\n{'FAILED: ' + ', '.join(s.failures) if s.failures else 'all smoke checks passed'}")
    return 1 if s.failures else 0


if __name__ == "__main__":
    sys.exit(main())
