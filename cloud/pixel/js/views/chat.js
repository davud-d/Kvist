// The agent in front: header, feed, and below it the composer, the closed
// banner or (over everything) the agent's open question.

import { activate, pause, runCommand, sendGoal, stopAll, wake } from "../actions.js";
import { sessionOp } from "../bridge.js";
import { summary } from "../reducer.js";
import { account, canResume, duration, projectName } from "../cloud.js";
import { fill, frameThrottle, h, icon, keyedList } from "../dom.js";
import { t } from "../i18n.js";
import { coolDown, formatSeconds, shimmerColor, spinnerWord, tokenCount } from "../spinner.js";
import { animate, enter } from "../motion.js";
import { active, setMode, store, subscribe } from "../store.js";
import { makeDialog } from "./dialogs.js";
import { makeItem, paintItem } from "./items.js";

const NEAR_BOTTOM = 80;

export function mountChat(root) {
  const title = h("div", { class: "chat-title" });
  const chips = h("div", { class: "chips" });
  const meter = h("div", { class: "meter", title: "" });
  const controls = h("div", { class: "chat-controls" });
  const header = h("div", { class: "chat-header" }, h("div", { class: "chat-heading" }, title, chips), meter, controls);
  const feed = h("div", { class: "feed", tabindex: "-1" });
  const empty = h("div", { class: "empty" });
  const status = h("div", { class: "status" });
  const input = h("textarea", { class: "composer-input", rows: "1", spellcheck: "false" });
  const send = h("button", { class: "btn primary send", type: "button", title: t("send") }, icon("send", 14));
  const composer = h("div", { class: "composer px" }, input, send);
  // A cloud task takes words only: no pictures to attach.
  const attachments = { images: [] };
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

  const atBottom = () => feed.scrollHeight - feed.scrollTop - feed.clientHeight < NEAR_BOTTOM;
  const toBottom = () => { feed.scrollTop = feed.scrollHeight; };

  function bindFeed(agent) {
    fill(feed, );
    drawFeed = keyedList(feed, {
      key: (item) => item.id,
      make: (item) => makeItem(item),
      update: (el, item) => paintItem(el, item),
      onEnter: (el) => shownAgent?.items.length < 400 && enter(el),
    });
    shownAgent = agent;
    dialogFor = null;
    sessionOp(agent.workspace, "input-history").then((reply) => { history = reply.entries ?? []; historyAt = history.length; }).catch(() => {});
  }

  function paintHeader(agent) {
    const stateLabel = summary(agent);
    const project = projectName(agent.workspace, account.projects, t("no project"));
    fill(title, h("span", { class: `state-dot ${stateLabel}` }), h("span", { class: "title-text" }, agent.title || t("new task")), h("span", { class: "title-sub" }, agent.session ? `#${agent.session}` : ""));
    const role = agent.routedRole || agent.role;
    fill(chips, 
      h("span", { class: "chip" }, t(stateLabel)),
      h("span", { class: "chip repo", title: project }, project),
      role ? h("span", { class: "chip role" }, t(role)) : null,
      agent.models?.fast ? h("span", { class: "chip model", title: `${agent.provider}: ${agent.models.fast} / ${agent.models.smart}` }, agent.models.smart || agent.models.fast) : null,
      agent.cloud ? h("span", { class: "chip hours", title: t("this task") }, duration(agent.cloud.used_seconds)) : null,
    );
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
      h("button", { class: "btn", type: "button", title: t("zoom out"), onclick: () => setMode("overview") }, icon("grid", 14)),
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
    empty.textContent = t("empty cloud");
    empty.hidden = agent.items.length > 0;
  }

  // What stands where the composer was: the link to a server that is down, a session another frontend
  // drives, a session that was closed.
  function bannerWords(agent) {
    if (agent.remote) return agent.remote.text;
    return null;
  }

  // What Enter on the banner does; the link that is down offers nothing.
  function bannerAction() {
    return () => {};
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
    animate(dialog.firstChild, [{ opacity: 0, transform: "translateY(16px) scale(0.94)" }, { opacity: 1, transform: "none" }], { spring: "bouncy" });
  }

  // The line shown while the turn runs: Claude Code's rippling word, ported (spinner.js). It has its own clock,
  // so it moves between core events; `shownWait` is the wait the colour is showing, which cools down gradually.
  let shownWait = 0;
  let shownAt = Date.now();
  function paintStatus() {
    const agent = active();
    if (!agent || agent.state !== "running") {
      shownWait = 0;
      return;
    }
    const now = Date.now();
    const calling = [...agent.calls.values()].some((call) => call.phase === "run");
    const target = agent.turn.waitingSince && !calling ? (now - agent.turn.waitingSince) / 1000 : 0;
    shownWait = coolDown(shownWait, target, (now - shownAt) / 1000);
    shownAt = now;
    const elapsed = agent.turn.startedAt ? (now - agent.turn.startedAt) / 1000 : 0;
    const request = agent.items.findLast((item) => item.kind === "user")?.text ?? "";
    const russian = /\p{Script=Cyrillic}/u.test(request);
    const word = spinnerWord(agent.routedRole || agent.role, agent.turn.startedAt, elapsed, request);
    const thinking = [...agent.streams.keys()].some((key) => key.startsWith("t:"));
    const effort = agent.effort && agent.effort !== "false" ? ` ${russian ? "· усилие" : "with"} ${agent.effort}` : "";
    const output = Math.max(0, agent.spend.output - agent.turn.outputAtStart);
    const doing = thinking ? `${russian ? "думаю" : "thinking"}${effort}` : (agent.status.text || "").slice(0, 48);
    const detail = [formatSeconds(elapsed), output > 0 ? `↓ ${tokenCount(output)} ${t("tokens")}` : "", doing].filter(Boolean).join(" · ");
    const letters = [...`${word}…`].map((char, index) => h("span", { style: { color: shimmerColor(shownWait, now, index) } }, char));
    fill(status, h("span", { class: "squirrel small working" }), h("span", { class: "shimmer" }, letters), h("span", { class: "status-detail" }, `(${detail})`));
  }
  setInterval(paintStatus, 100);

  const redraw = frameThrottle(() => {
    const agent = active();
    root.toggleAttribute("data-empty", !agent);
    // A task lives in a project's folder: with no project picked there is nowhere to write it.
    if (!agent) {
      composer.hidden = true;
      empty.textContent = t("empty cloud");
      empty.hidden = false;
      return;
    }
    if (agent !== shownAgent) bindFeed(agent);
    const stick = atBottom();
    drawFeed(agent.items);
    paintHeader(agent);
    paintBottom(agent);
    paintDialog(agent);
    if (stick) toBottom();
    const running = agent.state === "running";
    document.getElementById("app").dataset.busy = running ? "1" : "0";
  });

  function submit() {
    const agent = active();
    if (!agent || (!input.value.trim() && !attachments.images.length)) return;
    const text = input.value;
    input.value = "";
    input.style.height = "";
    history.push(text);
    historyAt = history.length;
    // The words come back if the cloud refuses them (no key, no hours, a bad repository link).
    sendGoal(agent, text).then((sent) => { if (!sent && !input.value) input.value = text; });
    toBottom();
  }

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  });
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
    if (!agent || store.mode !== "chat") return;
    if (event.key === "Escape" && agent.state === "running" && !agent.dialogs.length) pause(agent);
    else if (event.key === "c" && event.ctrlKey && agent.state === "running" && !getSelection()?.toString()) stopAll(agent);
    else if (event.key === "Enter" && bannerWords(agent) !== null && !agent.dialogs.length && document.activeElement !== input) bannerAction(agent)();
  });

  subscribe((change) => {
    if (change.kind === "agent" && change.id !== store.activeId) return;
    if (change.kind === "active") {
      const agent = active();
      if (agent?.state === "asleep") wake(agent);
      queueMicrotask(() => (active() && bannerWords(active()) === null ? input.focus() : banner.focus()));
    }
    redraw();
  });
  redraw();
  return { focusInput: () => input.focus(), activate };
}
