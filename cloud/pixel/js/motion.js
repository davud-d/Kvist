// Workspace transitions use short travel and respect both motion preferences.
const curves = {
  out: "cubic-bezier(.16, 1, .3, 1)",
  in: "cubic-bezier(.7, 0, .84, 0)",
};

let speed = 1;

export function setSpeed(value) {
  speed = Number.isFinite(value) && value >= 0 ? value : 1;
}

export function animate(el, keyframes, { curve = "out", ms = 180, fill = "backwards" } = {}) {
  if (!speed || matchMedia("(prefers-reduced-motion: reduce)").matches) return null;
  return el.animate?.(keyframes, { duration: ms / speed, easing: curves[curve], fill }) ?? null;
}

export function finished(animation) {
  return animation ? animation.finished.catch(() => {}) : Promise.resolve();
}

export function enter(el, { from = "translateY(5px)" } = {}) {
  return animate(el, [{ opacity: 0, transform: from }, { opacity: 1, transform: "none" }]);
}
