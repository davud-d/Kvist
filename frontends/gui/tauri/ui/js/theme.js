// Interface preferences as CSS: palette, glass opacity, size, motion.

import { setSpeed } from "./motion.js";

export const THEMES = ["dusk", "moss", "ember", "paper", "mono"];

/** How the window was made; without transparency there is nothing behind the glass. */
export const windowState = { transparent: true, safe: false };

export function applyPrefs(prefs) {
  const root = document.documentElement;
  root.dataset.theme = THEMES.includes(prefs.theme) ? prefs.theme : "dusk";
  root.dataset.opaque = windowState.transparent ? "" : "1";
  root.style.setProperty("--glass", String(windowState.transparent ? prefs.glass : 1));
  root.style.setProperty("--zoom", String(prefs.scale));
  setSpeed(prefs.speed);
}
