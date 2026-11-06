// Tiny DOM helpers: element builder, pixel icons, a keyed list.

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

// 8×8 bitmaps; "#" is a lit pixel. They are drawn as SVG rects so they stay
// sharp at any zoom.
const BITMAPS = {
  gear: ["#..##..#", ".######.", ".##..##.", "###..###", "###..###", ".##..##.", ".######.", "#..##..#"],
  plus: ["...##...", "...##...", "...##...", "########", "########", "...##...", "...##...", "...##..."],
  grid: ["###.###.", "###.###.", "###.###.", "........", "###.###.", "###.###.", "###.###.", "........"],
  panel: ["########", "#......#", "#....###", "#....###", "#....###", "#....###", "#......#", "########"],
  close: ["##....##", "###..###", ".######.", "..####..", "..####..", ".######.", "###..###", "##....##"],
  min: ["........", "........", "........", "........", "........", "########", "########", "........"],
  max: ["########", "#......#", "#......#", "#......#", "#......#", "#......#", "#......#", "########"],
  send: ["....#...", "....##..", "#######.", "########", "#######.", "....##..", "....#...", "........"],
  stop: ["........", ".######.", ".######.", ".######.", ".######.", ".######.", ".######.", "........"],
  pause: [".##..##.", ".##..##.", ".##..##.", ".##..##.", ".##..##.", ".##..##.", ".##..##.", ".##..##."],
  bot: ["...##...", ".######.", "########", "#.####.#", "########", ".######.", ".#....#.", "##....##"],
  globe: ["..####..", ".#.##.#.", "#.####.#", "########", "########", "#.####.#", ".#.##.#.", "..####.."],
  check: ["........", ".......#", "......##", "#....##.", "##..##..", ".####...", "..##....", "........"],
  folder: ["###.....", "########", "#......#", "#......#", "#......#", "#......#", "#......#", "########"],
  bolt: ["....##..", "...##...", "..##....", ".######.", "..####..", "...##...", "..##....", ".##....."],
  chev: ["##......", ".##.....", "..##....", "...##...", "..##....", ".##.....", "##......", "........"],
  ctx: ["########", "#......#", "#.####.#", "#.####.#", "#.####.#", "#.####.#", "#......#", "########"],
  term: ["########", "#......#", "#.#....#", "#..#...#", "#.#..#.#", "#......#", "#......#", "########"],
  file: ["#####...", "#...##..", "#...####", "#......#", "#......#", "#......#", "#......#", "########"],
  search: [".####...", "#....#..", "#....#..", "#....#..", ".####...", "....##..", ".....##.", "......##"],
  play: ["##......", "####....", "######..", "########", "########", "######..", "####....", "##......"],
  clock: ["..####..", ".#....#.", "#..#...#", "#..#...#", "#..###.#", "#......#", ".#....#.", "..####.."],
  server: ["########", "#......#", "#.#..#.#", "########", "########", "#......#", "#.#..#.#", "########"],
  eye: ["........", "..####..", ".######.", "###..###", "###..###", ".######.", "..####..", "........"],
};

export function icon(name, size = 16) {
  const rows = BITMAPS[name] ?? BITMAPS.bolt;
  let rects = "";
  rows.forEach((row, y) => {
    for (let x = 0; x < 8; x++) if (row[x] === "#") rects += `<rect x="${x}" y="${y}" width="1" height="1"/>`;
  });
  const span = h("span", { class: "icon", style: { width: `${size}px`, height: `${size}px` } });
  span.innerHTML = `<svg viewBox="0 0 8 8" width="${size}" height="${size}" shape-rendering="crispEdges" fill="currentColor" aria-hidden="true">${rects}</svg>`;
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
