// One element per feed item: what the person said, what the agent said and
// thought, and a card for each call it made.

import { fill, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { renderMarkdown } from "../markdown.js";

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
  fill(el, head, tail, detail ? h("div", { class: "call-detail" }, detail) : null);
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
