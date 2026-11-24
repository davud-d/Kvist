// Bottom sheets and toasts. A sheet slides up over the page, closes by its button, the dimmed area, a pull down on
// its top edge, Escape, or the phone's back key (the app calls window.kvistBack()). Sheets stack: the top one closes first.

import { h, icon } from "./dom.js";
import { t } from "./i18n.js";

const stack = [];
const DRAG_CLOSE_PX = 90;

const layer = () => document.getElementById("layer");

/**
 * openSheet({title, build}) -> {close, body}. `build(close)` returns the body's content (a node or an array).
 * `variant: "full"` makes it tall, for forms with a keyboard.
 */
export function openSheet({ title, build, variant = "", onClose = () => {} }) {
  const scrim = h("div", { class: "scrim", onclick: () => close() });
  const body = h("div", { class: "sheet-body" });
  const grab = h("div", { class: "sheet-grab", aria: { hidden: "true" } }, h("i"));
  const head = h("header", { class: "sheet-head" },
    h("h2", { class: "sheet-title" }, title),
    h("button", { class: "icon-btn", type: "button", aria: { label: t("close") }, onclick: () => close() }, icon("close", 20)));
  const panel = h("section", { class: `sheet ${variant}`, role: "dialog", "aria-modal": "true", "aria-label": title }, grab, head, body);
  const entry = { close, node: panel };

  function close() {
    const at = stack.indexOf(entry);
    if (at < 0) return;
    stack.splice(at, 1);
    stack.at(-1)?.node.classList.remove("under");
    panel.classList.add("leaving");
    scrim.classList.add("leaving");
    setTimeout(() => {
      panel.remove();
      scrim.remove();
    }, 180);
    onClose();
  }

  // A pull down on the grip or the title closes it, the way every phone sheet does.
  let startY = null;
  const drag = (event) => {
    if (startY === null) return;
    panel.style.transform = `translateY(${Math.max(0, event.touches[0].clientY - startY)}px)`;
  };
  const release = (event) => {
    if (startY === null) return;
    const moved = event.changedTouches[0].clientY - startY;
    startY = null;
    panel.style.transition = "";
    panel.style.transform = "";
    if (moved > DRAG_CLOSE_PX) close();
  };
  for (const handle of [grab, head]) {
    handle.addEventListener("touchstart", (event) => {
      startY = event.touches[0].clientY;
      panel.style.transition = "none";
    }, { passive: true });
    handle.addEventListener("touchmove", drag, { passive: true });
    handle.addEventListener("touchend", release);
  }

  // The sheet below steps back while this one is open: three stacked panels peeking out would be noise.
  stack.at(-1)?.node.classList.add("under");
  const content = build(close, body);
  if (content !== undefined) body.append(...[content].flat(Infinity).filter(Boolean));
  layer().append(scrim, panel);
  stack.push(entry);
  requestAnimationFrame(() => {
    panel.classList.add("in");
    scrim.classList.add("in");
  });
  return { close, body };
}

/** Closes the top sheet; true when there was one (the phone's back key uses it). */
export function closeTop() {
  const top = stack.at(-1);
  if (!top) return false;
  top.close();
  return true;
}

export const sheetOpen = () => stack.length > 0;

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeTop();
});

let toastTimer = 0;
/** A short line near the bottom: `kind` "error" stays longer. */
export function toast(text, kind = "") {
  if (!text) return;
  const host = document.getElementById("toasts");
  host.replaceChildren(h("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" }, text));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => host.replaceChildren(), kind === "error" ? 6000 : 3000);
}
