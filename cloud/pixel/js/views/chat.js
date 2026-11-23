// The agent in front: header, feed, and below it the composer, the closed
// banner or (over everything) the agent's open question.

import { activate, pause, runCommand, sendGoal, stopAll, wake } from "../actions.js";
import { sessionOp } from "../bridge.js";
import { summary } from "../reducer.js";
import { account, canResume, duration, onAccount, projectName } from "../cloud.js";
import { fill, frameThrottle, h, icon, keyedList } from "../dom.js";
import { t } from "../i18n.js";
import { formatSeconds, tokenCount } from "../spinner.js";
import { enter } from "../motion.js";
import { active, store, subscribe } from "../store.js";
import { makeDialog } from "./dialogs.js";
import { makeItem, paintItem } from "./items.js";
import { openProjectDialog } from "./project-dialog.js";

const NEAR_BOTTOM = 80;
const EXAMPLES = [
  ["table", "review spreadsheet", "spreadsheet example"],
  ["list", "draft checklist", "checklist example"],
  ["term", "test code", "code example"],
];

export function mountChat(root) {
  const title = h("div", { class: "chat-title" });
  const chips = h("div", { class: "chips" });
  const meter = h("div", { class: "meter", title: "" });
  const controls = h("div", { class: "chat-controls" });
  const header = h("div", { class: "chat-header" }, h("div", { class: "chat-heading" }, title, chips), meter, controls);
  const feed = h("div", { class: "feed", tabindex: "-1" });
  const empty = h("div", { class: "empty" });
  const statusLabel = h("span", { class: "status-label" });
  const statusDetail = h("span", { class: "status-detail" });
  const status = h("div", { class: "status" }, h("span", { class: "state-dot running" }), statusLabel, statusDetail);
  const input = h("textarea", { class: "composer-input", rows: "1", "aria-label": t("task message") });
  const send = h("button", { class: "btn primary send", type: "button", title: t("send"), "aria-label": t("send") }, icon("send"), h("span", {}, t("send")));
  const composer = h("div", { class: "composer px" }, input, send);
  const banner = h("button", { class: "closed-bar px", type: "button" });
  // paintBottom only runs once there is an agent; until then the empty bar would sit under the composer.
  banner.hidden = true;
  const dock = h("div", { class: "chat-bottom" }, status, composer, banner);
  const overlay = h("div", { class: "dialog-layer" });
  root.append(header, h("div", { class: "feed-wrap" }, feed, empty), dock, overlay);

  let shownAgent = null;
  let drawFeed = null;
  let dialogFor = null;
  let history = [];
  let historyAt = -1;
  let starterMode = null;
  const drafts = new WeakMap();

  function paintStarter(agent) {
    const kind = agent?.items.length || (agent && bannerWords(agent) !== null)
      ? "hidden" : agent ? "task" : account.ready ? "project" : "loading";
    if (starterMode === kind) return;
    starterMode = kind;
    empty.hidden = kind === "hidden" || kind === "loading";
    if (empty.hidden) return;
    const examples = EXAMPLES.map(([symbol, label, prompt]) =>
      h("button", { class: "btn starter-example", type: "button", onclick: () => {
        input.value = [input.value.trimEnd(), t(prompt)].filter(Boolean).join("\n\n");
        sizeInput();
        input.focus();
      } }, icon(symbol), h("span", {}, t(label)), icon("chev", 14)));
    fill(empty, h("div", { class: "starter" },
      h("h1", {}, t(kind === "task" ? "new task" : "workspace")),
      h("p", {}, t(kind === "task" ? "task starter hint" : "project starter hint")),
      kind === "task" ? h("div", { class: "starter-actions" }, examples)
        : h("button", { class: "btn primary", type: "button", onclick: openProjectDialog }, icon("plus"), t("add project"))));
  }

  const atBottom = () => feed.scrollHeight - feed.scrollTop - feed.clientHeight < NEAR_BOTTOM;
  const toBottom = () => { feed.scrollTop = feed.scrollHeight; };

  function bindFeed(agent) {
    if (shownAgent) drafts.set(shownAgent, input.value);
    input.value = drafts.get(agent) ?? "";
    sizeInput();
    fill(feed);
    drawFeed = keyedList(feed, {
      key: (item) => item.id,
      make: (item) => makeItem(item),
      update: (el, item) => paintItem(el, item),
      onEnter: (el) => shownAgent?.items.length < 400 && enter(el),
    });
    shownAgent = agent;
    dialogFor = null;
    history = [];
    historyAt = 0;
    sessionOp(agent.workspace, "input-history").then((reply) => {
      if (shownAgent !== agent) return;
      history = reply.entries ?? [];
      historyAt = history.length;
    }).catch(() => {});
  }

  function paintHeader(agent) {
    const stateLabel = summary(agent);
    const project = projectName(agent.workspace, account.projects, t("no project"));
    const draft = !agent.session && !agent.items.length && ["starting", "idle"].includes(stateLabel);
    fill(title, draft ? icon("folder") : h("span", { class: `state-dot ${stateLabel}` }),
      h(draft ? "div" : "h1", { class: "title-text" }, draft ? project : agent.title || t("new task")));
    const role = agent.routedRole || agent.role;
    fill(chips, ...(!draft ? [
      h("span", { class: "chip" }, t(stateLabel)),
      h("span", { class: "chip repo", title: project }, project),
      role ? h("span", { class: "chip role" }, t(role)) : null,
      agent.models?.fast ? h("span", { class: "chip model", title: `${agent.provider}: ${agent.models.fast} / ${agent.models.smart}` }, agent.models.smart || agent.models.fast) : null,
      agent.cloud ? h("span", { class: "chip hours", title: t("this task") }, duration(agent.cloud.used_seconds)) : null,
    ] : []));
    const { tokens, limit } = agent.context;
    const share = limit ? Math.min(1, tokens / limit) : 0;
    meter.title = limit ? `${tokens.toLocaleString()} / ${limit.toLocaleString()} ${t("tokens")}` : "";
    fill(meter, h("span", { class: "meter-label" }, t("context left")), h("span", { class: "meter-bar" }, ...Array.from({ length: 12 }, (_, i) => h("i", { class: i < Math.round(share * 12) ? "on" : "" }))));
    meter.hidden = !limit;
    const running = agent.state === "running";
    fill(controls, 
      canResume(agent.cloud) ? h("button", { class: "btn primary", type: "button", title: t("resume"), onclick: () => runCommand(agent, "resume") }, icon("play", 14)) : null,
      running ? h("button", { class: "btn", type: "button", title: `${t("pause")} (Esc)`, onclick: () => pause(agent) }, icon("pause", 14)) : null,
      running ? h("button", { class: "btn danger", type: "button", title: t("stop all"), onclick: () => stopAll(agent) }, icon("stop", 14)) : null,
    );
  }

  function paintBottom(agent) {
    const words = bannerWords(agent);
    composer.hidden = words !== null;
    banner.hidden = words === null;
    if (words !== null) banner.textContent = words;
    const running = agent.state === "running";
    // A session takes as many messages as you like; while the agent works they queue behind its turn.
    input.placeholder = running ? t("message running") : agent.session ? t("write a message") : t("write your task");
    input.disabled = agent.state === "gone";
    status.classList.toggle("on", running);
    paintStatus();
    paintStarter(agent);
  }

  // What stands where the composer was: the link to a server that is down, a session another frontend
  // drives, a session that was closed.
  function bannerWords(agent) {
    if (agent.remote) return agent.remote.text;
    return agent.closed !== null ? agent.closed || t("closed hint") : null;
  }

  // What Enter on the banner does; the link that is down offers nothing, a closed session opens a new task.
  function bannerAction(agent) {
    if (agent.remote) return () => {};
    return () => runCommand(agent, "new");
  }

  function paintDialog(agent) {
    // The driver's to answer; whoever takes over is sent the ones still open.
    const request = agent.dialogs[0];
    if (!request) {
      if (dialogFor) fill(overlay, );
      dialogFor = null;
      return;
    }
    if (dialogFor === request.request_id) return;
    dialogFor = request.request_id;
    const dialog = makeDialog(agent);
    fill(overlay, dialog);
    enter(dialog.firstChild);
  }

  function paintStatus() {
    const agent = active();
    if (!agent || agent.state !== "running") return;
    const elapsed = agent.turn.startedAt ? (Date.now() - agent.turn.startedAt) / 1000 : 0;
    const thinking = [...agent.streams.keys()].some((key) => key.startsWith("t:"));
    const output = Math.max(0, agent.spend.output - agent.turn.outputAtStart);
    const doing = thinking ? `${russian ? "думаю" : "thinking"}${effort}` : (agent.status.text || "").slice(0, 48);
    const detail = [formatSeconds(elapsed), output > 0 ? `↓ ${tokenCount(output)} ${t("tokens")}` : "", agent.status.text].filter(Boolean).join(" · ");
    statusLabel.textContent = t(thinking ? "thinking" : "task in progress");
    statusDetail.textContent = detail;
  }
  setInterval(paintStatus, 1000);

  const redraw = frameThrottle(() => {
    const agent = active();
    root.toggleAttribute("data-empty", !agent);
    // A task lives in a project's folder: with no project picked there is nowhere to write it.
    if (!agent) {
      composer.hidden = true;
      banner.hidden = true;
      status.classList.remove("on");
      paintStarter(null);
      return;
    }
    if (agent !== shownAgent) bindFeed(agent);
    const stick = atBottom();
    drawFeed(agent.items);
    paintHeader(agent);
    paintBottom(agent);
    paintDialog(agent);
    if (stick) toBottom();
  });

  function submit() {
    const agent = active();
    if (!agent || !input.value.trim()) return;
    const text = input.value;
    input.value = "";
    input.style.height = "";
    history.push(text);
    historyAt = history.length;
    // The words come back if the cloud refuses them (no key, no hours, a bad repository link).
    sendGoal(agent, text).then((sent) => {
      if (sent) return;
      if (shownAgent === agent && !input.value) {
        input.value = text;
        sizeInput();
      } else if (shownAgent !== agent && !drafts.get(agent)) {
        drafts.set(agent, text);
      }
    });
    toBottom();
  }

  function sizeInput() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }
  input.addEventListener("input", sizeInput);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    } else if (event.key === "ArrowUp" && !input.value.includes("\n") && input.selectionStart === 0 && historyAt > 0) {
      event.preventDefault();
      input.value = history[--historyAt];
    } else if (event.key === "ArrowDown" && historyAt < history.length && input.selectionEnd === input.value.length) {
      event.preventDefault();
      input.value = history[++historyAt] ?? "";
    }
  });
  send.addEventListener("click", submit);

  banner.addEventListener("click", () => {
    const agent = active();
    if (agent) bannerAction(agent)();
  });
  banner.addEventListener("keydown", (event) => {
    // Nothing is typed into a closed conversation; only Enter does something.
    if (event.key !== "Enter") event.preventDefault();
  });

  document.addEventListener("keydown", (event) => {
    const agent = active();
    if (!agent || store.mode !== "chat" || event.defaultPrevented) return;
    if (event.key === "Escape" && agent.state === "running" && !agent.dialogs.length) pause(agent);
    else if (event.key === "c" && event.ctrlKey && agent.state === "running" && !getSelection()?.toString()) stopAll(agent);
    else if (event.key === "Enter" && bannerWords(agent) !== null && !agent.dialogs.length && document.activeElement !== input) bannerAction(agent)();
  });

  subscribe((change) => {
    if (change.kind === "agent" && change.id !== store.activeId) return;
    if (change.kind === "active") {
      const agent = active();
      if (agent?.state === "asleep") wake(agent);
      if (store.mode === "chat" && !matchMedia("(pointer: coarse)").matches) {
        requestAnimationFrame(() => (active() && bannerWords(active()) === null ? input.focus() : banner.focus()));
      }
    }
    redraw();
  });
  onAccount(redraw);
  redraw();
  return { focusInput: () => input.focus(), activate };
}
