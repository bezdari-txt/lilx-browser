"use strict";

(() => {
  const { h, t } = lilx;
  const $ = (id) => document.getElementById(id);

  const TOGGLES = [
    ["history_enabled", "privacy.history", "privacy.history_desc"],
    ["block_third_party_cookies", "privacy.tp", "privacy.tp_desc"],
    ["send_privacy_signals", "privacy.gpc", "privacy.gpc_desc"],
    ["javascript_enabled", "privacy.js", "privacy.js_desc"],
  ];

  let state = null;
  // Controls are built once and then only updated, so switches animate instead of being re-created.
  const controls = { segments: {}, toggles: {}, selects: {} };

  async function call(method, body, message) {
    try {
      state = await lilx.post(method, body);
      render();
      if (message) lilx.toast(message);
      return true;
    } catch (error) {
      lilx.toast(error.message, "error");
      render();
      return false;
    }
  }

  const set = (key, value, message = t("settings.saved")) => call("settings.set", { key, value }, message);

  // ---------- static controls ----------
  const searchText = (...keys) => keys.filter(Boolean).map(lilx.allLanguages).join(" ");

  function buildSegmented(id, name, options, onChange) {
    const inputs = {};
    $(id).replaceChildren(...options.map(([value, label]) => {
      const input = h("input", { type: "radio", name, value, onchange: () => onChange(value) });
      inputs[value] = input;
      return h("label", {}, input, label);
    }));
    controls.segments[name] = inputs;
  }

  function buildToggle(container, key, labelKey, descKey) {
    const input = h("input", { type: "checkbox", "aria-label": t(labelKey), onchange: (e) => set(key, e.target.checked) });
    const desc = h("div", { class: "desc" }, descKey ? t(descKey) : "");
    container.append(h("label", { class: "setting", dataset: { search: searchText(labelKey, descKey) } },
      h("div", {}, h("div", { class: "label" }, t(labelKey)), desc),
      h("span", { class: "switch" }, input, h("span", {}))));
    controls.toggles[key] = { input, desc };
  }

  function buildSelect(container, key, labelKey, descKey, options) {
    const select = h("select", {
      "aria-label": t(labelKey),
      onchange: (e) => {
        const option = options.find(([value]) => String(value) === e.target.value);
        set(key, option ? option[0] : e.target.value);
      },
    }, options.map(([value, label]) => h("option", { value: String(value) }, label)));
    container.append(h("label", { class: "setting", dataset: { search: searchText(labelKey, descKey) } },
      h("div", {}, h("div", { class: "label" }, t(labelKey)), descKey ? h("div", { class: "desc" }, t(descKey)) : null),
      select));
    controls.selects[key] = select;
  }

  function build() {
    buildSegmented("languages", "language", Object.entries(state.languages), (value) => set("language", value));
    buildSegmented("engines", "engine", state.engines.map((e) => [e.id, e.name]),
      (value) => set("search_engine", value, t("settings.search_changed")));
    buildSegmented("themes", "theme", state.themes.map((th) => [th, t(`theme.${th}`)]),
      (value) => set("theme", value, t("settings.theme_changed")));
    buildSegmented("accent-modes", "accent_mode", [["default", t("accent.default")], ["custom", t("accent.custom")]],
      (value) => set("accent_mode", value, ""));
    buildAccentEditor();
    for (const [key, label, desc] of TOGGLES) buildToggle($("privacy-toggles"), key, label, desc);
    buildToggle($("lilblock-toggle"), "adblock_enabled", "lilblock.enable", null);

    buildToggle($("home-toggles"), "show_home_bookmarks", "home.show_bookmarks", "home.show_bookmarks_desc");
    buildToggle($("home-toggles"), "show_top_sites", "home.show_top", "home.show_top_desc");

    const fonts = [["", t("content.font_default")], ...state.fonts.map((f) => [f, f])];
    const sizes = [12, 13, 14, 15, 16, 17, 18, 20, 22, 24].map((n) => [n, `${n} px`]);
    const zooms = state.zoom_levels.map((z) => [z, `${z}%`]);
    buildSelect($("content-selects"), "font_standard", "content.font", "content.font_desc", fonts);
    buildSelect($("content-selects"), "font_fixed", "content.font_fixed", null, fonts);
    buildSelect($("content-selects"), "font_size", "content.font_size", null, sizes);
    buildSelect($("content-selects"), "default_zoom", "content.zoom", "content.zoom_desc", zooms);
    buildToggle($("content-toggles"), "smooth_scrolling", "content.smooth", "content.smooth_desc");
    buildToggle($("content-toggles"), "block_autoplay", "content.autoplay", "content.autoplay_desc");
  }

  // ---------- accent color ----------
  const PRESETS = ["#3b82f6", "#0ea5e9", "#14b8a6", "#22c55e", "#84cc16", "#f59e0b",
                   "#f97316", "#ef4444", "#ec4899", "#a855f7", "#7456e8", "#64748b"];
  const channels = {};  // r/g/b -> {range, number}
  let saveTimer = null;

  const toHex = (rgb) => "#" + rgb.map((c) => Math.max(0, Math.min(255, c | 0)).toString(16).padStart(2, "0")).join("");
  const fromHex = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  // Same rule as lilx/ui/theme.py: white while it keeps 3:1 contrast, otherwise near-black.
  function readableText(rgb) {
    const [r, g, b] = rgb.map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
    const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    return 1.05 / (luminance + 0.05) >= 3 ? "#ffffff" : "#131217";
  }

  function buildAccentEditor() {
    $("accent-swatches").replaceChildren(...PRESETS.map((color) => {
      const swatch = h("button", {
        class: "swatch", type: "button", title: color, "aria-label": color,
        onclick: () => pickAccent(color, true),
      });
      swatch.style.background = color;  // CSSOM: allowed by the page CSP
      return swatch;
    }));
    for (const [channel, label] of [["r", "R"], ["g", "G"], ["b", "B"]]) {
      const range = h("input", { type: "range", min: "0", max: "255", "aria-label": label,
                                 oninput: () => fromChannels(false), onchange: () => fromChannels(true) });
      const number = h("input", { type: "number", min: "0", max: "255", "aria-label": label,
                                  oninput: () => { range.value = number.value; fromChannels(false); },
                                  onchange: () => fromChannels(true) });
      channels[channel] = { range, number };
      $("accent-rgb").append(h("label", { class: `rgb-row ch-${channel}` }, h("span", {}, label), range, number));
    }
    $("accent-hex").addEventListener("change", () => {
      const value = $("accent-hex").value.trim().toLowerCase();
      const hex = value.startsWith("#") ? value : `#${value}`;
      if (/^#[0-9a-f]{6}$/.test(hex)) pickAccent(hex, true);
      else { lilx.toast(t("accent.bad_hex"), "error"); showAccent(state.settings.accent_color); }
    });
    $("accent-picker").addEventListener("input", () => pickAccent($("accent-picker").value, false));
    $("accent-picker").addEventListener("change", () => pickAccent($("accent-picker").value, true));
  }

  function showAccent(hex) {
    const rgb = fromHex(hex);
    ["r", "g", "b"].forEach((c, i) => { channels[c].range.value = rgb[i]; channels[c].number.value = rgb[i]; });
    $("accent-hex").value = hex;
    $("accent-picker").value = hex;
    $("accent-preview-button").style.background = hex;
    $("accent-preview-button").style.color = readableText(rgb);
    $("accent-preview-switch").style.background = hex;
    // Colored slider tracks show what each channel contributes.
    const [r, g, b] = rgb;
    channels.r.range.style.background = `linear-gradient(to right, rgb(0,${g},${b}), rgb(255,${g},${b}))`;
    channels.g.range.style.background = `linear-gradient(to right, rgb(${r},0,${b}), rgb(${r},255,${b}))`;
    channels.b.range.style.background = `linear-gradient(to right, rgb(${r},${g},0), rgb(${r},${g},255))`;
    document.querySelectorAll(".swatch").forEach((el) => el.classList.toggle("on", el.title === hex));
  }

  function fromChannels(commit) {
    pickAccent(toHex(["r", "g", "b"].map((c) => Number(channels[c].range.value))), commit);
  }

  /** Preview immediately; save shortly after (dragging stays smooth) or at once on commit. */
  function pickAccent(hex, commit) {
    showAccent(hex);
    clearTimeout(saveTimer);
    const save = () => { if (hex !== state.settings.accent_color) set("accent_color", hex, ""); };
    if (commit) save();
    else saveTimer = setTimeout(save, 150);
  }

  // ---------- rendering ----------
  function check(name, value) {
    for (const [v, input] of Object.entries(controls.segments[name])) input.checked = v === value;
  }

  function emptyRow(key) {
    return h("div", { class: "empty small" }, t(key));
  }

  function ruleRow(rule) {
    const kinds = [
      rule.clear_cookies && t("forget.kind_cookies"),
      rule.clear_site_data && t("forget.kind_site_data"),
      rule.clear_history && t("forget.kind_history"),
    ].filter(Boolean).join(", ");
    return h("div", { class: "list-item" },
      lilx.avatar(`https://${rule.host}`),
      h("div", { class: "grow" },
        h("span", { class: "title" }, rule.host),
        h("div", { class: "sub" }, t("forget.forget_kinds", { kinds }))),
      h("button", {
        class: "ghost icon", type: "button", title: t("forget.remove_title"),
        onclick: () => call("settings.site_rule_remove", { host: rule.host }),
      }, t("forget.remove")));
  }

  function allowRow(host) {
    return h("div", { class: "list-item" },
      lilx.avatar(`https://${host}`),
      h("div", { class: "grow" }, h("span", { class: "title" }, host)),
      h("button", {
        class: "ghost icon", type: "button",
        onclick: () => call("settings.adblock_block", { host }),
      }, t("lilblock.turn_on")));
  }

  function blockedHostRow(item) {
    return h("div", { class: "list-item compact" },
      lilx.avatar(`https://${item.host}`),
      h("div", { class: "grow" },
        h("span", { class: "title" }, item.host),
        h("div", { class: "sub" }, `${lilx.formatDay(item.last)} ${lilx.formatTime(item.last)}`)),
      h("span", { class: "count" }, t("lilblock.times", { n: item.count })));
  }

  function recentRow(item) {
    return h("div", { class: "list-item compact" },
      h("span", { class: "time" }, lilx.formatTime(item.time)),
      h("div", { class: "grow" },
        h("span", { class: "title", title: item.url }, item.host),
        h("div", { class: "sub", title: item.url }, item.page ? `${t("lilblock.on_page", { page: item.page })} · ${item.url}` : item.url)));
  }

  function render() {
    const s = state.settings;
    check("language", state.language);
    check("engine", s.search_engine);
    check("theme", s.theme);
    check("accent_mode", s.accent_mode);
    $("accent-editor").hidden = s.accent_mode !== "custom";
    // Don't fight the user while they drag a slider or type a value.
    if (!$("accent-editor").contains(document.activeElement)) showAccent(s.accent_color);
    for (const [key, { input }] of Object.entries(controls.toggles)) input.checked = Boolean(s[key]);
    for (const [key, select] of Object.entries(controls.selects)) {
      if (![...select.options].some((o) => o.value === String(s[key]))) {
        select.append(h("option", { value: String(s[key]) }, String(s[key])));  // e.g. a custom zoom
      }
      select.value = String(s[key]);
    }
    // Live preview in the chosen fonts (CSSOM, allowed by the page CSP).
    $("font-preview").style.fontFamily = s.font_standard ? `"${s.font_standard}", sans-serif` : "";
    $("font-preview").style.fontSize = `${s.font_size}px`;
    $("font-preview-mono").style.fontFamily = s.font_fixed ? `"${s.font_fixed}", monospace` : "monospace";

    if (document.activeElement !== $("homepage")) $("homepage").value = s.homepage;
    if (document.activeElement !== $("download-dir")) $("download-dir").value = s.download_dir;
    $("download-dir-current").textContent = state.download_dir;
    $("download-dir-current").title = state.download_dir;
    $("download-dir-kind").textContent = t(s.download_dir ? "dl.kind_custom" : "dl.kind_default");
    $("download-dir-default").hidden = !s.download_dir;

    // lilBlock
    const lb = state.lilblock;
    controls.toggles.adblock_enabled.desc.textContent =
      t("lilblock.enable_desc", { rules: lb.rules.toLocaleString(lilx.lang), lists: lb.lists.join(", ") });
    $("lilblock-lists").textContent = t("lilblock.extra_lists", { dir: lb.extra_lists_dir });
    $("allowlist").replaceChildren(...(s.adblock_allowlist.length
      ? s.adblock_allowlist.map(allowRow) : [emptyRow("lilblock.no_exceptions")]));
    $("clear-allowlist").disabled = !s.adblock_allowlist.length;
    $("block-total").textContent = t("lilblock.total", { n: lb.log.total.toLocaleString(lilx.lang) });
    $("block-hosts").replaceChildren(...(lb.log.hosts.length
      ? lb.log.hosts.slice(0, 12).map(blockedHostRow) : [emptyRow("lilblock.empty_history")]));
    $("block-recent").replaceChildren(...(lb.log.recent.length
      ? lb.log.recent.slice(0, 12).map(recentRow) : [emptyRow("lilblock.empty_history")]));
    $("clear-block-log").disabled = !lb.log.total && !lb.log.recent.length;

    // home page
    const hidden = s.top_sites_hidden;
    $("hidden-sites-desc").textContent = hidden.length ? hidden.join(", ") : t("homecard.hidden_none");
    $("restore-top-sites").disabled = !hidden.length;

    // forget on close
    $("rules").replaceChildren(...(s.forget_on_close.length
      ? s.forget_on_close.map(ruleRow) : [emptyRow("forget.empty")]));
    $("site-data-note").textContent = t("forget.note", { kinds: state.unsupported_site_data.join(", ") });

    // browsing data
    const st = state.storage;
    $("storage-summary").textContent =
      t("data.summary", { history: st.history_entries, cookies: st.cookies, sites: st.cookie_sites });
    $("storage-location").textContent = t("data.location", { dir: st.data_dir });
    $("encryption-badge").textContent = t(st.encrypted ? "data.encrypted" : "data.not_encrypted");
    $("encryption-badge").className = `badge ${st.encrypted ? "good" : "warn"}`;

    const a = state.about;
    $("about").textContent =
      `lilx ${a.lilx} · PySide6 ${a.pyside} · QtWebEngine ${a.qtwebengine} · Chromium ${a.chromium}`;
  }

  // ---------- forms and buttons ----------
  $("restore-top-sites").addEventListener("click",
    () => call("settings.top_sites_restore", {}, t("homecard.restored")));

  // ---------- search in settings ----------
  // A card matches by its title/description; otherwise only its matching rows stay visible.
  // Every text is searched in all languages (data-i18n keys and data-search attributes).
  const cardText = (card) => [...card.querySelectorAll("[data-i18n]")]
    .map((el) => lilx.allLanguages(el.dataset.i18n)).join(" ") + " " + card.textContent;

  function filterSettings() {
    const q = $("settings-query").value.trim().toLowerCase();
    let visible = 0;
    for (const card of document.querySelectorAll("main > .card")) {
      const rows = [...card.querySelectorAll(".setting")];
      if (!q) {
        card.hidden = false;
        rows.forEach((row) => { row.hidden = false; });
        visible += 1;
        continue;
      }
      const head = [card.querySelector("h2"), card.querySelector(":scope > .hint")].filter(Boolean);
      const headMatch = head.some((el) => (lilx.allLanguages(el.dataset.i18n || "") + " " + el.textContent)
        .toLowerCase().includes(q));
      let rowMatch = false;
      for (const row of rows) {
        const match = headMatch || ((row.dataset.search || "") + " " + row.textContent).toLowerCase().includes(q);
        row.hidden = !match;
        rowMatch = rowMatch || match;
      }
      const show = headMatch || rowMatch || cardText(card).toLowerCase().includes(q);
      if (show && !headMatch && !rowMatch) rows.forEach((row) => { row.hidden = false; });
      card.hidden = !show;
      if (show) visible += 1;
    }
    $("settings-no-match").hidden = visible > 0;
  }

  $("settings-query").addEventListener("input", filterSettings);
  lilx.bindSearchField($("settings-query"));

  $("homepage-form").addEventListener("submit", (event) => {
    event.preventDefault();
    set("homepage", $("homepage").value, t("settings.homepage_saved"));
  });

  $("download-form").addEventListener("submit", (event) => {
    event.preventDefault();
    set("download_dir", $("download-dir").value, t("dl.saved"));
  });

  $("download-dir-choose").addEventListener("click", () => lilx.post("settings.choose_download_dir")
    .catch((error) => lilx.toast(error.message, "error")));
  $("download-dir-open").addEventListener("click", () => lilx.post("settings.open_download_dir")
    .catch((error) => lilx.toast(error.message, "error")));
  $("download-dir-default").addEventListener("click", () => set("download_dir", "", t("dl.reset")));

  // The folder dialog and other windows change settings outside this page.
  window.addEventListener("lilx:settings-changed", async () => {
    if (!state) return;
    const before = state.download_dir;
    try {
      state = await lilx.get("settings.get");
      render();
      if (state.download_dir !== before) lilx.toast(t("dl.changed", { dir: state.download_dir }));
    } catch (error) {
      lilx.toast(error.message, "error");
    }
  });

  $("allow-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const host = $("allow-host").value;
    if (await call("settings.adblock_allow", { host })) {
      $("allow-host").value = "";
      lilx.toast(t("lilblock.added", { host: host.trim() }));
    }
  });

  lilx.confirmClick($("clear-allowlist"),
    () => call("settings.adblock_clear_allowlist", {}, t("lilblock.exceptions_cleared")));
  lilx.confirmClick($("clear-block-log"),
    () => call("settings.adblock_clear_log", {}, t("lilblock.history_cleared")));

  $("rule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const ok = await call("settings.site_rule_set", {
      host: $("rule-host").value,
      clear_cookies: $("rule-cookies").checked,
      clear_site_data: $("rule-site-data").checked,
      clear_history: $("rule-history").checked,
    }, t("forget.added"));
    if (ok) $("rule-host").value = "";
  });

  const clearActions = {
    "clear-history": [{ history: true }, "data.cleared_history"],
    "clear-cookies": [{ cookies: true }, "data.cleared_cookies"],
    "clear-cache": [{ cache: true }, "data.cleared_cache"],
    "clear-downloads": [{ downloads: true }, "data.cleared_downloads"],
  };
  for (const [id, [body, message]] of Object.entries(clearActions)) {
    lilx.confirmClick($(id), async () => {
      await call("settings.clear_data", body, t(message));
      // Cookie deletion is asynchronous in the engine; refresh the counters shortly after.
      setTimeout(async () => { state = await lilx.get("settings.get"); render(); }, 500);
    });
  }

  // ---------- reset: three consecutive presses ----------
  const resetButton = $("reset-button");
  const resetSteps = [...$("reset-steps").children];
  let presses = 0;
  let resetTimer = null;

  function showPresses() {
    resetSteps.forEach((step, i) => step.classList.toggle("on", i < presses));
    resetButton.classList.toggle("confirming", presses > 0);
  }

  function cancelReset() {
    presses = 0;
    resetButton.textContent = t("reset.button");
    showPresses();
  }

  resetButton.addEventListener("click", async () => {
    clearTimeout(resetTimer);
    presses += 1;
    showPresses();
    if (presses < 3) {
      resetButton.textContent = t("reset.progress", { n: presses });
      resetTimer = setTimeout(cancelReset, 4000);  // presses must follow each other
      return;
    }
    resetButton.textContent = t("reset.final");
    resetButton.disabled = true;
    try {
      await lilx.post("settings.reset", { confirm: "reset" });
      lilx.toast(t("reset.restarting"));
    } catch (error) {
      lilx.toast(error.message, "error");
      resetButton.disabled = false;
      cancelReset();
    }
  });

  lilx.get("settings.get")
    .then((data) => {
      state = data;
      build();
      render();
      if (location.hash) document.querySelector(location.hash)?.scrollIntoView({ behavior: "smooth", block: "start" });
    })
    .catch((error) => lilx.toast(error.message, "error"));
})();
