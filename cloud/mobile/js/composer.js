// The box at the bottom: it grows with the text up to a few lines, then scrolls inside. One round button on its right
// is send when there is text and stop while the agent works and the box is empty: the same place, the same size.
//
// It is docked in the layout (not floating over the conversation), so the last message is never hidden behind it.

import { h, icon } from "./dom.js";
import { t } from "./i18n.js";
import * as native from "./native.js";

const MAX_ROWS_PX = 160;

export function createComposer({ onSend, onStop }) {
  const field = h("textarea", {
    class: "composer-input", rows: "1", enterkeyhint: "send", autocapitalize: "sentences",
    "aria-label": t("message"), placeholder: t("composer placeholder"),
  });
  const action = h("button", { class: "round", type: "button" });
  const box = h("div", { class: "composer" }, field, action);
  let mode = "send";
  let busy = false;
  let locked = false;

  const grow = () => {
    field.style.height = "auto";
    field.style.height = `${Math.min(field.scrollHeight, MAX_ROWS_PX)}px`;
  };

  const paint = () => {
    const hasText = field.value.trim().length > 0;
    mode = busy && !hasText ? "stop" : "send";
    action.replaceChildren(icon(mode === "stop" ? "stop" : "arrow-up", mode === "stop" ? 18 : 20));
    action.setAttribute("aria-label", mode === "stop" ? t("pause") : t("send"));
    action.classList.toggle("stop", mode === "stop");
    action.disabled = locked || (mode === "send" && !hasText);
  };

  field.addEventListener("input", () => {
    grow();
    paint();
  });
  action.addEventListener("click", () => {
    if (mode === "stop") {
      native.haptic("tick");
      onStop();
      return;
    }
    const text = field.value.trim();
    if (!text) return;
    native.haptic("tick");
    onSend(text);
  });
  paint();

  return {
    node: box,
    /** The agent works (the round button becomes "pause" while the box is empty). */
    setBusy(value) {
      busy = value;
      field.placeholder = value ? t("composer busy") : t("composer placeholder");
      paint();
    },
    setLocked(value) {
      locked = value;
      field.disabled = value;
      paint();
    },
    clear() {
      field.value = "";
      grow();
      paint();
    },
    set(text) {
      field.value = text;
      grow();
      paint();
      field.focus();
    },
    focus: () => field.focus(),
    get value() {
      return field.value;
    },
  };
}
