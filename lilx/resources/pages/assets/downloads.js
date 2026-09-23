"use strict";

(() => {
  const { h, t } = lilx;
  const container = document.getElementById("items");
  const query = document.getElementById("query");
  let timer = null;
  let items = [];

  const STATE_TEXT = {
    completed: "downloads.done",
    cancelled: "downloads.cancelled",
    interrupted: "downloads.failed",
  };

  function statusText(item) {
    if (item.state === "in_progress") {
      const received = lilx.formatBytes(item.received);
      const size = item.total > 0 ? t("downloads.of", { a: received, b: lilx.formatBytes(item.total) }) : received;
      return `${t(item.paused ? "downloads.paused" : "downloads.downloading")} · ${size}`;
    }
    if (item.state === "completed") {
      return item.exists ? `${lilx.formatBytes(item.total > 0 ? item.total : item.received)} · ${lilx.hostOf(item.url)}`
        : t("downloads.missing");
    }
    const reason = item.error ? ` — ${item.error}` : "";
    return `${STATE_TEXT[item.state] ? t(STATE_TEXT[item.state]) : item.state}${reason}`;
  }

  function actions(item) {
    const button = (label, method, title) =>
      h("button", { class: "ghost icon", type: "button", title, dataset: { action: method },
                    onclick: () => run(method, item.id) }, label);
    if (item.state === "in_progress") {
      return [
        item.paused
          ? button(t("downloads.resume"), "downloads.resume")
          : button(t("downloads.pause"), "downloads.pause"),
        button(t("downloads.cancel"), "downloads.cancel"),
      ];
    }
    const list = [];
    if (item.exists) {
      list.push(button(t("downloads.open"), "downloads.open"), button(t("downloads.show"), "downloads.show"));
    }
    list.push(button("✕", "downloads.remove", t("downloads.remove")));
    return list;
  }

  function row(item) {
    const percent = item.total > 0 ? Math.min(100, (item.received / item.total) * 100) : 0;
    return h("div", { class: "list-item" },
      h("div", { class: "avatar", "aria-hidden": "true" }, (item.file_name.split(".").pop() || "?").slice(0, 3)),
      h("div", { class: "grow" },
        h("span", { class: "title", title: item.file_name }, item.file_name,
          item.private ? h("span", { class: "badge private-badge" }, t("downloads.private")) : null),
        h("div", { class: "sub" }, statusText(item)),
        item.state === "in_progress"
          ? h("div", { class: `progress ${item.paused ? "paused" : ""}` }, h("div", { style: null, dataset: { p: percent } }))
          : null),
      h("div", { class: "actions" }, actions(item)));
  }

  async function run(method, id) {
    try {
      const ok = await lilx.post(method, { id });
      if (ok === false) lilx.toast(t("downloads.unavailable"), "error");
      load();
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  }

  function matches(item, q) {
    return [item.file_name, item.url, lilx.hostOf(item.url), item.directory]
      .some((text) => (text || "").toLowerCase().includes(q));
  }

  function render() {
    const q = query.value.trim().toLowerCase();
    const shown = q ? items.filter((item) => matches(item, q)) : items;
    let empty = null;
    if (!items.length) empty = t("downloads.empty");
    else if (!shown.length) empty = t("downloads.no_match");
    container.replaceChildren(...(empty ? [h("div", { class: "empty" }, empty)] : shown.map(row)));
    // Progress width is set through CSSOM (allowed by the CSP), not inline style attributes.
    container.querySelectorAll(".progress > div").forEach((bar) => { bar.style.width = `${bar.dataset.p}%`; });
  }

  query.addEventListener("input", render);
  lilx.bindSearchField(query);

  async function load() {
    clearTimeout(timer);
    try {
      const data = await lilx.get("downloads.list");
      document.getElementById("directory").textContent = data.directory;
      items = data.items;
      render();
      const active = data.items.some((item) => item.state === "in_progress");
      timer = setTimeout(load, active ? 700 : 3000);
    } catch (error) {
      lilx.toast(error.message, "error");
      timer = setTimeout(load, 5000);
    }
  }

  document.getElementById("clear").addEventListener("click", () => run("downloads.clear", ""));
  document.getElementById("change-dir").addEventListener("click", () => lilx.post("downloads.choose_dir")
    .catch((error) => lilx.toast(error.message, "error")));
  document.getElementById("open-dir").addEventListener("click", () => lilx.post("downloads.open_dir")
    .catch((error) => lilx.toast(error.message, "error")));

  // Sent by the browser after the folder dialog (or another page) changed the folder.
  window.addEventListener("lilx:settings-changed", async () => {
    const before = document.getElementById("directory").textContent;
    await load();
    const after = document.getElementById("directory").textContent;
    if (after !== before) lilx.toast(t("dl.changed", { dir: after }));
  });
  load();
})();
