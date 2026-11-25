// Tiny DOM helpers: element builder, line icons, a keyed list.

export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key === "data") Object.assign(el.dataset, value);
    else if (key === "html") el.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  el.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return el;
}

export const $ = (selector, root = document) => root.querySelector(selector);

/** Replaces everything inside `el`. */
export function fill(el, ...children) {
  el.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return el;
}

// Line icons on a 24-unit grid, drawn with round caps and joins so they sit with the rounded type.
const ICONS = {
  gear: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  image: '<rect x="4" y="5" width="16" height="14" rx="2.5"/><path d="M4 16l4.5-4.5 3.5 3.5 3-3L20 16"/><path d="M9 9.5h.01"/>',
  grid: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.8"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.8"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.8"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.8"/>',
  panel: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M15 4v16"/>',
  close: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="2"/>',
  pause: '<path d="M9 6.5v11M15 6.5v11"/>',
  play: '<path d="M8 5.8v12.4a1 1 0 0 0 1.5.9l9.6-6.2a1 1 0 0 0 0-1.7L9.5 4.9A1 1 0 0 0 8 5.8z"/>',
  bot: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3 19.5c.4-3.2 2.9-5.2 6-5.2s5.6 2 6 5.2"/><path d="M15.5 5.6a3 3 0 0 1 0 5.8M17.5 14.6c2 .6 3.3 2.4 3.5 4.9"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.8 3 2.8 15 0 18M12 3c-2.8 3-2.8 15 0 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  folder: '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.4l2.2 2.4h7.4A2.5 2.5 0 0 1 21 9.9v7.6a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
  bolt: '<path d="M4 20h4.2L19.4 8.8a2 2 0 0 0 0-2.8l-1.4-1.4a2 2 0 0 0-2.8 0L4 15.8z"/><path d="M13.5 6.5l4 4"/>',
  chev: '<path d="M9.5 6l6 6-6 6"/>',
  ctx: '<rect x="5" y="3" width="14" height="18" rx="2.5"/><path d="M9 8h6M9 12h6M9 16h3.5"/>',
  term: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7.5 9.5l2.5 2.5-2.5 2.5M13 15h3.5"/>',
  file: '<path d="M14 3H7.5A2.5 2.5 0 0 0 5 5.5v13A2.5 2.5 0 0 0 7.5 21h9a2.5 2.5 0 0 0 2.5-2.5V8z"/><path d="M14 3v5h5"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.4-4.4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5V12l3 2"/>',
  server: '<rect x="3.5" y="4" width="17" height="7" rx="2"/><rect x="3.5" y="13" width="17" height="7" rx="2"/><path d="M7.5 7.5h.01M7.5 16.5h.01"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.6 4.6L6 6M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4"/>',
  moon: '<path d="M20 14.6A8.2 8.2 0 0 1 9.4 4a8.2 8.2 0 1 0 10.6 10.6z"/>',
  twig: '<path d="M6 20c3.5-2 7-6.5 8.5-14"/><path d="M9.6 15.2C7 15 5.2 13.4 4.6 10.8c2.6.1 4.4 1.7 5 4.4zM13 10.6c.2-2.8 1.9-4.6 4.8-5.2.1 2.8-1.6 4.7-4.8 5.2z"/>',
  list: '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><path d="M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
  arrowLeft: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  download: '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
  gauge: '<path d="M4.5 17.5a8.5 8.5 0 1 1 15 0"/><path d="M12 13.5l3.5-4.5"/><circle cx="12" cy="14" r="1.2"/>',
  reset: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4 4.5v4h4"/>',
  min: '<path d="M5 12h14"/>',
  max: '<rect x="5" y="5" width="14" height="14" rx="2.5"/>',
};

export function icon(name, size = 16) {
  const span = h("span", { class: "icon", style: { width: `${size}px`, height: `${size}px` } });
  span.innerHTML = `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] ?? ICONS.bolt}</svg>`;
  return span;
}

/** A track filled to `share` (0..1); below a quarter it turns amber, empty it turns red. */
export function meterTrack(share, { big = false, warnBelow = 0 } = {}) {
  const value = Math.max(0, Math.min(1, Number(share) || 0));
  const tone = value === 0 ? " empty" : value < warnBelow ? " low" : "";
  return h("span", { class: `meter-track${big ? " big" : ""}${tone}`, role: "meter", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.round(value * 100)) }, h("i", { style: { transform: `scaleX(${value})` } }));
}

/**
 * Keeps `container`'s children in step with `items`: an element per key,
 * created once, updated when the item's `rev` moved, removed when gone.
 * `make(item)` builds an element, `update(el, item)` refreshes it.
 */
export function keyedList(container, { key, make, update, onEnter, rev = (item) => item.rev }) {
  const cache = new Map();
  return (items) => {
    const seen = new Set();
    let previous = null;
    for (const item of items) {
      const k = key(item);
      seen.add(k);
      let entry = cache.get(k);
      if (!entry) {
        entry = { el: make(item), rev: rev(item) };
        entry.el.dataset.key = k;
        cache.set(k, entry);
        onEnter?.(entry.el, item);
      } else if (entry.rev !== rev(item)) {
        update(entry.el, item);
        entry.rev = rev(item);
      }
      const wanted = previous ? previous.nextSibling : container.firstChild;
      if (entry.el !== wanted) container.insertBefore(entry.el, wanted);
      previous = entry.el;
    }
    for (const [k, entry] of cache) {
      if (seen.has(k)) continue;
      entry.el.remove();
      cache.delete(k);
    }
  };
}

/**
 * Puts a looping CSS animation on the page's one clock: an element made again by a repaint picks up where the
 * one it replaced stood, instead of starting its turn over (which reads as a flicker).
 */
export function inPhase(el, periodMs) {
  el.style.animationDelay = `-${Math.round(performance.now() % periodMs)}ms`;
  return el;
}

/** The turning circle of work in progress, in phase with every other one. */
export const spinner = () => inPhase(h("span", { class: "spin" }), 900);

/** Calls `fn` at most once per animation frame. */
export function frameThrottle(fn) {
  let queued = false;
  return () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      fn();
    });
  };
}

export function basename(path) {
  const parts = String(path).split(/[\\/]+/).filter(Boolean);
  return parts.at(-1) ?? path;
}
