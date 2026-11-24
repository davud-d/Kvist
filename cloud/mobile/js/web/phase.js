// From cloud/workspace/js/dom.js: puts a looping CSS animation on the page's one clock, so an element made again by a
// repaint picks up where the one it replaced stood, instead of starting its turn over (which reads as a flicker).
export function inPhase(el, periodMs) {
  el.style.animationDelay = `-${Math.round(performance.now() % periodMs)}ms`;
  return el;
}
