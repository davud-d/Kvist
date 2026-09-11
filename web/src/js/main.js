/**
 * main.js — theme, language bar, reveal, nav state, and the two
 * enhancements (hero canvas, cast replay). Everything here is optional: the
 * page is complete before this file runs.
 */
import { mountHero } from "./hero.js";
import { mountPlayer } from "./player.js";
import { mountCursorGlow } from "./cursor-glow.js";

const THEME_KEY = "kvist.theme";
const LANG_KEY = "kvist.lang-bar";

const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
const reducedMotion = () => motionQuery.matches;
motionQuery.addEventListener?.("change", () => {
  window.dispatchEvent(new CustomEvent("kvist:motion"));
});

/* -- theme ----------------------------------------------------------------- */
/* The initial value is applied by the inline script in <head>; this only wires
   the toggle and tells the canvas to re-read its colours. */

function currentTheme() {
  const explicit = document.documentElement.dataset.theme;
  if (explicit) return explicit;
  return window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

function setupTheme() {
  const button = document.querySelector("[data-theme-toggle]");
  if (!button) return;
  const label = () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    button.textContent = button.dataset[next];
    button.setAttribute("aria-pressed", currentTheme() === "light" ? "true" : "false");
  };
  label();
  button.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* private mode: the choice simply does not outlive the tab */
    }
    label();
    window.dispatchEvent(new CustomEvent("kvist:theme"));
  });
}

/* -- language bar ---------------------------------------------------------- */
/* No redirect: a shared link must land where it was shared. This only offers. */

function setupLangBar() {
  const bar = document.querySelector("[data-langbar]");
  if (!bar) return;
  const wanted = bar.dataset.langbar; // the locale this bar offers
  const speaks = (navigator.language || "").toLowerCase().startsWith(wanted);
  let dismissed = false;
  try {
    dismissed = localStorage.getItem(LANG_KEY) === "off";
  } catch {
    dismissed = false;
  }
  if (!speaks || dismissed) return;
  bar.hidden = false;
  // The bar is fixed, so the page has to give up the height it covers or the
  // footer sits underneath it.
  const reserve = () =>
    document.body.style.setProperty("padding-bottom", `${bar.offsetHeight}px`);
  reserve();
  window.addEventListener("resize", reserve, { passive: true });
  bar.querySelector("[data-langbar-close]")?.addEventListener("click", () => {
    bar.hidden = true;
    document.body.style.removeProperty("padding-bottom");
    try {
      localStorage.setItem(LANG_KEY, "off");
    } catch {
      /* nothing to remember it with; the bar comes back next visit */
    }
  });
}

/* -- reveal ---------------------------------------------------------------- */
/* One observer for the page. Children carry their own delay in --i. */

function setupReveal() {
  const groups = document.querySelectorAll(".reveal");
  if (!groups.length) return;
  if (!("IntersectionObserver" in window)) {
    for (const group of groups) group.classList.add("seen");
    return;
  }
  const watcher = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add("seen");
        watcher.unobserve(entry.target);
      }
    },
    { rootMargin: "0px 0px -12% 0px", threshold: 0.08 },
  );
  for (const group of groups) {
    [...group.children].forEach((child, index) => {
      child.style.setProperty("--i", String(index));
    });
    watcher.observe(group);
  }
}

/* -- nav state ------------------------------------------------------------- */

function setupRail() {
  const links = [...document.querySelectorAll(".rail a[href^='#']")];
  const sections = links
    .map((link) => document.getElementById(link.hash.slice(1)))
    .filter(Boolean);
  if (!sections.length || !("IntersectionObserver" in window)) return;

  const watcher = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const link = links.find((a) => a.hash === `#${entry.target.id}`);
        if (!link) continue;
        if (entry.isIntersecting) {
          for (const other of links) other.removeAttribute("aria-current");
          link.setAttribute("aria-current", "true");
        }
      }
    },
    { rootMargin: "-45% 0px -50% 0px" },
  );
  for (const section of sections) watcher.observe(section);
}

/* -- boot ------------------------------------------------------------------ */

setupTheme();
setupLangBar();
setupReveal();
setupRail();

const canvas = document.querySelector("[data-hero-canvas]");
if (canvas) mountHero(canvas, { reducedMotion });

mountCursorGlow(document.querySelector("[data-cursor-glow]"), { reducedMotion });

for (const replay of document.querySelectorAll("[data-replay]")) {
  mountPlayer(replay, {
    reducedMotion,
    strings: {
      play: replay.dataset.play,
      pause: replay.dataset.pause,
    },
  });
}
