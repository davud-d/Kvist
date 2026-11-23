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

const ICONS = {
  gear: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm0-5 2 3 3 .8 3 .2-.6 3 1.6 2-1.6 2 .6 3-3 .2-3 .8-2 3-2-3-3-.8-3-.2.6-3L3 12l1.6-2L4 7l3-.2 3-.8Z",
  plus: "M12 5v14M5 12h14",
  grid: "M4 4h6v6H4zm10 0h6v6h-6zM4 14h6v6H4zm10 0h6v6h-6z",
  panel: "M3 5h18v14H3zM14 5v14",
  menu: "M4 6h16M4 12h16M4 18h16",
  close: "m6 6 12 12M18 6 6 18",
  send: "M12 20V4m-6 6 6-6 6 6",
  stop: "M6 6h12v12H6z",
  pause: "M9 5v14m6-14v14",
  bot: "M5 8h14v11H5zM12 8V4M9 13h.01m6 0h.01",
  globe: "M21 12a9 9 0 1 0-18 0 9 9 0 0 0 18 0ZM3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z",
  check: "m5 12 4 4L19 6",
  folder: "M3 6h7l2 2h9v11H3Z",
  bolt: "m13 3-8 11h6l-1 7 9-11h-6Z",
  chev: "m9 6 6 6-6 6",
  ctx: "M4 4h16v16H4zm4 5h8m-8 3h8m-8 3h5",
  term: "M3 4h18v16H3zm4 5 3 3-3 3m6 0h4",
  file: "M6 3h8l4 4v14H6zm8 0v5h4M9 12h6m-6 4h6",
  search: "M16 10a6 6 0 1 0-12 0 6 6 0 0 0 12 0Zm-2 4 6 6",
  play: "m8 5 11 7-11 7Z",
  clock: "M21 12a9 9 0 1 0-18 0 9 9 0 0 0 18 0ZM12 7v5l3 2",
  server: "M4 4h16v16H4zm0 8h16M8 8h.01M8 16h.01",
  eye: "M3 12s3-6 9-6 9 6 9 6-3 6-9 6-9-6-9-6Zm12 0a3 3 0 1 0-6 0 3 3 0 0 0 6 0Z",
  table: "M4 5h16v14H4zm0 5h16m-16 4h16M10 5v14",
  list: "m3 6 1 1 2-2m-3 7 1 1 2-2m-3 7 1 1 2-2M9 6h12M9 12h12M9 18h12",
};

export function icon(name, size = 16) {
  const span = h("span", { class: "icon", "aria-hidden": "true" });
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  for (const [key, value] of Object.entries({ viewBox: "0 0 24 24", width: size, height: size, fill: "none",
    stroke: "currentColor", "stroke-width": "1.7", "stroke-linecap": "round", "stroke-linejoin": "round" })) {
    svg.setAttribute(key, value);
  }
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICONS[name] ?? ICONS.bolt);
  svg.append(path);
  span.append(svg);
  return span;
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
