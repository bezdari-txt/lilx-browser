// lilBlock scriptlet for YouTube.
//
// YouTube video ads are not separate requests to ad servers: the list of ad breaks
// ("adPlacements", "playerAds", "adSlots") is embedded in the player data of the page
// and of /youtubei/v1/player responses, and the ad videos come from the same servers
// as normal videos. Network filters therefore cannot catch them. This script removes
// the ad data before the player reads it, hides ad elements and, as a fallback,
// skips an ad that still starts. Injected at document creation, only on youtube.com.
(() => {
  "use strict";
  if (!/(^|\.)youtube(-nocookie)?\.com$/.test(location.hostname) || window.__lilblockYouTube) return;
  window.__lilblockYouTube = true;

  const AD_KEYS = ["adPlacements", "adSlots", "playerAds", "adBreakHeartbeatParams"];

  function prune(value) {
    if (!value || typeof value !== "object") return value;
    for (const key of AD_KEYS) {
      if (key in value) delete value[key];
    }
    // Player data is also nested in several response shapes.
    if (value.playerResponse) prune(value.playerResponse);
    if (value.response) prune(value.response);
    if (Array.isArray(value)) value.forEach(prune);
    return value;
  }

  // 1. JSON coming from the page and the player API.
  const originalParse = JSON.parse;
  JSON.parse = function lilblockParse(...args) {
    return prune(originalParse.apply(this, args));
  };
  const originalJson = Response.prototype.json;
  Response.prototype.json = function lilblockJson(...args) {
    return originalJson.apply(this, args).then(prune);
  };

  // 2. Data assigned directly in inline scripts.
  for (const name of ["ytInitialPlayerResponse", "playerResponse"]) {
    let stored;
    try {
      Object.defineProperty(window, name, {
        configurable: true,
        get: () => stored,
        set: (value) => { stored = prune(value); },
      });
    } catch (error) { /* already defined by the page */ }
  }

  // 3. Hide ad slots in the page layout.
  const css = [
    "#masthead-ad", "#player-ads", "#panels ytd-ads-engagement-panel-content-renderer",
    "ytd-ad-slot-renderer", "ytd-in-feed-ad-layout-renderer", "ytd-banner-promo-renderer",
    "ytd-promoted-sparkles-web-renderer", "ytd-promoted-video-renderer", "ytd-display-ad-renderer",
    "ytd-compact-promoted-video-renderer", "ytd-statement-banner-renderer", "ytd-search-pyv-renderer",
    "ytd-rich-item-renderer:has(> #content > ytd-ad-slot-renderer)",
    "ytd-reel-video-renderer:has(.ytd-ad-slot-renderer)", "ytm-promoted-sparkles-web-renderer",
    ".ytp-ad-overlay-container", ".ytp-ad-image-overlay", ".ytd-merch-shelf-renderer",
  ].join(",\n") + " { display: none !important; }";
  const addStyle = () => {
    const style = document.createElement("style");
    style.id = "lilblock-youtube";
    style.textContent = css;
    (document.head || document.documentElement).append(style);
  };
  if (document.documentElement) addStyle();
  else document.addEventListener("readystatechange", addStyle, { once: true });

  // 4. Fallback: if an ad still plays, jump to its end and press "Skip".
  //    (No muting: ads and the video share one <video> element, so it would stay muted.)
  setInterval(() => {
    const player = document.querySelector(".html5-video-player.ad-showing, .html5-video-player.ad-interrupting");
    if (!player) return;
    const video = player.querySelector("video");
    if (video && Number.isFinite(video.duration) && video.duration > 0) video.currentTime = video.duration;
    player.querySelector(".ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-skip-ad-button")?.click();
  }, 300);
})();
