# lilx architecture

## Layout

```
lilx/
├── __main__.py          python -m lilx
├── app.py               bootstrap: startup order, service wiring, shutdown
├── context.py           BrowserContext: the shared services passed to the UI
├── paths.py             data/cache locations (pathlib), resources
├── i18n.py              chrome translations (en/ru); pages use resources/pages/assets/i18n.js
├── core/                logic without widgets (unit-testable)
│   ├── storage.py       StorageBackend interface + PlainFileStorage  ← vault seam
│   ├── settings.py      Settings model, validation, SettingsManager
│   ├── search.py        search engines, address-bar input resolution
│   ├── history.py       HistoryStore (SQLite)
│   ├── downloads.py     DownloadManager (QWebEngineDownloadRequest; pause/resume/cancel)
│   ├── permissions.py   site permission rules: origins, Ask/Allow/Block resolution
│   ├── hosts.py         host normalization / matching for site rules
│   ├── privacy.py       offline per-site cleanup (engine not running)
│   ├── adblock.py       lilBlock filter engine (Adblock Plus network rules)
│   ├── reset.py         factory reset (deletes only lilx's own files)
│   ├── secrets.py       SecretStore interface (OS keychain) — not implemented
│   └── network.py       NetworkMode (direct; Tor planned) — the Tor seam
├── engine/              Qt WebEngine integration
│   ├── profile.py       persistent + off-the-record profiles, privacy attributes
│   ├── page.py          BrowserPage: popups → tabs, permission requests, lilx:// guard
│   ├── permissions.py   PermissionService: answers QWebEnginePermission from the rules
│   ├── scheme.py        lilx:// scheme handler (pages, assets, API)
│   ├── api.py           JSON API for internal pages
│   ├── lilblock.py      lilBlock service, per-page interceptor, block history
│   ├── extensions.py    ExtensionService over QWebEngineExtensionManager (MV3)
│   └── browser_data.py  runtime data management (clear data, forget sites)
├── ui/                  browser chrome (widgets)
│   ├── main_window.py   tabs + navigation + web view stack (normal or private window)
│   ├── windows.py       WindowManager, PrivateSession (shared off-the-record profile)
│   ├── permission_bar.py  in-window permission prompt
│   ├── tab_bar.py, navigation_bar.py, browser_view.py, overlays.py
│   ├── animations.py    fading buttons, animated menus, fades (short, optional)
│   ├── theme.py         palettes + Qt stylesheet
│   ├── icons.py         inline SVG icons
│   └── shortcuts.py     keyboard shortcut table
└── resources/pages/     internal pages (HTML/CSS/JS, no build step)
```

Dependencies point one way: `ui → engine → core`. `core` never imports `engine`
or `ui` (it uses QtCore signals and the download request type only).

## Internal pages (`lilx://`)

* Served by `LilxSchemeHandler`; each page is its own origin (`lilx://settings`).
* Pages talk to Python through `fetch("/api/<namespace>.<method>?p=<json>")`.
  A page may only call its own namespace; mutations require POST.
  (Arguments travel in the query string because `requestBody()` crashes in
  PySide6 6.11.)
* Protection from websites: `BrowserPage.acceptNavigationRequest` refuses any
  navigation or frame load of `lilx://` that was not typed by the user or started
  from another `lilx://` page; the API checks the request initiator;
  `frame-ancestors 'none'` and `X-Frame-Options: DENY` are sent.
* Pages have a strict CSP (no inline scripts/styles, no remote resources) and never
  use `innerHTML` with data (history titles are attacker-controlled).
* The theme is injected server-side (`data-theme` on `<html>`).

## lilBlock

* `core/adblock.py` parses network rules (`||domain^`, anchors, wildcards, `@@`
  exceptions, type / third-party / `domain=` options). Rules whose options cannot be
  honoured (`redirect`, `csp`, `$document`, …) are skipped, never half-applied.
  Lookups are indexed by host suffix and by a literal URL token.
* Every `BrowserPage` installs its own `PageBlocker` interceptor, so blocked requests
  are counted per tab (shown on the shield next to the address bar). A page with its
  own interceptor bypasses the profile interceptor, so `PageBlocker` also adds the
  GPC/DNT headers.
* Main-frame navigations are never blocked. Exceptions (`Settings.adblock_allowlist`)
  match the first-party site and its subdomains.
* `BlockLog` keeps per-host totals and the last 300 blocked requests (query strings
  stripped; the page host is omitted when history is off), saved in `lilblock-log.json`.

## Extensions

`engine/extensions.py` wraps Qt's `QWebEngineExtensionManager` of the tabs' own
profile; lilx implements no extension API itself. What Qt leaves to the app (verified
with PySide6 6.11): installs (folder or .zip, copied to `<profile>/Extensions`) finish
asynchronously and failures come as an info with an empty id and `error()`; installed
extensions are reloaded on start but always disabled, so the enabled state is kept in
`extensions.json` and re-applied on `loadFinished`; reinstalling creates a new id, so it
is treated as an update of the older copy; `extensions()` also returns Qt's built-in
components, which are hidden. Non-MV3 folders are rejected before install, non-MV3
.zips right after Qt unpacks them. The action popup (`actionPopupUrl()`) is shown by
`ui/extension_popup.py` in a `QWebEngineView` on the same profile.

## Windows and profiles

`ui/windows.WindowManager` owns all windows (Control+N: normal, Control+Shift+N:
private; on macOS the physical Control key, `Meta` in Qt). Normal windows share the
persistent profile and all services. Private windows share one `PrivateSession`: an
off-the-record profile from `QWebEngineProfileBuilder.createOffTheRecordProfile()` with
the same privacy configuration, its own `lilx://` handler (pages get
`data-private="1"`) and a lilBlock interceptor that records nothing. Private windows
never write history; their download entries and permission decisions stay in memory.
When the last private window is destroyed the profile is deleted, so cookies, storage,
cache and session data of the private session are gone; the next private window starts
a fresh session. Extensions run in the normal profile only.

Named profiles: `lilx --profile NAME` uses a complete, separate data directory
(`<data dir>/profiles/NAME`: settings, history, bookmarks, cookies, storage).

## Site permissions

Camera, microphone, location, notifications and clipboard (Qt has one permission for
clipboard read and write). `core/permissions.py` resolves a request for an origin
(`scheme://host[:port]`, exact match: no subdomains, http and https separate):
global Block (hard, no prompt) → site rule (allow/block) → global default (ask/allow).
Rules live in settings (`permission_defaults`, `site_permissions`).

Profiles use `PersistentPermissionsPolicy.AskEveryTime`, so Qt stores nothing.
Chromium still caches an answer within a tab for the same origin until the permission
is `reset()`; `BrowserPage` therefore resets its answers on navigation and when the
rules change (`PermissionService.rules_changed`), which makes a new global Block apply
to open tabs at once. "Ask" requests wait in the page and are shown by
`ui/permission_bar.PermissionBar` with the requesting origin; "remember" stores a site
rule (persistent in normal windows, in memory for the private session).

## Language

`Settings.language` is `""` (follow the system locale) until the user picks
English or Русский. The chrome uses `i18n.tr()`; internal pages get `<html lang>`
from the scheme handler and translate `data-i18n` markup with `i18n.js`.

## Factory reset

`Reset lilx` requires three consecutive presses (the counter resets after 4 s).
The window then closes normally, the profile is destroyed, `core/reset.py` deletes
lilx's own entries (settings, history, download list, lilBlock files, the whole
Chromium profile incl. cookies/storage/internal page data, cache) with retries, and a
new process is started with `sys.executable`. A `reset-pending` marker makes the new
process finish the reset before opening the profile if any file was still busy.

## Privacy features

### Forget sites on close (implemented)

Rules live in `Settings.forget_on_close` (`SiteRule`: host + which data kinds).
A rule for `example.com` also covers its subdomains.

| Data | How it is removed |
|---|---|
| History | `HistoryStore.delete_host` when lilx quits |
| Cookies | through the engine (`QWebEngineCookieStore.deleteCookie`) on close, and again in the `Cookies` SQLite file after the profile is destroyed (also at startup, which covers crashes) |
| IndexedDB | per-origin directories removed while the engine is stopped |
| Local Storage, Service Worker / Cache Storage | **not yet**: Chromium keeps them in shared LevelDB databases; needs a LevelDB-aware cleaner |

### Encrypted vault (planned — nothing is encrypted today)

Everything persistent goes through two seams:

1. `StorageBackend` (`core/storage.py`) for documents (settings, download list).
   A future `EncryptedVaultStorage` implements `read/write/delete` with an AEAD
   cipher (e.g. XChaCha20-Poly1305 / AES-GCM) under a random data key. Callers do
   not change; `encrypted = True` is reported to the settings page.
2. `HistoryStore` for history, whose methods are the only access to the database.
   It can move to an encrypted SQLite (SQLCipher) or into the vault.

The Chromium profile (cookies, storage, cache) is the hard part: it lives in
`paths.webengine_dir`. The plan is to keep it on an encrypted volume / in the
vault and unpack it only into a private location for the session, or to run
an off-the-record profile and persist cookies ourselves through the vault.
`AppPaths` keeps all of this under one directory to make that possible.

### Crypto-erasure (planned)

The vault data key will be stored only in the OS keychain (`core/secrets.py`).
"Erase the vault" deletes that key; the ciphertext left on disk is then
unrecoverable, even from backups or SSD remnants. This requires the vault above;
until then, deleting data means deleting files (with SQLite `secure_delete`
enabled for history).

### Passwords (planned)

lilx 0.1 never saves passwords. When a password manager is added, secrets go
only to `SecretStore` implementations (macOS Keychain, Linux Secret Service,
Windows Credential Manager) — never into lilx files in plain text.

### Tor mode (planned)

`core/network.py` is the entry point. Requirements already accounted for:
a separate off-the-record profile (`QWebEngineProfileBuilder.createOffTheRecordProfile`),
application-wide SOCKS proxy set before the first page loads, WebRTC leak
prevention, and visual separation of Tor windows. `BrowserContext` holds the
profile explicitly so a second context with a different profile can be created.

## Startup and shutdown order

1. register `lilx://` (before `QApplication`)
2. offline cleanup of forgotten sites (before the profile opens its files)
3. profile → services → window
4. on quit: runtime forget-on-close cleanup → delete all windows (views/pages) → delete
   the private profile → delete the normal profile (Chromium flushes)
   → offline cleanup again
