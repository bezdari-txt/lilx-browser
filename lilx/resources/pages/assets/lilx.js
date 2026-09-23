// Shared helpers of lilx:// pages. No innerHTML with dynamic data: everything
// shown from history/downloads goes through textContent.
"use strict";

const lilx = (() => {
  const lang = LILX_I18N[document.documentElement.lang] ? document.documentElement.lang : "en";

  /** t("history.removed_site", {n: 3, host: "a.com"}) */
  function t(key, values = {}) {
    const text = LILX_I18N[lang][key] ?? LILX_I18N.en[key] ?? key;
    return text.replace(/\{(\w+)\}/g, (match, name) => (name in values ? String(values[name]) : match));
  }

  /** Translate static markup: data-i18n (text), data-i18n-placeholder, data-i18n-title. */
  function translatePage(root = document) {
    root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    root.querySelectorAll("[data-i18n-title]").forEach((el) => {
      el.title = t(el.dataset.i18nTitle);
      el.setAttribute("aria-label", el.title);
    });
  }
  translatePage();

  // The accent color can change while a page is open: reload only the generated stylesheet.
  window.addEventListener("lilx:settings-changed", () => {
    const link = document.getElementById("theme-css");
    if (link) link.href = `/assets/theme.css?v=${Date.now()}`;
  });

  // Arguments travel as JSON in the "p" query parameter; state-changing calls use POST.
  async function request(name, args, method) {
    const url = `/api/${name}?p=${encodeURIComponent(JSON.stringify(args || {}))}`;
    const response = await fetch(url, { method, cache: "no-store" });
    if (!response.ok) throw new Error(`request failed (${response.status})`);
    const json = await response.json();
    if (!json.ok) throw new Error(json.error || "request failed");
    return json.data;
  }

  /** h("div", {class: "x", onclick: fn}, child, "text") */
  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else if (key === "class") el.className = value;
      else if (key === "dataset") Object.assign(el.dataset, value);
      else if (value === true) el.setAttribute(key, "");
      else el.setAttribute(key, value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  let toastTimer = null;
  function toast(message, kind = "") {
    let el = document.querySelector(".toast");
    if (!el) {
      el = h("div", { class: "toast", role: "status" });
      document.body.append(el);
    }
    el.textContent = message;
    el.className = `toast show ${kind}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = `toast ${kind}`; }, 2600);
  }

  /** Two-step destructive button: first click arms, second click runs. */
  function confirmClick(button, action, prompt = t("common.confirm")) {
    let armed = false;
    let timer = null;
    const label = button.textContent;
    button.addEventListener("click", async () => {
      if (!armed) {
        armed = true;
        button.textContent = prompt;
        button.classList.add("confirming");
        timer = setTimeout(reset, 3000);
        return;
      }
      reset();
      await action();
    });
    function reset() {
      armed = false;
      clearTimeout(timer);
      button.textContent = label;
      button.classList.remove("confirming");
    }
  }

  function formatBytes(bytes) {
    if (bytes === undefined || bytes === null || bytes < 0) return "";
    const units = lang === "ru" ? ["Б", "КБ", "МБ", "ГБ", "ТБ"] : ["B", "KB", "MB", "GB", "TB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
    return `${value.toFixed(value < 10 && unit ? 1 : 0)} ${units[unit]}`;
  }

  function formatTime(seconds) {
    return new Date(seconds * 1000).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
  }

  function formatDay(seconds) {
    const date = new Date(seconds * 1000);
    const today = new Date();
    const yesterday = new Date(Date.now() - 86400000);
    if (date.toDateString() === today.toDateString()) return t("day.today");
    if (date.toDateString() === yesterday.toDateString()) return t("day.yesterday");
    return date.toLocaleDateString(lang, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  }

  function hostOf(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
  }

  function avatar(text) {
    const letter = (hostOf(text) || "?").charAt(0);
    return h("div", { class: "avatar", "aria-hidden": "true" }, letter);
  }

  /** Text of a key in every language, so search finds "font" and "шрифт" alike. */
  function allLanguages(key) {
    return Object.values(LILX_I18N).map((table) => table[key] || "").join(" ");
  }

  /** ⌘/Ctrl+F or "/" focuses the page's search field; Escape clears it. */
  function bindSearchField(input) {
    document.addEventListener("keydown", (event) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
      if (((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "f") || (event.key === "/" && !typing)) {
        event.preventDefault();
        input.focus();
        input.select();
      }
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && input.value) {
        input.value = "";
        input.dispatchEvent(new Event("input"));
      }
    });
  }

  function debounce(fn, ms) {
    let timer = null;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  }

  return {
    get: (name, args) => request(name, args, "GET"),
    post: (name, args) => request(name, args, "POST"),
    lang, t, translatePage, allLanguages, bindSearchField, h, toast, confirmClick, formatBytes, formatTime, formatDay, hostOf, avatar, debounce,
  };
})();
