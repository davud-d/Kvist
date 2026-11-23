// Motion: named curves, springs baked into CSS linear() easings, and thin
// wrappers over the Web Animations API. Everything goes through `animate`, so
// the person's speed setting (and prefers-reduced-motion) reach every effect.
//
// This is a work tool used all day, so motion is short and never bounces: things that answer the person
// ease out (they start fast, so the page feels quick), things that move on screen ease in and out, exits
// are faster than entrances, and nothing scales up from nothing (0.96 at most). Springs are critically damped.

export const curves = {
  out: "cubic-bezier(0.23, 1, 0.32, 1)",
  inOut: "cubic-bezier(0.77, 0, 0.175, 1)",
  drawer: "cubic-bezier(0.32, 0.72, 0, 1)",
  // Older names, kept so every call reads the same curve family.
  in: "cubic-bezier(0.23, 1, 0.32, 1)",
  emphasized: "cubic-bezier(0.32, 0.72, 0, 1)",
};

export const springs = {
  soft: { stiffness: 300, damping: 34, mass: 1 },
  snappy: { stiffness: 520, damping: 46, mass: 1 },
};

const STEP = 1 / 240;
const SAMPLE_EVERY = 4;
const SETTLED = 0.0008;
const LONGEST = 2.2;

/** Position of a spring pulled from 0 to 1, sampled until it has come to rest. */
export function springSamples({ stiffness, damping, mass }) {
  const samples = [0];
  let x = 0;
  let v = 0;
  let calm = 0;
  for (let i = 1; i * STEP < LONGEST; i++) {
    v += ((-stiffness * (x - 1) - damping * v) / mass) * STEP;
    x += v * STEP;
    if (i % SAMPLE_EVERY === 0) samples.push(x);
    calm = Math.abs(x - 1) < SETTLED && Math.abs(v) < SETTLED ? calm + 1 : 0;
    if (calm > 12) break;
  }
  samples.push(1);
  return { samples, seconds: (samples.length - 1) * SAMPLE_EVERY * STEP };
}

const baked = new Map();

/** A spring as `{easing, ms}`: a linear() easing and the time it needs. */
export function springEasing(spec) {
  const key = `${spec.stiffness}/${spec.damping}/${spec.mass}`;
  const cached = baked.get(key);
  if (cached) return cached;
  const { samples, seconds } = springSamples(spec);
  const points = samples.map((p) => (Math.round(p * 10000) / 10000).toString());
  const result = { easing: `linear(${points.join(", ")})`, ms: Math.round(seconds * 1000) };
  baked.set(key, result);
  return result;
}

export const motion = { speed: 1 };

function reduced() {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function setSpeed(value) {
  motion.speed = Number.isFinite(value) && value >= 0 ? value : 1;
}

export function playing() {
  return motion.speed > 0 && !reduced();
}

/**
 * Plays `keyframes` on `el`. `curve` is a name from `curves`; `spring` (a name
 * from `springs` or a spec) replaces it and sets the duration itself.
 * Returns the Animation, or null when motion is off (the end state is simply
 * the element's own style, so callers never wait on it).
 */
export function animate(el, keyframes, { curve = "out", spring = null, ms = 220, delay = 0, fill = "backwards" } = {}) {
  if (!playing() || !el.animate) return null;
  let easing = curves[curve] ?? curve;
  let duration = ms;
  if (spring) {
    const baked = springEasing(typeof spring === "string" ? springs[spring] : spring);
    easing = baked.easing;
    duration = baked.ms;
  }
  return el.animate(keyframes, { duration: duration / motion.speed, delay: delay / motion.speed, easing, fill });
}

export function finished(animation) {
  return animation ? animation.finished.catch(() => {}) : Promise.resolve();
}

/** A new element arrives: it settles a few pixels into place. */
export function enter(el, { from = "translateY(6px)", delay = 0, ms = 240 } = {}) {
  return animate(el, [{ opacity: 0, transform: from }, { opacity: 1, transform: "none" }], { ms, delay });
}

/** An element goes away, then is removed. */
export async function leave(el, { to = "scale(0.98)" } = {}) {
  const run = animate(el, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: to }], { ms: 150, fill: "forwards" });
  await finished(run);
  el.remove();
}

/** A sheet (dialog, popover) opens from `origin`; its exit is the same movement, shorter. */
export function popIn(el, origin = "50% 50%") {
  el.style.transformOrigin = origin;
  return animate(el, [{ opacity: 0, transform: "scale(0.96) translateY(4px)" }, { opacity: 1, transform: "none" }], { ms: 200 });
}

export function popOut(el) {
  return animate(el, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "scale(0.98)" }], { ms: 140, fill: "forwards" });
}

/**
 * First-Last-Invert-Play for children that change place: call with the
 * container, change the DOM inside `change`, and every child slides from where
 * it was to where it is.
 */
export function flip(container, change, key = (child) => child.dataset.key) {
  const before = new Map();
  for (const child of container.children) before.set(key(child), child.getBoundingClientRect());
  change();
  for (const child of container.children) {
    const was = before.get(key(child));
    if (!was) continue;
    const now = child.getBoundingClientRect();
    const dx = was.left - now.left;
    const dy = was.top - now.top;
    if (!dx && !dy) continue;
    animate(child, [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { spring: "snappy" });
  }
}

/** The transform that makes a box at `from` look like the box at `to`. */
export function boxTransform(from, to) {
  const sx = from.width / to.width;
  const sy = from.height / to.height;
  const dx = from.left - to.left;
  const dy = from.top - to.top;
  return `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`;
}

/** Zooms `el` from the box `from` into its own place (the lens moves in). */
export function zoomIn(el, from) {
  const to = el.getBoundingClientRect();
  return animate(el, [{ transform: boxTransform(from, to), opacity: 0.2, transformOrigin: "0 0" }, { transform: "none", opacity: 1, transformOrigin: "0 0" }], { curve: "drawer", ms: 380 });
}

/** Zooms `el` away from its place into the box `to` (the lens moves out). */
export function zoomOut(el, to) {
  const from = el.getBoundingClientRect();
  return animate(el, [{ transform: "none", opacity: 1, transformOrigin: "0 0" }, { transform: boxTransform(to, from), opacity: 0, transformOrigin: "0 0" }], { curve: "drawer", ms: 320, fill: "forwards" });
}
