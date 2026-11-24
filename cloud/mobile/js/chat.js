// The conversation on screen. `render(feed)` makes the DOM follow the feed: one node per item, redrawn only when the
// item's rev moved, so a streaming reply costs one paragraph, not the whole page.
//
// Reading while it streams: the view follows the end only while the person is within a thumb of it. Scroll up and
// it stays put; a small "latest" button appears instead of the page jumping.

import { actionKind, tally } from "./feed.js";
import { clock, duration } from "./format.js";
import { h, icon } from "./dom.js";
import { t } from "./i18n.js";
import { parse, render as renderMarkdown } from "./markdown.js";
import * as native from "./native.js";

const PIN_DISTANCE = 100;
const STREAM_PAINT_MS = 50;

export function createChat(root, jump, tail) {
  const column = h("div", { class: "col" });
  column.append(tail);
  root.append(column);
  /** id -> {node, rev} */
  const nodes = new Map();
  const open = new Set();
  let feedRef = null;
  let scheduled = 0;

  const distance = () => root.scrollHeight - root.scrollTop - root.clientHeight;
  const pinned = () => distance() < PIN_DISTANCE;
  const toEnd = (smooth = false) => root.scrollTo({ top: root.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  const syncJump = () => jump.classList.toggle("hidden", pinned());
  root.addEventListener("scroll", syncJump, { passive: true });
  jump.addEventListener("click", () => toEnd(true));

  const markdown = (text) => h("div", { class: "md" }, renderMarkdown(parse(text), {
    onLink: (url) => native.open(url),
    copy: async (code, button) => {
      await native.copy(code);
      native.haptic("tick");
      button.textContent = t("copied");
      setTimeout(() => (button.textContent = t("copy")), 1400);
    },
  }));

  function reply(item) {
    const body = item.text.trim()
      ? markdown(item.live ? item.text + " " : item.text)
      : h("p", { class: "md dim" }, "…");
    return h("article", { class: `msg assistant${item.live ? " live" : ""}${item.outcome === "incomplete" ? " incomplete" : ""}` },
      body,
      item.live || !item.text.trim() ? null : h("div", { class: "msg-actions" },
        h("button", { class: "ghost", type: "button", aria: { label: t("copy") }, onclick: async (event) => {
          await native.copy(item.text);
          native.haptic("tick");
          event.currentTarget.replaceChildren(icon("check", 16));
        } }, icon("copy", 16))));
  }

  function callRow(call) {
    const details = [call.summary, call.output].filter(Boolean).join("\n").trim();
    const row = h("li", { class: `call ${call.phase}` },
      h("button", { class: "call-head", type: "button", disabled: !details, aria: { expanded: open.has(call.callId) }, onclick: () => {
        open.has(call.callId) ? open.delete(call.callId) : open.add(call.callId);
        paint();
      } },
        h("span", { class: "call-kind" }, icon(actionKind(call.actionType), 16)),
        h("span", { class: "call-label" }, call.label || call.actionType || t("step")),
        call.phase === "run" ? h("span", { class: "pulse", aria: { label: t("running") } })
          : h("span", { class: `call-mark ${call.phase}` }, icon(call.phase === "ok" ? "check" : "close", 14)),
        call.added || call.removed ? h("span", { class: "delta" }, `+${call.added ?? 0} −${call.removed ?? 0}`) : null),
      open.has(call.callId) && details ? h("pre", { class: "call-out" }, details.slice(-4000)) : null);
    return row;
  }

  function thoughtRow(thought, key) {
    const shown = open.has(key);
    return h("li", { class: "call thought" },
      h("button", { class: "call-head", type: "button", aria: { expanded: shown }, onclick: () => {
        shown ? open.delete(key) : open.add(key);
        paint();
      } },
        h("span", { class: "call-kind" }, icon("clock", 16)),
        h("span", { class: "call-label" }, thought.live ? t("thinking") : thought.seconds ? t("thought for", { s: Math.max(1, Math.round(thought.seconds)) }) : t("thought")),
        thought.live ? h("span", { class: "pulse" }) : null),
      shown && thought.text ? h("div", { class: "thought-text" }, thought.text.trim()) : null);
  }

  function work(item) {
    const counts = tally(item);
    const key = `w${item.id}`;
    // A turn in progress is open so the person sees the steps go by; a finished one folds into a line.
    const isOpen = item.live ? !open.has(`${key}-closed`) : open.has(key);
    const elapsed = ((item.endedAt ?? Date.now()) - item.startedAt) / 1000;
    const title = item.live
      ? (counts.running?.label || t("working"))
      : t(counts.total === 1 ? "worked one" : "worked", { time: elapsed >= 60 ? duration(elapsed) : `${Math.max(1, Math.round(elapsed))} ${t("sec")}`, n: counts.total });
    const toggle = () => {
      if (item.live) open.has(`${key}-closed`) ? open.delete(`${key}-closed`) : open.add(`${key}-closed`);
      else open.has(key) ? open.delete(key) : open.add(key);
      paint();
    };
    const rows = [...item.thoughts.map((th, i) => thoughtRow(th, `${key}t${i}`)), ...item.calls.map(callRow)];
    return h("section", { class: `work${item.live ? " live" : ""}${counts.failed ? " has-failed" : ""}` },
      h("button", { class: "work-head", type: "button", aria: { expanded: isOpen }, onclick: toggle },
        item.live ? h("span", { class: "pulse" }) : h("span", { class: "work-mark" }, icon("check", 14)),
        h("span", { class: "work-title" }, title),
        item.live ? h("span", { class: "clock", data: { since: item.startedAt } }, clock(elapsed)) : null,
        counts.files ? h("span", { class: "work-files" }, t("files changed", { n: counts.files })) : null,
        h("span", { class: `chev${isOpen ? " up" : ""}` }, icon("chevron-down", 16))),
      isOpen && rows.length ? h("ul", { class: "calls" }, rows) : null);
  }

  const builders = {
    user: (item) => h("article", { class: "msg user" }, h("div", { class: "bubble" },
      item.images?.length ? h("div", { class: "pics" }, item.images.map((pic) => pic.data_url
        ? h("img", { class: "pic", src: pic.data_url, alt: "" })
        : h("span", { class: "pic gone", title: pic.name }, icon("image", 20)))) : null,
      item.text ? h("div", {}, item.text) : null)),
    reply,
    work,
    note: (item) => h("p", { class: "note" }, item.text),
    error: (item) => h("div", { class: "msg error", role: "alert" }, icon("alert", 18), h("div", {}, item.text)),
    plan: (item) => h("section", { class: "plan" }, h("div", { class: "plan-label" }, t("plan")), markdown(item.text)),
  };

  function paint() {
    if (!feedRef) return;
    const wasPinned = pinned();
    const live = new Set();
    let anchor = null;
    for (const item of feedRef.items) {
      live.add(item.id);
      const build = builders[item.kind];
      if (!build) continue;
      const known = nodes.get(item.id);
      // A work item also depends on which of its rows are open, and on the clock: always rebuilt while it is live.
      if (!known || known.rev !== item.rev || item.kind === "work") {
        const node = build(item);
        if (known) known.node.replaceWith(node);
        else if (anchor) anchor.after(node);
        else column.prepend(node);
        nodes.set(item.id, { node, rev: item.rev });
      }
      anchor = nodes.get(item.id).node;
    }
    for (const [id, { node }] of nodes) {
      if (live.has(id)) continue;
      node.remove();
      nodes.delete(id);
    }
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

  setInterval(() => {
    for (const node of column.querySelectorAll(".clock[data-since]")) node.textContent = clock((Date.now() - Number(node.dataset.since)) / 1000);
  }, 1000);

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
      nodes.clear();
      open.clear();
      column.replaceChildren(tail);
    },
    toEnd,
    column,
  };
}
