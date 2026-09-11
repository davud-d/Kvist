/**
 * cursor-glow.js — a soft accent halo that trails the pointer, mirroring the
 * JetBrains GUI. Optional and purely decorative: the element renders invisible
 * until the pointer moves, and it is skipped entirely under reduced motion or
 * on devices without a fine pointer.
 */

export function mountCursorGlow(el, { reducedMotion }) {
  if (!el || reducedMotion()) return;
  if (!window.matchMedia("(pointer: fine)").matches) return;

  const pos = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
  const target = { ...pos };
  let frame = 0;

  const paint = () => {
    el.style.setProperty("--gx", `${pos.x}px`);
    el.style.setProperty("--gy", `${pos.y}px`);
  };

  const step = () => {
    pos.x += (target.x - pos.x) * 0.16;
    pos.y += (target.y - pos.y) * 0.16;
    paint();
    frame =
      Math.abs(target.x - pos.x) > 0.4 || Math.abs(target.y - pos.y) > 0.4
        ? requestAnimationFrame(step)
        : 0;
  };

  paint();
  window.addEventListener(
    "pointermove",
    (event) => {
      target.x = event.clientX;
      target.y = event.clientY;
      el.classList.add("is-on");
      if (!frame) frame = requestAnimationFrame(step);
    },
    { passive: true },
  );
  document.addEventListener("mouseleave", () => el.classList.remove("is-on"));
  window.addEventListener("blur", () => el.classList.remove("is-on"));
}
