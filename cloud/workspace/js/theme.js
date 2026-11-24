// Interface preferences as CSS: light or dark (or the system's), size, motion.

import { motion, setSpeed } from "./motion.js";
import { announce, setPref, store } from "./store.js";

export const THEMES = ["system", "light", "dark"];

/** A web page has nothing behind it to see through, so the glass is always solid. */
export const windowState = { transparent: false, safe: false };

// The pixel window had five palettes; "paper" was its light one, the others were dark.
const LEGACY = { paper: "light", dusk: "dark", moss: "dark", ember: "dark", mono: "dark" };

export function themeOf(prefs) {
  if (THEMES.includes(prefs.theme)) return prefs.theme;
  return LEGACY[prefs.theme] ?? "system";
}

const systemDark = () => typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;

/** What is on screen now: "light" or "dark". */
export function resolvedTheme(prefs = store.prefs) {
  const theme = themeOf(prefs);
  if (theme !== "system") return theme;
  return systemDark() ? "dark" : "light";
}

export function applyPrefs(prefs) {
  const root = document.documentElement;
  root.dataset.theme = themeOf(prefs);
  root.dataset.motion = prefs.speed > 0 ? "on" : "off";
  root.style.setProperty("--zoom", String(prefs.scale));
  setSpeed(prefs.speed);
}

const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Sets the theme. When the look really changes and motion is on, the new colours open as a circle from
 * `origin` (the button pressed) through a view transition; otherwise they simply apply.
 */
export function switchTheme(theme, origin = null) {
  const before = resolvedTheme();
  // The colours apply at once; the views redraw on the next frame, so the new page is captured after it.
  const commit = () => {
    setPref("theme", theme);
    return new Promise((resolve) => requestAnimationFrame(resolve));
  };
  const after = resolvedTheme({ ...store.prefs, theme });
  if (before === after || !document.startViewTransition || motion.speed === 0 || reduced()) {
    setPref("theme", theme);
    return;
  }
  const box = origin?.getBoundingClientRect();
  const x = box ? box.left + box.width / 2 : innerWidth - 40;
  const y = box ? box.top + box.height / 2 : 28;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  const transition = document.startViewTransition(commit);
  transition.ready.then(() => {
    document.documentElement.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 480, easing: "cubic-bezier(0.77, 0, 0.175, 1)", pseudoElement: "::view-transition-new(root)" },
    );
  }).catch(() => {
    // The browser skipped the transition (another one started); the theme is applied either way.
  });
}

// "As the system" follows the system while the page is open.
if (typeof matchMedia === "function") {
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (themeOf(store.prefs) === "system") announce({ kind: "prefs", key: "theme" });
  });
}
