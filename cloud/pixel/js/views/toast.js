// Short notices that rise from the bottom and leave on their own.

import { animate, enter, finished } from "../motion.js";
import { h } from "../dom.js";

const LIFETIME_MS = 3600;

export function toast(text, tone = "ok") {
  if (!text) return;
  const host = document.getElementById("toasts");
  if (!host) return;
  const el = h("div", { class: `toast ${tone}`, role: "status" }, text);
  host.append(el);
  enter(el, { from: "translateY(12px) scale(0.98)", ms: 320 });
  setTimeout(async () => {
    await finished(animate(el, [{ opacity: 1 }, { opacity: 0, transform: "translateY(4px)" }], { ms: 180, fill: "forwards" }));
    el.remove();
  }, LIFETIME_MS);
}
