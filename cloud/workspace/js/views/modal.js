// A window-level dialog for the interface's own questions (not the core's).

import { h } from "../dom.js";
import { animate, finished, popIn, popOut } from "../motion.js";

export function openModal({ title, content, onClose }) {
  const card = h("div", { class: "dialog", role: "dialog", "aria-modal": "true", tabindex: "-1" }, h("div", { class: "dialog-title" }, title), ...[content].flat());
  const scrim = h("div", { class: "scrim" }, card);
  let closing = false;
  const close = async () => {
    if (closing) return;
    closing = true;
    document.removeEventListener("keydown", onKey, true);
    animate(scrim, [{ opacity: 1 }, { opacity: 0 }], { ms: 160, fill: "forwards" });
    await finished(popOut(card));
    scrim.remove();
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
  animate(scrim, [{ opacity: 0 }, { opacity: 1 }], { ms: 200 });
  popIn(card);
  queueMicrotask(() => (card.querySelector("input, .btn.primary") ?? card).focus());
  return { close, card };
}
