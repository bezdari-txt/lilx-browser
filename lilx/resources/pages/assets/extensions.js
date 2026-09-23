"use strict";

(() => {
  const { h, t } = lilx;
  const $ = (id) => document.getElementById(id);
  let data = null;
  let lastSeq = null;

  async function load() {
    try {
      data = await lilx.get("extensions.list");
      announce(data.last_event);
      render();
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  }

  // Installs finish asynchronously in Qt; the browser then sends "lilx:settings-changed".
  function announce(event) {
    if (lastSeq !== null && event.seq !== lastSeq && event.kind && event.kind !== "error") {
      lilx.toast(t(`extensions.event_${event.kind}`, { name: event.name }));
    }
    lastSeq = event.seq;
  }

  function chips(items, cls) {
    return items.map((item) => h("span", { class: `chip ${cls}` }, item === "<all_urls>" ? t("extensions.all_sites") : item));
  }

  function card(ext) {
    const toggle = h("input", {
      type: "checkbox", checked: ext.enabled, "aria-label": t("extensions.enabled"),
      onchange: async (event) => {
        try {
          await lilx.post("extensions.set_enabled", { id: ext.id, enabled: event.target.checked });
          lilx.toast(t(event.target.checked ? "extensions.turned_on" : "extensions.turned_off", { name: ext.name }));
        } catch (error) {
          event.target.checked = !event.target.checked;
          lilx.toast(error.message, "error");
        }
      },
    });
    const remove = h("button", { class: "danger", type: "button" }, t("extensions.uninstall"));
    lilx.confirmClick(remove, async () => {
      try {
        await lilx.post("extensions.uninstall", { id: ext.id });
      } catch (error) {
        lilx.toast(error.message, "error");
      }
    });
    const icon = ext.icon
      ? h("img", { class: "ext-icon", src: ext.icon, alt: "" })
      : h("div", { class: "ext-icon placeholder", "aria-hidden": "true" }, (ext.name || "?").charAt(0));
    const access = [...chips(ext.hosts, "host"), ...chips(ext.permissions, "perm")];
    return h("article", { class: `ext ${ext.enabled ? "" : "off"}`, dataset: { id: ext.id } },
      icon,
      h("div", { class: "grow" },
        h("div", { class: "ext-title" },
          h("span", { class: "title" }, ext.name),
          ext.version ? h("span", { class: "badge" }, ext.version) : null,
          h("span", { class: "badge" }, `MV${ext.manifest_version || "?"}`)),
        ext.description ? h("p", { class: "ext-desc" }, ext.description) : null,
        ext.error ? h("p", { class: "ext-error" }, ext.error) : null,
        access.length ? h("div", { class: "chips" }, h("span", { class: "chips-label" }, t("extensions.access")), access) : null,
        h("div", { class: "ext-meta" }, `ID ${ext.id}`),
        h("div", { class: "actions" },
          ext.options ? h("a", { class: "button", href: ext.options, target: "_blank" }, t("extensions.options")) : null,
          remove)),
      h("label", { class: "switch", title: t("extensions.enabled") }, toggle, h("span", {})));
  }

  function renderErrors() {
    const box = $("errors");
    if (!data.errors.length) { box.replaceChildren(); return; }
    box.replaceChildren(h("div", { class: "card error-card" },
      h("div", { class: "error-head" },
        h("h2", {}, t("extensions.errors")),
        h("button", { class: "ghost small", type: "button", onclick: async () => {
          await lilx.post("extensions.dismiss_errors"); load();
        } }, t("extensions.dismiss"))),
      ...data.errors.map((e) => h("div", { class: "error-row" },
        h("span", { class: "time" }, lilx.formatTime(e.time)),
        h("div", { class: "grow" },
          h("div", { class: "title" }, e.source || t("extensions.unknown_source")),
          h("div", { class: "sub" }, e.message))))));
  }

  function render() {
    renderErrors();
    const q = $("query").value.trim().toLowerCase();
    const items = data.extensions.filter((ext) => !q ||
      [ext.name, ext.description, ext.id].some((text) => (text || "").toLowerCase().includes(q)));
    let empty = null;
    if (!data.extensions.length) empty = t("extensions.empty");
    else if (!items.length) empty = t("extensions.no_match");
    $("list").replaceChildren(...(empty ? [h("div", { class: "empty" }, empty)] : items.map(card)));
  }

  $("install-zip").addEventListener("click", () => lilx.post("extensions.install_zip")
    .catch((error) => lilx.toast(error.message, "error")));
  $("install-folder").addEventListener("click", () => lilx.post("extensions.install_folder")
    .catch((error) => lilx.toast(error.message, "error")));
  $("query").addEventListener("input", () => data && render());
  lilx.bindSearchField($("query"));
  window.addEventListener("lilx:settings-changed", load);
  load();
})();
