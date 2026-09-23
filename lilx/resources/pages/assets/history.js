"use strict";

(() => {
  const { h, t } = lilx;
  const PAGE = 200;
  const container = document.getElementById("entries");
  const query = document.getElementById("query");
  const more = document.getElementById("more");
  let entries = [];

  async function load(append = false) {
    try {
      const data = await lilx.get("history.list", {
        q: query.value.trim(), limit: PAGE, offset: append ? entries.length : 0,
      });
      entries = append ? entries.concat(data.entries) : data.entries;
      more.hidden = data.entries.length < PAGE;
      document.getElementById("status").textContent = t(data.enabled ? "history.local" : "history.disabled");
      render();
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  }

  function render() {
    if (!entries.length) {
      container.replaceChildren(h("div", { class: "empty" },
        t(query.value.trim() ? "history.no_match" : "history.empty")));
      return;
    }
    const nodes = [];
    let lastDay = null;
    for (const entry of entries) {
      const day = lilx.formatDay(entry.visited_at);
      if (day !== lastDay) {
        nodes.push(h("div", { class: "day" }, day));
        lastDay = day;
      }
      nodes.push(row(entry));
    }
    container.replaceChildren(...nodes);
  }

  function row(entry) {
    return h("div", { class: "list-item" },
      h("span", { class: "time" }, lilx.formatTime(entry.visited_at)),
      lilx.avatar(entry.url),
      h("div", { class: "grow" },
        h("a", { class: "title", href: entry.url, title: entry.url }, entry.title || entry.url),
        h("div", { class: "sub" }, entry.host)),
      h("button", {
        class: "ghost icon hover-only", type: "button", title: t("history.forget_site_title", { host: entry.host }),
        onclick: () => forgetHost(entry.host),
      }, t("history.forget_site")),
      h("button", {
        class: "ghost icon hover-only", type: "button", title: t("history.remove"), "aria-label": t("history.remove"),
        onclick: () => removeEntry(entry.id),
      }, "✕"));
  }

  async function removeEntry(id) {
    try {
      await lilx.post("history.delete", { id });
      entries = entries.filter((e) => e.id !== id);
      render();
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  }

  async function forgetHost(host) {
    try {
      const { removed } = await lilx.post("history.delete_host", { host });
      lilx.toast(t("history.removed_site", { n: removed, host }));
      load();
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  }

  lilx.confirmClick(document.getElementById("clear-all"), async () => {
    try {
      await lilx.post("history.clear");
      lilx.toast(t("history.cleared"));
      load();
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  });

  query.addEventListener("input", lilx.debounce(() => load(), 200));
  lilx.bindSearchField(query);
  more.addEventListener("click", () => load(true));
  load();
})();
