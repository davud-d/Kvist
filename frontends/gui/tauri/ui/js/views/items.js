// One element per feed item: what the person said, what the agent said and
// thought, and a card for each call it made.

import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { renderMarkdown } from "../markdown.js";
import { animate, finished } from "../motion.js";

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
      if (event.target.closest(".call-head, .thought-head")) el.classList.toggle("open");
    });
  }
  paintItem(el, item);
  return el;
}

export function paintItem(el, item) {
  const paint = PAINT[item.kind] ?? paintNote;
  paint(el, item);
}

function markdown(text, live) {
  const body = h("div", { class: "md", html: renderMarkdown(text) });
  if (live) body.append(h("span", { class: "caret" }));
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

function paintThought(el, item) {
  const label = item.live ? `${t("thinking")}` : item.seconds ? `${t("thought for")} ${Math.round(item.seconds)}s` : t("thought for");
  el.classList.toggle("live", item.live === true);
  const body = h("div", { class: "thought-body" }, item.text);
  fill(el, h("div", { class: "thought-head" }, icon("chev", 10), h("span", {}, label), item.live ? h("span", { class: "dots" }) : null), body);
  if (item.live) body.scrollTop = body.scrollHeight;
}

function diffLines(patch) {
  const rows = [];
  for (const line of patch.split("\n")) {
    if (line.startsWith("---") || line.startsWith("+++") || line === "") continue;
    const kind = line.startsWith("@@") ? "hunk" : line[0] === "+" ? "add" : line[0] === "-" ? "del" : "ctx";
    rows.push(h("div", { class: `diff-line ${kind}` }, line));
  }
  return h("div", { class: "diff" }, rows);
}

const LANGUAGES = {
  py: "Python", rs: "Rust", ts: "TypeScript", tsx: "TSX", jsx: "JSX", js: "JavaScript", mjs: "JavaScript", json: "JSON",
  md: "Markdown", html: "HTML", css: "CSS", sh: "Shell", bash: "Shell", toml: "TOML", yaml: "YAML", yml: "YAML",
  go: "Go", c: "C", h: "C", cpp: "C++", java: "Java",
};

function languageName(path) {
  const extension = String(path ?? "").split("/").pop().split(".").slice(1).pop() ?? "";
  return LANGUAGES[extension] ?? (extension ? extension.toUpperCase() : "Text");
}

// The text a call is typing: what goes out, then what comes in.
function codeRows(item) {
  const rows = [];
  const side = (text, kind, sign) => {
    if (!text) return;
    for (const line of text.replace(/\n$/, "").split("\n")) rows.push(h("div", { class: `code-line ${kind}` }, h("span", { class: "sign" }, sign), line));
  };
  const replacing = item.actionType === "replace_in_file";
  side(item.stream.old_text, "del", "−");
  side(replacing ? item.stream.new_text : item.stream.content, replacing ? "add" : "", replacing ? "+" : "");
  return rows;
}

// The block a write is typed into: a header with the language, and a window of fixed height that follows the end of
// the code until the person scrolls up; the top edge fades while more lies above.
function codeBox(item) {
  const path = item.args?.path ?? "";
  const counts = item.added || item.removed ? h("span", { class: "counts" }, h("b", { class: "plus" }, `+${item.added ?? 0}`), item.removed ? h("b", { class: "minus" }, ` −${item.removed}`) : null) : null;
  const head = h("div", { class: "code-head" }, h("span", { class: "code-mark" }, "</>"), h("span", { class: "code-lang" }, languageName(path)), h("span", { class: "code-path" }, path), counts);
  const body = h("div", { class: "code-body" }, codeRows(item));
  body.addEventListener("scroll", () => {
    item.pinned = body.scrollHeight - body.scrollTop - body.clientHeight < 8;
    body.classList.toggle("has-above", body.scrollTop > 0);
  });
  const box = h("div", { class: "codebox" }, head, body);
  // Laid out only once it is in the page, so the scroll position is set by the caller.
  box.follow = () => {
    if (item.pinned !== false) body.scrollTop = body.scrollHeight;
    else body.scrollTop = item.scrollTop ?? 0;
    body.classList.toggle("has-above", body.scrollTop > 0);
  };
  body.addEventListener("scroll", () => { item.scrollTop = body.scrollTop; });
  return box;
}

function callDetail(item) {
  if (item.patch) return diffLines(item.patch);
  if (item.output) return h("pre", { class: "call-output" }, item.output);
  const args = Object.keys(item.args ?? {}).length ? JSON.stringify(item.args, null, 2) : "";
  return args ? h("pre", { class: "call-output dim" }, args) : null;
}

function paintCall(el, item) {
  el.dataset.phase = item.phase;
  const counts = item.added || item.removed ? h("span", { class: "counts" }, h("b", { class: "plus" }, `+${item.added ?? 0}`), " ", h("b", { class: "minus" }, `−${item.removed ?? 0}`)) : null;
  const state = item.phase === "run" ? h("span", { class: "spin" }) : h("span", { class: `mark ${item.phase}` }, item.phase === "ok" ? "✓" : "✕");
  const head = h("div", { class: "call-head" }, icon(CALL_ICONS[item.actionType] ?? "bolt", 14), h("span", { class: "call-label" }, item.label), counts, h("span", { class: "call-summary" }, item.summary), state);
  const detail = callDetail(item);
  const tail = item.phase === "run" && item.output ? h("pre", { class: "call-tail" }, item.output.split("\n").slice(-3).join("\n")) : null;
  const typing = item.stream && !item.folded;
  const box = typing ? codeBox(item) : null;
  fill(el, head, tail, box, detail ? h("div", { class: "call-detail" }, detail) : null);
  if (box) {
    box.follow();
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
    await finished(animate(box, [{ height: `${box.offsetHeight}px`, opacity: 1 }, { height: "0px", opacity: 0, marginBottom: "0px" }], { curve: "emphasized", ms: 360, fill: "forwards" }));
  }
  box.remove();
}

function paintPlan(el, item) {
  fill(el, h("div", { class: "card-title" }, "PLAN"), markdown(item.text, false));
}

function paintError(el, item) {
  fill(el, h("span", { class: "mark fail" }, "✕"), h("span", {}, item.text));
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
