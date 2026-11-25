// The first message of a new session leaves the box like a letter: it folds (50 ms), flies to its place at the top of the
// conversation (200 ms) and unfolds there (120 ms) while the start screen's words drift up and away. Nothing here blocks
// the page: the ghosts take no touches, so the person can scroll or tap the whole time, and the real message is already
// in the feed under the ghost, which is taken away only once that message has settled.

import { h } from "./dom.js";
import { animate, finished, playing } from "./web/motion.js";

const FOLD_MS = 50;
const FLY_MS = 200;
const UNFOLD_MS = 120;
const TOTAL_MS = FOLD_MS + FLY_MS + UNFOLD_MS;
const FOLDED = "scale(0.94, 0.62)";
const ARC_PX = 28;
// A stream that never brings the message back must not leave a ghost on the screen for ever.
const WAIT_FOR_MESSAGE_MS = 1500;
const COLUMN_PADDING = 16;
const BUBBLE_SHARE = 0.82;

const place = (x, y) => `translate(${x}px, ${y}px)`;

/** The start screen's words lift away from where they stood. */
export function flyHomeAway(node, rect) {
  if (!playing() || !node) return;
  const ghost = node.cloneNode(true);
  Object.assign(ghost.style, { position: "fixed", left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, margin: "0", pointerEvents: "none", zIndex: "19" });
  document.body.append(ghost);
  const run = animate(ghost, [{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(-28px)" }], { curve: "out", ms: TOTAL_MS, fill: "forwards" });
  finished(run).then(() => ghost.remove());
}

/**
 * `from` is the box the text left (the field's rectangle); `column` is the conversation's column, where the message will
 * stand. Resolves when the ghost is gone and the real message shows.
 */
export async function flyMessage({ text, from, column }) {
  if (!playing() || !text) return;
  const ghost = h("div", { class: "user-text flight" }, text);
  const columnBox = column.getBoundingClientRect();
  ghost.style.maxWidth = `${(columnBox.width - COLUMN_PADDING * 2) * BUBBLE_SHARE}px`;
  document.body.append(ghost);
  const size = ghost.getBoundingClientRect();
  const left = columnBox.right - COLUMN_PADDING - size.width;
  const top = columnBox.top + COLUMN_PADDING;
  Object.assign(ghost.style, { left: `${left}px`, top: `${top}px` });

  // Where it starts: its own box laid over the field, left edges together, centres on one line.
  const startX = from.left - left;
  const startY = from.top + from.height / 2 - (top + size.height / 2);
  const mid = place(startX / 2, startY / 2 - ARC_PX);
  column.classList.add("flying");
  const run = animate(ghost, [
    { transform: `${place(startX, startY)} scale(1)`, offset: 0, easing: "cubic-bezier(0.4, 0, 0.6, 1)" },
    { transform: `${place(startX, startY - 4)} ${FOLDED}`, offset: FOLD_MS / TOTAL_MS, easing: "cubic-bezier(0.45, 0, 0.55, 1)" },
    { transform: `${mid} ${FOLDED} rotate(-3deg)`, offset: (FOLD_MS + FLY_MS / 2) / TOTAL_MS, easing: "cubic-bezier(0.45, 0, 0.55, 1)" },
    { transform: `translate(0px, 0px) ${FOLDED}`, offset: (FOLD_MS + FLY_MS) / TOTAL_MS, easing: "cubic-bezier(0.23, 1, 0.32, 1)" },
    { transform: "none", offset: 1 },
  ], { ms: TOTAL_MS, curve: "linear", fill: "both" });
  await finished(run);
  await landed(column);
  ghost.remove();
  column.classList.remove("flying");
}

/** The real message is in the feed and has finished its own entrance (or the wait ran out). */
function landed(column) {
  const find = () => column.querySelector(".item-user");
  const settle = (el) => Promise.all((el.getAnimations?.() ?? []).map((a) => a.finished.catch(() => {})));
  const present = find();
  if (present) return settle(present);
  return new Promise((resolve) => {
    const done = () => {
      observer.disconnect();
      clearTimeout(timer);
      resolve();
    };
    const observer = new MutationObserver(() => {
      const el = find();
      if (el) settle(el).then(done);
    });
    const timer = setTimeout(done, WAIT_FOR_MESSAGE_MS);
    observer.observe(column, { childList: true });
  });
}
