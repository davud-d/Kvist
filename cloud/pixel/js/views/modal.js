// A window-level dialog for the interface's own questions (not the core's).

import { h } from "../dom.js";
import { animate, enter, finished } from "../motion.js";

export function openModal({ title, content, onClose }) {
  const trigger = document.activeElement;
  const card = h("div", { class: "dialog px", role: "dialog", "aria-modal": "true", "aria-label": title, tabindex: "-1" }, h("div", { class: "dialog-title" }, title), ...[content].flat());
  const scrim = h("div", { class: "scrim" }, card);
  let closing = false;
  const close = async () => {
    if (closing) return;
    closing = true;
    document.removeEventListener("keydown", onKey, true);
    await finished(animate(card, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(4px)" }], { curve: "in", ms: 140, fill: "forwards" }));
    scrim.remove();
    if (trigger?.isConnected && document.activeElement === document.body) trigger.focus();
    onClose?.();
  };
  const onKey = (event) => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    close();
  };
  document.addEventListener("keydown", onKey, true);
  scrim.addEventListener("pointerdown", (event) => event.target === scrim && close());
  document.getElementById("modals").append(scrim);
  enter(card);
  queueMicrotask(() => (card.querySelector("input, .btn.primary") ?? card).focus());
  return { close, card };
}
