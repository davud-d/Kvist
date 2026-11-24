// How hard the model thinks, as the web workspace shows it: the level in words, the model it applies to, and a track with a
// stop per level. Dragging shows each level as it passes; the choice is saved once, when the finger lets go (every save is a
// request). A tap on the track goes to the nearest stop. Ported small from cloud/workspace/js/views/effort.js.

import { account, refresh } from "./account.js";
import { api } from "./api.js";
import { h, icon } from "./dom.js";
import { t } from "./i18n.js";
import { providerMark } from "./mark.js";
import { openPopover } from "./popover.js";
import { toast } from "./sheet.js";

export const LEVELS = ["false", "low", "medium", "high", "xhigh", "max"];
const DEFAULT = "medium";
const LAST = LEVELS.length - 1;
const INSET = 14;

export const effortOf = () => (LEVELS.includes(account.key?.effort) ? account.key.effort : DEFAULT);
export const effortWord = (level) => t(`effort ${level}`);

const stopAt = (i) => `calc(${INSET}px + (100% - ${2 * INSET}px) * ${i / LAST})`;

function dial() {
  let index = LEVELS.indexOf(effortOf());
  let saved = LEVELS[index];
  const word = h("div", { class: "dial-word", "aria-live": "polite" });
  const reset = h("button", { class: "dial-reset", type: "button", aria: { label: t("effort reset") }, onclick: () => choose(LEVELS.indexOf(DEFAULT), true) }, icon("close", 16));
  const fill = h("div", { class: "dial-fill" });
  const thumb = h("div", { class: "dial-thumb" });
  const stops = LEVELS.map((_, i) => h("i", { class: "dial-stop", style: { left: stopAt(i) } }));
  const track = h("div", { class: "dial-track", role: "slider", tabindex: "0", aria: { label: t("effort"), valuemin: 0, valuemax: LAST } }, fill, stops, thumb);

  function show(i) {
    index = i;
    word.textContent = effortWord(LEVELS[i]);
    fill.style.width = stopAt(i);
    thumb.style.left = stopAt(i);
    stops.forEach((stop, n) => stop.classList.toggle("passed", n < i));
    track.setAttribute("aria-valuenow", String(i));
    reset.disabled = LEVELS[i] === DEFAULT;
  }
  async function save() {
    const level = LEVELS[index];
    if (level === saved) return;
    saved = level;
    try {
      await api("/api/effort", { method: "PUT", body: { level } });
      await refresh("key");
    } catch (error) {
      toast(error.localized, "error");
    }
  }
  function choose(i, now) {
    show(i);
    if (now) save();
  }
  // The nearest stop under the finger.
  const indexAt = (x) => {
    const box = track.getBoundingClientRect();
    return Math.round(Math.min(1, Math.max(0, (x - box.left - INSET) / (box.width - 2 * INSET))) * LAST);
  };
  track.addEventListener("pointerdown", (event) => {
    track.setPointerCapture(event.pointerId);
    track.classList.add("dragging");
    show(indexAt(event.clientX));
  });
  track.addEventListener("pointermove", (event) => {
    if (track.hasPointerCapture(event.pointerId) && indexAt(event.clientX) !== index) show(indexAt(event.clientX));
  });
  track.addEventListener("pointerup", () => {
    track.classList.remove("dragging");
    save();
  });
  track.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[event.key];
    if (step === undefined) return;
    event.preventDefault();
    choose(Math.min(LAST, Math.max(0, index + step)), true);
  });
  show(index);

  const key = account.key;
  return h("div", { class: "dial" },
    h("div", { class: "dial-head" }, h("span", { class: "dial-gauge" }, icon("gauge", 18)), word, reset),
    key?.set ? h("div", { class: "dial-model" }, providerMark(key.provider, key.label, 14), h("span", {}, key.model || key.label || key.provider)) : null,
    track);
}

/** The dial above the button that opened it. */
export const openDial = (anchor) => openPopover(anchor, [], { content: dial() });
