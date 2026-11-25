// A small menu that opens above the thing that was tapped, as a card of glass that grows out of its corner and closes on
// a tap anywhere else. Only transform and opacity move, so it runs at the screen's own rate.

import { h } from "./dom.js";

let open = null;

export function closePopover() {
  open?.close();
}

/** items: [{icon: Node, label, detail?, onSelect}]; `anchor` is the button the menu stands above. */
export function openPopover(anchor, items, { below = false, content = null, wide = false } = {}) {
  closePopover();
  const card = h("div", { class: `popover${below ? " below" : ""}${content ? " custom" : ""}${wide ? " wide" : ""}`, role: content ? "dialog" : "menu" }, content ?? items.map((item) =>
    item.divider ? h("hr", { class: "pop-rule" }) : h("button", { class: `pop-item${item.on ? " on" : ""}`, type: "button", role: "menuitem", onclick: () => {
      close();
      item.onSelect();
    } }, h("span", { class: "pop-icon" }, item.icon), h("span", { class: "pop-label" }, item.label),
    item.detail ? h("span", { class: "pop-detail" }, item.detail) : null,
    item.on ? h("span", { class: "pop-check" }, item.check) : null)));
  const scrim = h("div", { class: "pop-scrim", onclick: () => close() });
  const box = anchor.getBoundingClientRect();
  if (below) {
    // Under the title, centred on it, never wider than the screen.
    card.style.top = `${box.bottom + 8}px`;
    card.style.left = "12px";
  } else {
    // Above the button, as far left as the screen lets it stand.
    card.style.left = `${Math.min(Math.max(12, box.left), window.innerWidth - (wide ? 372 : content ? 316 : 12))}px`;
    card.style.bottom = `${window.innerHeight - box.top + 10}px`;
  }
  document.getElementById("layer").append(scrim, card);
  // Centred on the title by its real width (a long project name widens the card), never past the screen's edges.
  if (below) card.style.left = `${Math.min(Math.max(12, box.left + box.width / 2 - card.offsetWidth / 2), window.innerWidth - 12 - card.offsetWidth)}px`;
  requestAnimationFrame(() => card.classList.add("in"));
  function close() {
    card.classList.remove("in");
    setTimeout(() => {
      card.remove();
      scrim.remove();
    }, 160);
    open = null;
  }
  open = { close };
  return close;
}
