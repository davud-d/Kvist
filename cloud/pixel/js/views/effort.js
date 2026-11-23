// How hard the model thinks, as a dial: the level in words, the model it applies to, and a track with a stop per
// level. Dragging shows each level as it passes; the choice is saved once, when the finger or mouse lets go
// (every save is a request and a toast). The keyboard moves it one stop at a time.

import { EFFORT_LEVELS, effortNow, setEffort } from "../actions.js";
import { h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { animate } from "../motion.js";
import { pickedLabel } from "./model-picker.js";

const DEFAULT = "medium";
const SAVE_AFTER_MS = 400;
const LAST = EFFORT_LEVELS.length - 1;

export const effortWord = (level) => t(`effort ${level}`);

/**
 * The dial. `onModel`, when given, makes the model line a button (the home field opens its model menu with it);
 * without it the line only says which model the effort applies to.
 */
export function effortDial({ onModel } = {}) {
  let index = Math.max(0, EFFORT_LEVELS.indexOf(effortNow()));
  const word = h("div", { class: "effort-word", "aria-live": "polite" });
  const reset = h("button", { class: "effort-reset", type: "button", title: t("effort reset"), "aria-label": t("effort reset"), onclick: () => choose(EFFORT_LEVELS.indexOf(DEFAULT)) }, icon("reset", 16));
  const modelText = h("span", {}, pickedLabel());
  const model = onModel
    ? h("button", { class: "effort-model", type: "button", onclick: onModel }, modelText, icon("chev", 14))
    : h("div", { class: "effort-model" }, modelText);
  const fillBar = h("div", { class: "effort-fill" });
  const thumb = h("div", { class: "effort-thumb" });
  const stops = EFFORT_LEVELS.map((_, i) => h("i", { class: "effort-stop", style: `left:${stopAt(i)}` }));
  const track = h("div", { class: "effort-track", role: "slider", tabindex: "0", "aria-label": t("effort"), "aria-valuemin": "0", "aria-valuemax": String(LAST) }, fillBar, ...stops, thumb);

  function show(i, glide = true) {
    // Where the thumb is right now, mid-glide included, so a second step carries on from there.
    const from = { left: parseFloat(getComputedStyle(thumb).left), width: parseFloat(getComputedStyle(fillBar).width) };
    index = i;
    word.textContent = effortWord(EFFORT_LEVELS[i]);
    fillBar.style.width = stopAt(i);
    thumb.style.left = stopAt(i);
    stops.forEach((stop, n) => stop.classList.toggle("passed", n < i));
    track.setAttribute("aria-valuenow", String(i));
    track.setAttribute("aria-valuetext", effortWord(EFFORT_LEVELS[i]));
    reset.disabled = EFFORT_LEVELS[i] === DEFAULT;
    if (glide && track.isConnected) travel(from);
  }

  // Every step travels to its stop, a drag's included, quickly enough to keep up with the hand.
  function settle() {
    thumb.getAnimations().forEach((a) => a.cancel());
    fillBar.getAnimations().forEach((a) => a.cancel());
  }
  function travel(from) {
    settle();
    const to = { left: thumb.offsetLeft, width: fillBar.offsetWidth };
    if (to.left === from.left) return;
    animate(thumb, [{ left: `${from.left}px` }, { left: `${to.left}px` }], { ms: GLIDE_MS });
    animate(fillBar, [{ width: `${from.width}px` }, { width: `${to.width}px` }], { ms: GLIDE_MS });
  }

  // Several quick steps save once, the last one: each save is a request and a toast, and comparing with the
  // server's answer mid-flight would drop a step that lands back on the level the request is about to replace.
  let saved = effortNow();
  let timer = 0;
  function save(wait) {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const level = EFFORT_LEVELS[index];
      if (level === saved) return;
      saved = level;
      setEffort(level, { quiet: true });
    }, wait);
  }

  function choose(i) {
    show(i);
    save(SAVE_AFTER_MS);
  }

  // The nearest stop under the pointer.
  const indexAt = (clientX) => {
    const box = track.getBoundingClientRect();
    const share = Math.min(1, Math.max(0, (clientX - box.left - INSET) / (box.width - 2 * INSET)));
    return Math.round(share * LAST);
  };
  track.addEventListener("pointerdown", (event) => {
    track.setPointerCapture(event.pointerId);
    track.classList.add("dragging");
    show(indexAt(event.clientX));
  });
  track.addEventListener("pointermove", (event) => {
    if (!track.hasPointerCapture(event.pointerId)) return;
    const i = indexAt(event.clientX);
    if (i !== index) show(i);
  });
  track.addEventListener("pointerup", () => {
    track.classList.remove("dragging");
    save(0);
  });
  track.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1 }[event.key];
    const to = event.key === "Home" ? 0 : event.key === "End" ? LAST : step === undefined ? null : Math.min(LAST, Math.max(0, index + step));
    if (to === null) return;
    event.preventDefault();
    choose(to);
  });

  show(index, false);
  return h("div", { class: "effort-dial" }, h("div", { class: "effort-head" }, h("span", { class: "effort-gauge" }, icon("gauge", 16)), word, reset), model, track);
}

// The first and last stops sit inside the track's rounded ends, so the thumb never hangs over its edge.
const INSET = 14;
function stopAt(i) {
  return `calc(${INSET}px + (100% - ${2 * INSET}px) * ${i / LAST})`;
}

// Short and ease-out: the dial answers the hand, it does not perform.
const GLIDE_MS = 160;
