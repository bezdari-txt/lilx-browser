"use strict";

(() => {
  const { h, t } = lilx;
  const form = document.getElementById("search-form");
  const input = document.getElementById("search-input");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    try {
      const { url } = await lilx.get("home.resolve", { q });
      window.location.assign(url);
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  });

  const isPrivate = document.documentElement.dataset.private === "1";
  document.getElementById("private-note").hidden = !isPrivate;

  function renderTiles(sites) {
    const tiles = document.getElementById("tiles");
    // A private window does not show what was browsed normally.
    document.getElementById("top-section").hidden = isPrivate || !sites.length;
    tiles.replaceChildren(...sites.map((site) =>
      h("div", { class: "tile-wrap" },
        h("a", { class: "tile", href: site.url, title: site.title || site.host },
          lilx.avatar(site.url),
          h("span", { class: "name" }, site.host.replace(/^www\./, ""))),
        h("button", {
          class: "tile-remove", type: "button", title: t("home.top_remove"), "aria-label": t("home.top_remove"),
          onclick: async (event) => {
            event.preventDefault();
            try {
              await lilx.post("home.top_site_hide", { host: site.host });
              lilx.toast(t("home.top_removed", { host: site.host.replace(/^www\./, "") }));
              load();
            } catch (error) {
              lilx.toast(error.message, "error");
            }
          },
        }, "✕"))));
  }

  document.getElementById("hide-top-sites").addEventListener("click", async () => {
    try {
      await lilx.post("home.top_sites_off");
      lilx.toast(t("home.top_hidden"));
      load();
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  });

  // ---------- bookmarks ----------
  const bookmarkForm = document.getElementById("bookmark-form");
  const urlInput = document.getElementById("bm-url");
  const titleInput = document.getElementById("bm-title");

  function showForm(visible) {
    bookmarkForm.hidden = !visible;
    if (visible) urlInput.focus();
    else { urlInput.value = ""; titleInput.value = ""; }
  }

  function bookmarkTile(bookmark) {
    return h("div", { class: "tile-wrap" },
      h("a", { class: "tile", href: bookmark.url, title: bookmark.url },
        lilx.avatar(bookmark.url),
        h("span", { class: "name" }, bookmark.title || bookmark.host)),
      h("button", {
        class: "tile-remove", type: "button", title: t("home.bm_remove"), "aria-label": t("home.bm_remove"),
        onclick: async (event) => {
          event.preventDefault();
          try {
            await lilx.post("home.bookmark_remove", { id: bookmark.id });
            lilx.toast(t("home.bm_removed"));
            load();
          } catch (error) {
            lilx.toast(error.message, "error");
          }
        },
      }, "✕"));
  }

  function renderBookmarks(data) {
    document.getElementById("bookmarks-section").hidden = !data.show_bookmarks;
    const addTile = h("button", { class: "tile add-tile", type: "button", onclick: () => showForm(true) },
      h("div", { class: "avatar" }, "+"),
      h("span", { class: "name" }, t("home.add_bookmark")));
    document.getElementById("bookmarks").replaceChildren(...data.bookmarks.map(bookmarkTile), addTile);
  }

  document.getElementById("bm-cancel").addEventListener("click", () => showForm(false));
  bookmarkForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await lilx.post("home.bookmark_add", { url: urlInput.value, title: titleInput.value });
      showForm(false);
      lilx.toast(t("home.bm_added"));
      load();
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  });

  function renderPrivacy(p) {
    const badge = (on, text) => h("span", { class: `badge ${on ? "good" : ""}` }, text);
    const badges = [
      badge(p.lilblock_enabled,
        p.lilblock_enabled ? t("home.lilblock_on", { n: p.lilblock_total }) : t("home.lilblock_off")),
      badge(p.third_party_cookies_blocked, t(p.third_party_cookies_blocked ? "home.tp_blocked" : "home.tp_allowed")),
      badge(p.privacy_signals, t(p.privacy_signals ? "home.gpc_on" : "home.gpc_off")),
      isPrivate
        ? badge(true, t("home.history_private"))
        : badge(!p.history_enabled, t(p.history_enabled ? "home.history_on" : "home.history_off")),
    ];
    if (p.forget_on_close) badges.push(badge(true, t("home.forget", { n: p.forget_on_close })));
    document.getElementById("privacy").replaceChildren(...badges);
  }

  async function load() {
    try {
      const data = await lilx.get("home.data");
      input.placeholder = t("home.placeholder_engine", { engine: data.engine.name });
      renderBookmarks(data);
      renderTiles(data.top_sites);
      renderPrivacy(data.privacy);
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  }

  // Bookmarks added from the star button or settings changed elsewhere.
  window.addEventListener("lilx:settings-changed", load);
  load();
})();
