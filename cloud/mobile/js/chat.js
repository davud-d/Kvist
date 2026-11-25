// The conversation on screen, drawn as the web workspace draws it (cloud/workspace/js/views/chat.js): the feed's items
// grouped into blocks (js/web/feed.js), a block per item or per run of calls, each made once and repainted only when it
// changed (js/web/items.js); runs of calls fold as Kvist Glass folds them (js/web/folds.js).
//
// Reading while it streams: the view follows the end only while the person is within a thumb of it. Scroll up and
// it stays put; a small "latest" button appears instead of the page jumping.

import { h } from "./dom.js";
import * as native from "./native.js";
import { keyedList } from "./web/dom.js";
import { feedBlocks } from "./web/feed.js";
import { Folds } from "./web/folds.js";
import { makeItem, makeWork, paintItem, paintWork, wakeWork } from "./web/items.js";
import { enter } from "./web/motion.js";

const PIN_DISTANCE = 100;
const STREAM_PAINT_MS = 50;

export function createChat(root, jump, tail, afterPaint = () => {}) {
  const column = h("div", { class: "col" });
  column.append(tail);
  root.append(column);
  let feedRef = null;
  let draw = null;
  let folds = null;
  let settledOnce = false;
  let scheduled = 0;

  const distance = () => root.scrollHeight - root.scrollTop - root.clientHeight;
  const pinned = () => distance() < PIN_DISTANCE;
  const toEnd = (smooth = false) => root.scrollTo({ top: root.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  const syncJump = () => jump.classList.toggle("hidden", pinned());
  root.addEventListener("scroll", syncJump, { passive: true });
  jump.addEventListener("click", () => toEnd(true));

  // Links in the agent's words open in the system browser, not in the app's one window.
  column.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-external]");
    if (!link) return;
    event.preventDefault();
    native.open(link.href);
  });

  function bind() {
    folds = new Folds();
    settledOnce = false;
    const isWork = (block) => block.kind === "work";
    draw = keyedList(column, {
      key: (block) => block.id,
      make: (block) => (isWork(block) ? makeWork(block, folds) : makeItem(block)),
      update: (el, block) => (isWork(block) ? paintWork(el, block) : paintItem(el, block)),
      onEnter: (el) => feedRef?.items.length < 400 && enter(el),
    });
  }

  function paint() {
    if (!feedRef) return;
    if (!draw) bind();
    const wasPinned = pinned();
    const blocks = feedBlocks(feedRef.items);
    const now = performance.now();
    // What was already done when the session opened is folded at once, not opened and folded again.
    const busy = ["queued", "starting", "running"].includes(feedRef.task?.status);
    folds.settle(blocks, busy, now, { quiet: !settledOnce });
    settledOnce ||= blocks.length > 0;
    draw(blocks);
    wakeWork(column, now);
    afterPaint();
    if (wasPinned) requestAnimationFrame(() => toEnd());
    syncJump();
  }

  // The streaming reply repaints at most every STREAM_PAINT_MS: faster than that is not seen, only paid for.
  const schedule = () => {
    if (scheduled) return;
    scheduled = setTimeout(() => {
      scheduled = 0;
      paint();
    }, STREAM_PAINT_MS);
  };

  return {
    render(feed) {
      feedRef = feed;
      schedule();
    },
    renderNow(feed) {
      feedRef = feed;
      paint();
    },
    reset() {
      feedRef = null;
      draw = null;
      column.replaceChildren(tail);
    },
    toEnd,
    column,
  };
}
