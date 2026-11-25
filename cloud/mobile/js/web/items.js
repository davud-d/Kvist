// Vendored from cloud/workspace/js/views/items.js (the web workspace): only the import paths differ. Keep in step by hand.
// One element per feed item: what the person said, what the agent said and
// thought, and a card for each call it made.

import { fill, h, icon, keyedList, spinner } from "./dom.js";
import { workSummary } from "./feed.js";
import { FOLD_MS, OUT_CUBIC_CSS } from "./folds.js";
import { familyOf, highlightLine } from "./highlight.js";
import { counted, t } from "./i18n.js";
import { renderMarkdown } from "./markdown.js";
import { animate, enter, finished, playing } from "./motion.js";

// How long a finished write stays open before it folds.
const FOLD_AFTER_MS = 2000;

const CALL_ICONS = {
  read_file: "file", read_multiple_files: "file", list_dir: "folder", list_tree: "folder",
  search_code: "search", find_files: "search", git_status: "term", git_diff: "term",
  run_shell: "term", job_output: "term", job_stop: "term",
  write_file: "bolt", append_file: "bolt", replace_in_file: "bolt",
  web_search: "globe", fetch_url: "globe", delegate_task: "bot", helper: "bot",
};

export function makeItem(item) {
  const el = h("div", { class: `item item-${item.kind}` });
  if (item.kind === "call" || item.kind === "thought") {
    el.addEventListener("click", (event) => {
      const head = event.target.closest(".call-head, .thought-head");
      if (!head || el.classList.contains("live")) return;
      unfold(el, item.kind === "call" ? ".call-detail" : ".thought-window");
    });
  }
  paintItem(el, item);
  return el;
}

// A call or a thought opens and folds as Glass does it: 320 ms, easing out; a second click mid-way turns back
// from where it stood.
function unfold(el, selector) {
  const part = el.querySelector(`:scope > ${selector}`);
  const from = part?.isConnected ? part.getBoundingClientRect().height : 0;
  part?.getAnimations().forEach((a) => a.cancel());
  const opening = !el.classList.contains("open");
  el.classList.toggle("open", opening);
  if (!part) return;
  el.classList.toggle("closing", !opening);
  const to = opening ? part.scrollHeight : 0;
  const animation = animate(part, [{ height: `${from}px`, overflow: "hidden" }, { height: `${to}px`, overflow: "hidden" }], { ms: FOLD_MS, curve: OUT_CUBIC_CSS, fill: "none" });
  const done = () => el.classList.remove("closing");
  if (!animation) return done();
  animation.finished.then(done, () => {});
  return undefined;
}

export function paintItem(el, item) {
  const paint = PAINT[item.kind] ?? paintNote;
  paint(el, item);
}

function markdown(text, live) {
  const body = h("div", { class: "md", html: renderMarkdown(text) });
  // No caret: the orb (views/orb.js) stands at the end of words being written.
  return body;
}

function paintUser(el, item) {
  const pictures = item.images?.length ? h("div", { class: "user-images" }, item.images.map((i) => (i.data_url ? h("img", { class: "user-image", src: i.data_url, alt: i.name }) : h("span", { class: "chip" }, i.name)))) : null;
  fill(el, pictures, item.text ? h("div", { class: "user-text" }, item.text) : null);
}

function paintReply(el, item) {
  el.classList.toggle("live", item.live === true);
  el.classList.toggle("aside", item.aside === true);
  fill(el, markdown(item.text, item.live));
}

function paintFinal(el, item) {
  const chip = item.paused ? t("paused") : item.outcome === "incomplete" ? "incomplete" : "";
  fill(el, markdown(item.text, false), chip ? h("span", { class: "chip" }, chip) : null);
}

// -- a thought, as Kvist Glass shows it -------------------------------------------------------------------------
// While the model thinks: "Thinking" shimmering with a clock, and under it a window of its latest lines, the top
// ones fading. A provider that sends its thinking in bursts is drawn catching up, never slower than MIN_RATE.
// When it ends, the window folds into one line, "Thought for 7 s"; a click on that line opens the whole text.

const CATCH_UP_SECONDS = 0.12;
const MIN_RATE = 90;
const INSTANT_CHARS = 6;

// Empty paragraphs say nothing; the window keeps only lines with words.
const thoughtText = (text) => String(text ?? "").split("\n").map((line) => line.trim()).filter(Boolean).join("\n");

function buildThought(el) {
  const label = h("span", { class: "thought-label" });
  const clock = h("span", { class: "thought-clock" });
  const head = h("div", { class: "thought-head" }, label, clock, icon("chev", 12));
  const view = h("div", { class: "thought-window" });
  el.append(head, view);
  el.thought = { label, clock, view, text: "", shown: 0, raf: 0, timer: 0, started: 0 };
}

function paintThought(el, item) {
  if (!el.thought) buildThought(el);
  const state = el.thought;
  state.text = thoughtText(item.text);
  state.shown = Math.min(state.shown, state.text.length);
  if (item.live) {
    el.classList.add("live");
    state.started ||= Date.now();
    state.label.className = "thought-label shimmer";
    state.label.textContent = t("thinking");
    runClock(el);
    catchUp(el);
    return;
  }
  const wasLive = el.classList.contains("live");
  el.classList.remove("live");
  clearInterval(state.timer);
  cancelAnimationFrame(state.raf);
  state.raf = 0;
  state.shown = state.text.length;
  state.view.textContent = state.text;
  state.label.className = "thought-label";
  state.label.textContent = `${t("thought for")} ${Math.max(1, Math.round(item.seconds || 0))} ${t("sec")}`;
  state.clock.textContent = "";
  if (wasLive && !el.classList.contains("open")) foldThought(el);
}

function runClock(el) {
  const state = el.thought;
  const tick = () => {
    if (!el.isConnected && state.timer) return clearInterval(state.timer);
    state.clock.textContent = `${Math.floor((Date.now() - state.started) / 1000)} ${t("sec")}`;
  };
  tick();
  if (!state.timer) state.timer = setInterval(tick, 1000);
}

function catchUp(el) {
  const state = el.thought;
  if (!playing()) {
    state.shown = state.text.length;
    state.view.textContent = state.text;
    return;
  }
  if (state.raf) return;
  let last = performance.now();
  const step = (now) => {
    const seconds = (now - last) / 1000;
    last = now;
    const backlog = state.text.length - state.shown;
    state.shown = backlog <= INSTANT_CHARS ? state.text.length : Math.min(state.text.length, state.shown + Math.max(backlog / CATCH_UP_SECONDS, MIN_RATE) * seconds);
    state.view.textContent = state.text.slice(0, Math.floor(state.shown));
    state.raf = state.shown < state.text.length && el.isConnected ? requestAnimationFrame(step) : 0;
  };
  state.raf = requestAnimationFrame(step);
}

// The window closes over its height while its lines fade, the way Glass folds it.
function foldThought(el) {
  const view = el.thought.view;
  el.classList.add("folding");
  const done = () => el.classList.remove("folding");
  const animation = animate(view, [{ height: `${view.offsetHeight}px`, opacity: 1 }, { height: "0px", opacity: 0 }], { ms: FOLD_MS, curve: OUT_CUBIC_CSS, fill: "none" });
  if (!animation) return done();
  animation.finished.then(done, done);
  return undefined;
}

// -- a block of work: the calls between two things the person reads ------------------------------------------
// Folding follows Kvist Glass (js/folds.js): open while it runs, then two seconds after something follows it the
// group folds into its head line; a lone call has no head, as in Glass. The body's height follows the openness
// frame by frame, the way Glass shows a share of the rows, and the arrow turns with it.

function workLabel(summary) {
  const parts = [];
  if (summary.actions) parts.push(counted(summary.actions, ["действие", "действия", "действий"], ["action", "actions"]));
  if (summary.files.length) parts.push(`${counted(summary.files.length, ["файл изменён", "файла изменено", "файлов изменено"], ["file changed", "files changed"])}`);
  if (summary.failed) parts.push(counted(summary.failed, ["ошибка", "ошибки", "ошибок"], ["error", "errors"]));
  return parts.join(" · ");
}

// The blocks on screen that may move by themselves, and one frame loop for all of them while any does.
const moving = new Set();
let ticking = 0;

function tick() {
  const now = performance.now();
  let more = false;
  for (const el of moving) {
    if (!el.isConnected) {
      moving.delete(el);
      continue;
    }
    applyFold(el, now);
    more ||= el.folds.moving(now);
  }
  ticking = more ? requestAnimationFrame(tick) : 0;
}

function keepTicking(el) {
  moving.add(el);
  if (!ticking) ticking = requestAnimationFrame(tick);
}

function applyFold(el, now) {
  const { body, chev } = el.parts;
  if (!el.grouped) {
    body.hidden = false;
    body.style.height = "";
    return;
  }
  const open = playing() ? el.folds.openness(el.blockId, now) : Math.round(el.folds.openness(el.blockId, now + 1e9));
  el.classList.toggle("open", open > 0.5);
  el.parts.head.setAttribute("aria-expanded", String(open > 0.5));
  chev.style.transform = `rotate(${90 * open}deg)`;
  if (open >= 1) {
    body.hidden = false;
    body.style.height = "";
    body.style.overflow = "";
    return;
  }
  if (open <= 0) {
    body.hidden = true;
    return;
  }
  body.hidden = false;
  body.style.overflow = "hidden";
  body.style.height = "";
  const full = body.scrollHeight;
  body.style.height = `${full * open}px`;
}

/** After the groups were settled: the ones whose hold or fold runs now get the frame loop. */
export function wakeWork(container, now) {
  for (const el of container.querySelectorAll(":scope > .item-work")) if (el.folds.moving(now)) keepTicking(el);
}

export function makeWork(block, folds) {
  const chev = icon("chev", 14);
  const title = h("span", { class: "work-title" });
  const sum = h("span", { class: "work-summary" });
  const spin = spinner();
  const head = h("button", { class: "work-head", type: "button" }, chev, title, sum, spin);
  const body = h("div", { class: "work-body" });
  const el = h("div", { class: "item item-work" }, head, body);
  el.parts = { head, chev, title, sum, spin, body };
  el.folds = folds;
  el.blockId = block.id;
  // A call that joins a running block slides in; the ones there when the block was made are simply there.
  el.drawInner = keyedList(body, { key: (item) => item.id, make: makeItem, update: paintItem, onEnter: (row) => el.built && enter(row, { from: "translateY(4px)", ms: 200 }) });
  head.addEventListener("click", () => {
    folds.toggle(block.id, performance.now());
    keepTicking(el);
  });
  paintWork(el, block);
  el.built = true;
  return el;
}

export function paintWork(el, block) {
  const summary = workSummary(block.items);
  const { title, sum, spin, head } = el.parts;
  el.grouped = summary.actions > 1;
  el.classList.toggle("single", !el.grouped);
  el.classList.toggle("live", summary.running);
  head.hidden = !el.grouped;
  // Only the words change; the spinner stays the same element, so its turn never restarts.
  title.textContent = t(summary.running ? "working now" : "work done");
  sum.textContent = workLabel(summary);
  spin.hidden = !summary.running;
  el.drawInner(block.items);
  applyFold(el, performance.now());
  if (el.folds.moving(performance.now())) keepTicking(el);
}

/**
 * What a call did to its file, for the side sheet: the patch when the core sent one, otherwise the text the call
 * carried (a new file whole, a replacement as what went out and what came in). Null when there is nothing.
 */
export function fileBody(call) {
  if (call.patch) return diffLines(call.patch, call.args?.path);
  const family = familyOf(call.args?.path);
  const rows = [];
  const side = (text, kind, sign) => {
    for (const line of String(text ?? "").replace(/\n$/, "").split("\n")) {
      const row = h("div", { class: `diff-line ${kind}` }, h("span", { class: "sign" }, sign));
      row.insertAdjacentHTML("beforeend", highlightLine(line, family));
      rows.push(row);
    }
  };
  if (call.args?.old_text || call.args?.new_text) {
    side(call.args.old_text, "del", "−");
    side(call.args.new_text, "add", "+");
  } else if (typeof call.args?.content === "string") side(call.args.content, "ctx", "");
  return rows.length ? h("div", { class: "diff" }, rows) : null;
}

export function diffLines(patch, path) {
  const family = familyOf(path);
  const rows = [];
  for (const line of patch.split("\n")) {
    if (line.startsWith("---") || line.startsWith("+++") || line === "") continue;
    const kind = line.startsWith("@@") ? "hunk" : line[0] === "+" ? "add" : line[0] === "-" ? "del" : "ctx";
    if (kind === "hunk") {
      rows.push(h("div", { class: "diff-line hunk" }, line));
      continue;
    }
    const row = h("div", { class: `diff-line ${kind}` }, h("span", { class: "sign" }, line[0] === " " ? "" : line[0] === "-" ? "−" : "+"));
    row.insertAdjacentHTML("beforeend", highlightLine(line.slice(1), family));
    rows.push(row);
  }
  return h("div", { class: "diff" }, rows);
}

// The text a call is typing: what goes out, then what comes in, coloured as the file's language.
function codeRows(item) {
  const rows = [];
  const family = familyOf(item.args?.path);
  const side = (text, kind, sign) => {
    if (!text) return;
    for (const line of text.replace(/\n$/, "").split("\n")) {
      const row = h("div", { class: `code-line ${kind}` }, h("span", { class: "sign" }, sign));
      row.insertAdjacentHTML("beforeend", highlightLine(line, family));
      rows.push(row);
    }
  };
  const replacing = item.actionType === "replace_in_file";
  side(item.stream.old_text, "del", "−");
  side(replacing ? item.stream.new_text : item.stream.content, replacing ? "add" : "", replacing ? "+" : "");
  return rows;
}

// The block a write is typed into: a window of fixed height that follows the end of the code until the person
// scrolls up; the top edge fades while more lies above. The card's head already names the file.
function codeBox(item) {
  const body = h("div", { class: "code-body" }, codeRows(item));
  body.addEventListener("scroll", () => {
    item.pinned = body.scrollHeight - body.scrollTop - body.clientHeight < 8;
    item.scrollTop = body.scrollTop;
    body.classList.toggle("has-above", body.scrollTop > 0);
  });
  const box = h("div", { class: "codebox" }, body);
  // The caller runs this once the box is in the page, where its height is known.
  box.follow = () => {
    body.scrollTop = item.pinned !== false ? body.scrollHeight : (item.scrollTop ?? 0);
    body.classList.toggle("has-above", body.scrollTop > 0);
  };
  return box;
}

function callDetail(item) {
  if (item.patch) return diffLines(item.patch, item.args?.path);
  if (item.output) return h("pre", { class: "call-output" }, item.output);
  const args = Object.keys(item.args ?? {}).length ? JSON.stringify(item.args, null, 2) : "";
  return args ? h("pre", { class: "call-output dim" }, args) : null;
}

function paintCall(el, item) {
  el.dataset.phase = item.phase;
  const counts = item.added || item.removed ? h("span", { class: "counts" }, h("b", { class: "plus" }, `+${item.added ?? 0}`), " ", h("b", { class: "minus" }, `−${item.removed ?? 0}`)) : null;
  const state = item.phase === "run" ? spinner() : h("span", { class: `mark ${item.phase}` }, icon(item.phase === "ok" ? "check" : "close", 16));
  const head = h("div", { class: "call-head" }, icon(CALL_ICONS[item.actionType] ?? "bolt", 16), h("span", { class: "call-label" }, item.label), counts, h("span", { class: "call-summary" }, item.summary), state);
  const detail = callDetail(item);
  const tail = item.phase === "run" && item.output ? h("pre", { class: "call-tail" }, item.output.split("\n").slice(-3).join("\n")) : null;
  const typing = item.stream && !item.folded;
  const box = typing ? codeBox(item) : null;
  fill(el, head, tail, box, detail ? h("div", { class: "call-detail" }, detail) : null);
  if (box) {
    box.follow();
    // The block unrolls downwards the first time it appears; later paints only add lines.
    if (!item.unrolled) {
      item.unrolled = true;
      animate(box, [{ clipPath: "inset(0 0 100% 0)", opacity: 0.4 }, { clipPath: "inset(0 0 0 0)", opacity: 1 }], { curve: "drawer", ms: 320 });
    }
    followStream(el, item, box);
  }
}

// Two seconds after the call's result the block folds away.
function followStream(el, item, box) {
  if (item.phase === "run" || el.foldTimer) return;
  el.foldTimer = setTimeout(() => fold(el, item, box), FOLD_AFTER_MS);
}

async function fold(el, item, box) {
  item.folded = true;
  if (box.isConnected) {
    box.style.overflow = "hidden";
    await finished(animate(box, [{ height: `${box.offsetHeight}px`, opacity: 1 }, { height: "0px", opacity: 0, marginBottom: "0px" }], { curve: "drawer", ms: 360, fill: "forwards" }));
  }
  box.remove();
}

function paintPlan(el, item) {
  fill(el, h("div", { class: "card-title" }, t("task plan")), markdown(item.text, false));
}

function paintError(el, item) {
  fill(el, h("span", { class: "mark fail" }, icon("close", 16)), h("span", {}, item.text));
}

function paintNote(el, item) {
  el.className = `item item-note ${item.tone ?? "muted"}`;
  fill(el, h("span", {}, item.text));
}

function paintArtifact(el, item) {
  const picture = item.preview ? h("img", { class: "artifact-image", src: item.preview, alt: item.caption || item.name }) : null;
  fill(el, picture, h("div", { class: "artifact-caption" }, item.caption || item.name));
}

const PAINT = {
  user: paintUser,
  reply: paintReply,
  final: paintFinal,
  thought: paintThought,
  call: paintCall,
  plan: paintPlan,
  error: paintError,
  note: paintNote,
  artifact: paintArtifact,
};
