// Interface preferences as CSS: palette, glass opacity, size, motion.

import { setSpeed } from "./motion.js";

export const THEMES = ["dusk", "moss", "ember", "paper", "mono"];

/** A web page has nothing behind it to see through, so the glass is always solid. */
export const windowState = { transparent: false, safe: false };

export function applyPrefs(prefs) {
  const root = document.documentElement;
  root.dataset.theme = THEMES.includes(prefs.theme) ? prefs.theme : "dusk";
  root.dataset.opaque = windowState.transparent ? "" : "1";
  root.style.setProperty("--glass", String(windowState.transparent ? prefs.glass : 1));
  root.style.setProperty("--zoom", String(prefs.scale));
  setSpeed(prefs.speed);
}
