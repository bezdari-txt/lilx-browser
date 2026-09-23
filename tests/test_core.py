"""Unit tests for widget-free logic. Run with: python -m unittest discover -s tests"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from lilx.core.bookmarks import BookmarkError, BookmarkStore
from lilx.core.adblock import FilterEngine, Request, base_domain, parse_rule
from lilx.core.history import HistoryStore
from lilx.core.hosts import host_matches, normalize_host
from lilx.core.privacy import run_offline_cleanup
from lilx.core.reset import factory_reset
from lilx.core.search import looks_like_url, resolve_input
from lilx.core.settings import SettingsError, SettingsManager, SiteRule
from lilx.core.storage import PlainFileStorage, StorageError
from lilx.paths import AppPaths


class SearchTests(unittest.TestCase):
    def test_urls(self) -> None:
        cases = {
            "example.com": "https://example.com",
            "example.com/path?q=1": "https://example.com/path?q=1",
            "https://example.com": "https://example.com",
            "http://example.com": "http://example.com",
            "localhost:8000": "http://localhost:8000",
            "192.168.1.1": "http://192.168.1.1",
            "8.8.8.8": "https://8.8.8.8",
            "lilx://settings": "lilx://settings",
            "about:blank": "about:blank",
            "пример.рф": "https://пример.рф",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(resolve_input(text, "duckduckgo"), expected)

    def test_searches(self) -> None:
        self.assertEqual(resolve_input("hello world", "duckduckgo"), "https://duckduckgo.com/?q=hello+world")
        self.assertEqual(resolve_input("python", "google"), "https://www.google.com/search?q=python")
        self.assertEqual(resolve_input("a&b", "bing"), "https://www.bing.com/search?q=a%26b")
        self.assertEqual(resolve_input("version 1.2", "unknown"), "https://duckduckgo.com/?q=version+1.2")
        for text in ("hello", "what is example.com", "user@example.com", "1.2", "example.com:abc"):
            with self.subTest(text=text):
                self.assertFalse(looks_like_url(text))
                self.assertTrue(resolve_input(text, "duckduckgo").startswith("https://duckduckgo.com/?q="))

    def test_empty(self) -> None:
        self.assertEqual(resolve_input("   ", "google"), "")


class HostTests(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(normalize_host("https://www.Example.com/path"), "example.com")
        self.assertEqual(normalize_host("sub.example.com"), "sub.example.com")
        self.assertEqual(normalize_host("localhost"), "localhost")
        self.assertEqual(normalize_host("not a host"), "")
        self.assertEqual(normalize_host(""), "")

    def test_matches(self) -> None:
        self.assertTrue(host_matches("example.com", "example.com"))
        self.assertTrue(host_matches(".example.com", "example.com"))
        self.assertTrue(host_matches("a.b.example.com", "example.com"))
        self.assertFalse(host_matches("badexample.com", "example.com"))
        self.assertFalse(host_matches("example.com", "sub.example.com"))


class StorageAndSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = PlainFileStorage(self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_roundtrip_and_permissions(self) -> None:
        self.storage.write_json("doc.json", {"a": 1})
        self.assertEqual(self.storage.read_json("doc.json"), {"a": 1})
        self.assertEqual((self.root / "doc.json").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(StorageError):
            self.storage.read("../escape")

    def test_settings_validation_and_persistence(self) -> None:
        manager = SettingsManager(self.storage)
        manager.set("search_engine", "google")
        manager.set_site_rule(SiteRule(host="example.com"))
        with self.assertRaises(SettingsError):
            manager.set("search_engine", "altavista")
        with self.assertRaises(SettingsError):
            manager.set("history_enabled", "yes")
        with self.assertRaises(SettingsError):
            manager.set("homepage", "javascript:alert(1)")

        reloaded = SettingsManager(self.storage)
        self.assertEqual(reloaded.current.search_engine, "google")
        self.assertEqual([r.host for r in reloaded.current.forget_on_close], ["example.com"])

    def test_corrupted_settings_fall_back_to_defaults(self) -> None:
        (self.root / "settings.json").write_text("{not json")
        self.assertEqual(SettingsManager(self.storage).current.search_engine, "duckduckgo")
        self.assertEqual((self.root / "settings.json.corrupted").read_text(), "{not json")


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = HistoryStore(Path(self.tmp.name) / "history.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_add_search_delete(self) -> None:
        self.store.add_visit("https://example.com/", "Example")
        self.store.add_visit("https://example.com/", "Example again")  # deduplicated
        self.store.add_visit("https://news.example.com/a", "News")
        self.store.add_visit("https://other.org/", "Other")
        self.store.add_visit("lilx://settings", "ignored")
        self.assertEqual(self.store.count(), 3)
        self.assertEqual(self.store.search("example")[0].title, "News")
        self.assertEqual(self.store.search("100%"), [])
        self.assertEqual(self.store.delete_host("example.com"), 2)
        self.assertEqual([e.host for e in self.store.search()], ["other.org"])
        self.store.clear()
        self.assertEqual(self.store.count(), 0)

    def test_top_sites_exclude(self) -> None:
        for url in ("https://a.com/", "https://a.com/x", "https://b.com/", "https://c.com/"):
            self.store.add_visit(url, "t")
        self.assertEqual(self.store.top_sites(8)[0]["host"], "a.com")
        self.assertEqual({s["host"] for s in self.store.top_sites(8, ["a.com", "c.com"])}, {"b.com"})

    def test_hidden_top_sites_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = PlainFileStorage(Path(tmp))
            manager = SettingsManager(storage)
            manager.hide_top_site("News.Example")
            self.assertEqual(SettingsManager(storage).current.top_sites_hidden, ["news.example"])
            manager.restore_top_sites()
            self.assertEqual(SettingsManager(storage).current.top_sites_hidden, [])


class OfflineCleanupTests(unittest.TestCase):
    def test_cookies_and_indexeddb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            db = sqlite3.connect(profile / "Cookies")
            db.execute("CREATE TABLE cookies (host_key TEXT, name TEXT)")
            db.executemany("INSERT INTO cookies VALUES (?, ?)",
                           [(".example.com", "a"), ("sub.example.com", "b"), ("keep.org", "c")])
            db.commit()
            db.close()
            idb = profile / "IndexedDB"
            (idb / "https_www.example.com_0.indexeddb.leveldb").mkdir(parents=True)
            (idb / "https_keep.org_0.indexeddb.leveldb").mkdir()

            report = run_offline_cleanup(profile, [SiteRule(host="example.com")])

            self.assertEqual(report.cookies_removed, 2)
            rows = sqlite3.connect(profile / "Cookies").execute("SELECT host_key FROM cookies").fetchall()
            self.assertEqual(rows, [("keep.org",)])
            self.assertEqual(sorted(p.name for p in idb.iterdir()), ["https_keep.org_0.indexeddb.leveldb"])


def _req(url: str, page: str = "news.example", kind: str = "script") -> Request:
    from urllib.parse import urlsplit

    return Request.create(url, urlsplit(url).hostname or "", page, kind)


class FilterEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FilterEngine()
        self.engine.load_text(
            "\n".join([
                "! comment",
                "example.com##.banner",  # cosmetic: ignored
                "||ads.example^",
                "||tracker.net^$third-party",
                "||cdn.example/banners/*",
                "/adsbygoogle.js",
                "&ad_slot=$xhr",
                "||media.example^$image,domain=news.example|~sport.news.example",
                "||evil.example^$redirect=noop.js",  # unsupported option: skipped
                "@@||ads.example/allowed/",
            ])
        )

    def test_rule_count_skips_unsupported(self) -> None:
        self.assertEqual(self.engine.rule_count, 7)
        self.assertIsNone(parse_rule("||x.com^$csp=script-src 'none'"))

    def test_domain_anchor_and_subdomains(self) -> None:
        self.assertIsNotNone(self.engine.match(_req("https://ads.example/a.js")))
        self.assertIsNotNone(self.engine.match(_req("https://img.ads.example/a.png", kind="image")))
        self.assertIsNone(self.engine.match(_req("https://notads.example/a.js")))

    def test_exception_rule(self) -> None:
        self.assertIsNone(self.engine.match(_req("https://ads.example/allowed/x.js")))

    def test_third_party_option(self) -> None:
        self.assertIsNotNone(self.engine.match(_req("https://tracker.net/p.gif", page="news.example", kind="image")))
        self.assertIsNone(self.engine.match(_req("https://tracker.net/p.gif", page="www.tracker.net", kind="image")))

    def test_paths_wildcards_and_types(self) -> None:
        self.assertIsNotNone(self.engine.match(_req("https://cdn.example/banners/1/big.png", kind="image")))
        self.assertIsNone(self.engine.match(_req("https://cdn.example/app.js")))
        self.assertIsNotNone(self.engine.match(_req("https://site.example/js/adsbygoogle.js")))
        self.assertIsNotNone(self.engine.match(_req("https://site.example/x?a=1&ad_slot=2", kind="xmlhttprequest")))
        self.assertIsNone(self.engine.match(_req("https://site.example/x?a=1&ad_slot=2", kind="script")))

    def test_domain_option(self) -> None:
        self.assertIsNotNone(self.engine.match(_req("https://media.example/a.png", "news.example", "image")))
        self.assertIsNone(self.engine.match(_req("https://media.example/a.png", "sport.news.example", "image")))
        self.assertIsNone(self.engine.match(_req("https://media.example/a.png", "other.example", "image")))

    def test_documents_are_never_blocked_by_generic_rules(self) -> None:
        self.assertIsNone(self.engine.match(_req("https://ads.example/", kind="document")))

    def test_base_domain(self) -> None:
        self.assertEqual(base_domain("a.b.example.co.uk"), "example.co.uk")
        self.assertEqual(base_domain("www.example.com"), "example.com")

    def test_builtin_list_loads(self) -> None:
        engine = FilterEngine()
        builtin = Path(__file__).resolve().parent.parent / "lilx" / "resources" / "filters" / "lilblock-base.txt"
        self.assertGreater(engine.load_file(builtin), 150)
        self.assertIsNotNone(engine.match(_req("https://securepubads.g.doubleclick.net/tag/js/gpt.js")))
        self.assertIsNone(engine.match(_req("https://www.google.com/recaptcha/api.js")))


class AllowlistAndResetTests(unittest.TestCase):
    def test_allowlist_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = PlainFileStorage(Path(tmp))
            manager = SettingsManager(storage)
            self.assertEqual(manager.allow_ads("https://www.news.example/page"), "news.example")
            manager.set("language", "ru")
            reloaded = SettingsManager(storage)
            self.assertEqual(reloaded.current.adblock_allowlist, ["news.example"])
            self.assertEqual(reloaded.current.language, "ru")
            reloaded.block_ads("news.example")
            self.assertEqual(SettingsManager(storage).current.adblock_allowlist, [])
            with self.assertRaises(SettingsError):
                manager.set("language", "de")

    def test_factory_reset_removes_only_lilx_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AppPaths(data_dir=root / "data", cache_dir=root / "cache")
            paths.ensure()
            for name in ("settings.json", "settings.json.corrupted", "downloads.json", "lilblock-log.json",
                         "history.sqlite3", "history.sqlite3-journal"):
                (paths.data_dir / name).write_text("x")
            (paths.webengine_dir / "Cookies").write_text("x")
            (paths.data_dir / "user-notes.txt").write_text("keep me")
            factory_reset(paths)
            self.assertEqual([p.name for p in paths.data_dir.iterdir()], ["user-notes.txt"])
            self.assertFalse(paths.webengine_cache_dir.exists())


class BookmarkTests(unittest.TestCase):
    def test_add_remove_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = PlainFileStorage(Path(tmp))
            store = BookmarkStore(storage)
            first = store.add("https://github.com/", "GitHub")
            self.assertIs(store.add("https://github.com/", "dup"), first)  # no duplicates
            store.add("https://example.com/page")
            reloaded = BookmarkStore(storage)
            self.assertEqual([b.title for b in reloaded.all()], ["GitHub", "example.com"])
            self.assertTrue(reloaded.remove_url("https://github.com/"))
            self.assertEqual(len(BookmarkStore(storage).all()), 1)
            with self.assertRaises(BookmarkError):
                store.add("javascript:alert(1)")

    def test_numeric_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SettingsManager(PlainFileStorage(Path(tmp)))
            manager.set("font_size", 18)
            manager.set("default_zoom", "125")
            self.assertEqual((manager.current.font_size, manager.current.default_zoom), (18, 125))
            for key, value in (("font_size", 100), ("default_zoom", True), ("font_size", "big")):
                with self.subTest(key=key, value=value), self.assertRaises(SettingsError):
                    manager.set(key, value)


class AccentTests(unittest.TestCase):
    def test_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = SettingsManager(PlainFileStorage(Path(tmp)))
            manager.set("accent_mode", "custom")
            manager.set("accent_color", "#3B82F6")
            self.assertEqual(manager.current.accent_color, "#3b82f6")
            for key, value in (("accent_color", "blue"), ("accent_color", "#12345"), ("accent_mode", "rainbow")):
                with self.subTest(value=value), self.assertRaises(SettingsError):
                    manager.set(key, value)

    def test_palette(self) -> None:
        from lilx.ui.theme import accent_for, page_css

        self.assertIsNone(accent_for("default", "#3b82f6", False))
        light = accent_for("custom", "#3b82f6", False)
        self.assertEqual((light.color, light.text), ("#3b82f6", "#ffffff"))
        dark = accent_for("custom", "#3b82f6", True)
        self.assertNotEqual(dark.color, "#3b82f6")  # lightened for dark backgrounds
        self.assertEqual(accent_for("custom", "#facc15", False).text, "#131217")  # dark text on yellow
        self.assertIn("--accent: #22c55e", page_css("light", "custom", "#22c55e"))
        self.assertNotIn("--accent", page_css("light", "default", "#22c55e"))


class ExtensionManifestTests(unittest.TestCase):
    def test_manifest_and_icon(self) -> None:
        from lilx.engine.extensions import _icon_file, read_manifest

        fixture = Path(__file__).resolve().parent / "fixtures" / "extensions" / "hello-mv3"
        manifest = read_manifest(fixture)
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(_icon_file(fixture, manifest).name, "icon48.png")
        # Icons pointing outside the extension folder are ignored.
        self.assertIsNone(_icon_file(fixture, {"icons": {"48": "../../../../pyproject.toml"}}))
        self.assertEqual(read_manifest(fixture / "missing"), {})


class BrandingTests(unittest.TestCase):
    def test_logo_is_the_project_logo(self) -> None:
        from lilx.branding import logo_path

        root = Path(__file__).resolve().parent.parent
        self.assertEqual(logo_path(), root / "logo.png")

    def test_derived_icon_files(self) -> None:
        import struct

        icons = Path(__file__).resolve().parent.parent / "packaging" / "icons"
        ico = (icons / "lilx.ico").read_bytes()
        count = struct.unpack("<HHH", ico[:6])[2]
        self.assertEqual(sorted(ico[6 + 16 * i] or 256 for i in range(count)), [16, 24, 32, 48, 64, 128, 256])
        icns = (icons / "lilx.icns").read_bytes()
        self.assertEqual(icns[:4], b"icns")
        self.assertIn(b"ic10", icns)  # 1024 px (512@2x)


if __name__ == "__main__":
    unittest.main()
