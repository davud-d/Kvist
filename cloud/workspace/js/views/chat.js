// One task in focus, across the whole stage. Its header says what it is (name, project, state, time); under it the
// course of the work (the conversation, with what the agent did folded into blocks). Below it the field for the next message; when the
// agent asks something, the question takes the field's place, because answering it is the next thing to do.
// Details (screen, helpers, background commands, a file's changes) open in the side sheet on request.

import { activate, pause, runCommand, sendGoal, stopAll, wake } from "../actions.js";
import { sessionOp } from "../bridge.js";
import { account, canResume, duration, onAccount, projectName } from "../cloud.js";
import { fill, frameThrottle, h, icon, keyedList, spinner, inPhase } from "../dom.js";
import { feedBlocks } from "../feed.js";
import { lang, t } from "../i18n.js";
import { enter, popIn } from "../motion.js";
import { summary } from "../reducer.js";
import { coolDown, formatSeconds, spinnerWord, tokenCount } from "../spinner.js";
import { active, setSheet, store, subscribe } from "../store.js";
import { makeDialog } from "./dialogs.js";
import { Folds } from "../folds.js";
import { makeItem, makeWork, paintItem, paintWork, wakeWork } from "./items.js";
import { imageGoal, mountAttachments } from "./attachments.js";
import { attachCommandHint, offeredCommands } from "./commands.js";
import { makeOrb, orbState, setOrb, syncOrb } from "./orb.js";
import { mountNotices } from "./notices.js";
import { fieldMenus, runPicks } from "./run-picks.js";

const NEAR_BOTTOM = 80;
// Below this share of the context left, the header says so; above it the number is noise.
const CONTEXT_WARN = 0.2;
const LIVE_HELPERS = new Set(["starting", "running", "paused"]);

export function mountChat(root) {
  const title = h("h1", { class: "session-title" });
  const meta = h("p", { class: "session-meta" });
  const tools = h("div", { class: "session-tools" });
  const controls = h("div", { class: "session-controls" });
  const header = h("header", { class: "session-head" }, h("div", { class: "session-name" }, title, meta), tools, controls);
  const notices = h("div", { class: "notices" });
  const feed = h("div", { class: "feed", tabindex: "-1" });
  const empty = h("div", { class: "empty" });
  const status = h("div", { class: "status" });
  const input = h("textarea", { class: "composer-input", rows: "1", spellcheck: "true" });
  const send = h("button", { class: "btn primary send", type: "button", title: t("send"), "aria-label": t("send"), disabled: true }, icon("send", 18));
  const foot = h("div", { class: "composer-foot" });
  const composer = h("div", { class: "composer task-composer" }, input, foot);
  // The model and the effort the task's next message runs on; their menus open upwards, the field is at the bottom.
  const menus = fieldMenus(input, "0 100%");
  const picks = runPicks(composer, menus);
  foot.append(picks.model, picks.effort, h("span", { class: "spacer" }), send);
  const photos = mountAttachments({ composer, field: input, foot, onChange: () => { send.disabled = !ready(); } });
  const ready = () => Boolean(input.value.trim() || photos.images.length);
  onAccount(picks.redraw);
  document.addEventListener("kvist:model-picker", () => {
    if (store.mode === "chat") queueMicrotask(picks.openModel);
  });
  attachCommandHint(input, composer, () => submit(), () => offeredCommands(active()?.manifest ?? account.commands.manifest, account.commands.hidden));
  const hint = h("div", { class: "composer-hint" });
  const banner = h("button", { class: "closed-bar", type: "button", hidden: true });
  const question = h("div", { class: "question" });
  const dock = h("div", { class: "chat-bottom" }, status, question, composer, hint, banner);
  root.append(header, notices, h("div", { class: "session-body" }, feed, empty), dock);
  mountNotices(notices);

  let shownAgent = null;
  let drawFeed = null;
  // When the task's groups of calls fold (as in Kvist Glass); a fresh one per task shown.
  let folds = null;
  let settledOnce = false;
  let dialogFor = null;
  let history = [];
  let historyAt = -1;

  const atBottom = () => feed.scrollHeight - feed.scrollTop - feed.clientHeight < NEAR_BOTTOM;
  const toBottom = () => { feed.scrollTop = feed.scrollHeight; };

  function bindFeed(agent) {
    fill(feed);
    const isWork = (block) => block.kind === "work";
    folds = new Folds();
    settledOnce = false;
    drawFeed = keyedList(feed, {
      key: (block) => block.id,
      make: (block) => (isWork(block) ? makeWork(block, folds) : makeItem(block)),
      update: (el, block) => (isWork(block) ? paintWork(el, block) : paintItem(el, block)),
      onEnter: (el) => shownAgent?.items.length < 400 && enter(el),
    });
    shownAgent = agent;
    dialogFor = null;
    sessionOp(agent.workspace, "input-history").then((reply) => { history = reply.entries ?? []; historyAt = history.length; }).catch(() => {});
  }

  // The state in words a person uses, and the hours this task has cost, in one line under the name.
  function paintHeader(agent) {
    const state = summary(agent);
    title.textContent = agent.title || t("new task");
    const { tokens, limit } = agent.context;
    const left = limit ? 1 - Math.min(1, tokens / limit) : 1;
    const parts = [
      projectName(agent.workspace, account.projects, t("no project")),
      t(state),
      agent.cloud?.used_seconds >= 60 ? `${t("worked")} ${duration(agent.cloud.used_seconds)}` : "",
    ].filter(Boolean);
    fill(meta, parts.join(" · "), left < CONTEXT_WARN ? h("span", { class: "meta-warn", title: `${tokens.toLocaleString()} / ${limit.toLocaleString()} ${t("tokens")}` }, ` · ${t("context left")} ${Math.round(left * 100)}%`) : null);

    // The buttons are made again only when what they show changed: a button replaced under the pointer loses its
    // hover, and a running task sends many events a second.
    const helpers = [...agent.helpers.values()].filter((x) => LIVE_HELPERS.has(x.state)).length;
    const jobs = [...agent.jobs.values()].filter((job) => job.state === "running").length;
    const running = agent.state === "running";
    const shape = [agent.id, Boolean(agent.session), store.sheet?.kind, Boolean(agent.screen), agent.helpers.size, helpers, agent.jobs.size, jobs, canResume(agent.cloud), running, lang.current].join("|");
    if (paintHeader.shape === shape) return;
    paintHeader.shape = shape;
    // Only what this task has: the agent's screen, its helpers, its background commands.
    const tool = (kind, iconName, label, count) => h("button", { class: `btn flat${store.sheet?.kind === kind ? " on" : ""}`, type: "button", onclick: () => setSheet({ kind }) }, icon(iconName, 16), h("span", {}, label), count ? h("span", { class: "count" }, String(count)) : null);
    fill(tools,
      agent.screen ? tool("screen", "eye", t("screen"), 0) : null,
      agent.helpers.size ? tool("helpers", "bot", t("helpers"), helpers) : null,
      agent.jobs.size ? tool("jobs", "term", t("jobs"), jobs) : null);

    fill(controls,
      canResume(agent.cloud) ? h("button", { class: "btn primary", type: "button", onclick: () => runCommand(agent, "resume") }, icon("play", 16), h("span", {}, t("resume"))) : null,
      running ? h("button", { class: "btn", type: "button", title: `${t("pause")} (Esc)`, onclick: () => pause(agent) }, icon("pause", 16), h("span", {}, t("pause"))) : null,
      running ? h("button", { class: "btn danger", type: "button", onclick: () => stopAll(agent) }, icon("stop", 16), h("span", {}, t("stop all"))) : null);
  }

  function paintBottom(agent) {
    const words = bannerWords(agent);
    const asking = agent.dialogs.length > 0;
    composer.hidden = words !== null || asking;
    hint.hidden = words !== null || asking;
    banner.hidden = words === null || asking;
    if (words !== null) banner.textContent = words;
    const running = agent.state === "running";
    // A session takes as many messages as you like; while the agent works they queue behind its turn.
    input.placeholder = running ? t("message running") : agent.session ? t("write a message") : t("write your task");
    input.disabled = agent.state === "gone";
    status.classList.toggle("on", running && !asking);
    paintStatus();
    // Enter and Shift+Enter need no words; pausing with Esc is not obvious, so it is said while it applies.
    hint.hidden = hint.hidden || !running;
    fill(hint, running ? h("span", {}, t("composer hint running")) : null);
    send.disabled = !ready();
  }

  // What stands where the composer was: the link to a server that is down, a session another frontend
  // drives, a session that was closed.
  function bannerWords(agent) {
    if (agent.remote) return agent.remote.text;
    return agent.closed !== null ? agent.closed || t("closed hint") : null;
  }

  // What Enter on the banner does; the link that is down offers nothing, a closed session starts a new task.
  function bannerAction(agent) {
    if (agent.remote) return () => {};
    return () => runCommand(agent, "new");
  }

  function paintQuestion(agent) {
    // The driver's to answer; whoever takes over is sent the ones still open.
    const request = agent.dialogs[0];
    if (!request) {
      if (dialogFor) fill(question);
      dialogFor = null;
      return;
    }
    if (dialogFor === request.request_id) return;
    dialogFor = request.request_id;
    const dialog = makeDialog(agent);
    fill(question, dialog);
    popIn(dialog.firstChild, "50% 100%");
    queueMicrotask(() => (question.querySelector("input, textarea, .btn.primary") ?? question.querySelector(".dialog"))?.focus());
  }

  // The line shown while the turn runs: Claude Code's rippling word, ported (spinner.js). It has its own clock,
  // so it moves between core events; `shownWait` is the wait the colour is showing, which cools down gradually.
  let shownWait = 0;
  let shownAt = Date.now();
  function paintStatus() {
    const agent = active();
    if (!agent || agent.state !== "running" || store.mode !== "chat") {
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
    const doing = thinking ? `${russian ? "думаю" : "thinking"}${effort}` : (agent.status.text || "").slice(0, 60);
    const detail = [formatSeconds(elapsed), output > 0 ? `↓ ${tokenCount(output)} ${t("tokens")}` : "", doing].filter(Boolean).join(" · ");
    // A long wait for the model warms the word: amber, then red (the same thresholds as Claude Code's).
    const tone = shownWait > 45 ? " stuck" : shownWait > 5 ? " slow" : "";
    const label = `${word}…`;
    if (status.dataset.label !== label + tone) {
      status.dataset.label = label + tone;
      fill(status, spinner(), inPhase(h("span", { class: `shimmer${tone}` }, label), 2400), h("span", { class: "status-detail" }));
    }
    status.lastChild.textContent = detail;
  }
  setInterval(paintStatus, 100);

  // The orb: at the end of the words while the model speaks, on its own line at the end otherwise.
  const orb = makeOrb();
  const orbRow = h("div", { class: "orb-row" }, orb);
  function placeOrb(agent) {
    const state = orbState(agent);
    if (!state) {
      orbRow.remove();
      orb.remove();
      return;
    }
    setOrb(orb, state);
    const last = agent.items.at(-1);
    const words = state === "speak" ? feed.querySelector(`:scope > [data-key="${last.id}"] .md`) : null;
    // Inside the last paragraph (or list item) so it sits on the line, not under it.
    const host = words ? (words.lastElementChild && /^(P|LI|UL|OL)$/.test(words.lastElementChild.tagName) ? deepestLast(words.lastElementChild) : words) : orbRow;
    if (orb.parentNode !== host || host.lastChild !== orb) {
      host.append(orb);
      syncOrb(orb);
    }
    if (words) orbRow.remove();
    else if (orbRow.parentNode !== feed || feed.lastChild !== orbRow) feed.append(orbRow);
  }
  const deepestLast = (el) => (el.lastElementChild && /^(UL|OL|LI|P)$/.test(el.lastElementChild.tagName) ? deepestLast(el.lastElementChild) : el);

  const redraw = frameThrottle(() => {
    root.hidden = store.mode !== "chat";
    const agent = active();
    if (root.hidden || !agent) return;
    if (agent !== shownAgent) bindFeed(agent);
    const stick = atBottom();
    const blocks = feedBlocks(agent.items);
    const now = performance.now();
    // What was already done when the task opened is folded at once, not opened and folded again.
    folds.settle(blocks, agent.state === "running", now, { quiet: !settledOnce });
    settledOnce ||= blocks.length > 0;
    drawFeed(blocks);
    wakeWork(feed, now);
    placeOrb(agent);
    empty.hidden = agent.items.length > 0;
    fill(empty, agent.session ? h("p", { class: "dim" }, t("loading")) : null);
    paintHeader(agent);
    paintBottom(agent);
    paintQuestion(agent);
    if (stick) toBottom();
  });

  function submit() {
    const agent = active();
    if (!agent || !ready()) return;
    const text = input.value;
    const images = photos.images;
    input.value = "";
    input.style.height = "";
    photos.set([]);
    send.disabled = true;
    if (text.trim()) history.push(text);
    historyAt = history.length;
    // The words and the photos come back if the cloud refuses them (no key, no hours, a bad repository link).
    sendGoal(agent, imageGoal(text, images)).then((sent) => {
      if (sent || input.value || photos.images.length) return;
      input.value = text;
      photos.set(images);
    });
    toBottom();
  }

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 240)}px`;
    send.disabled = !ready();
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
    if (!agent || store.mode !== "chat" || document.querySelector("#modals > *")) return;
    if (event.key === "Escape" && !event.shiftKey && agent.state === "running" && !agent.dialogs.length) pause(agent);
    else if (event.key === "c" && event.ctrlKey && agent.state === "running" && !getSelection()?.toString()) stopAll(agent);
    else if (event.key === "Enter" && bannerWords(agent) !== null && !agent.dialogs.length && document.activeElement !== input) bannerAction(agent)();
  });

  subscribe((change) => {
    if (change.kind === "agent" && change.id !== store.activeId) return;
    if (change.kind === "active" || (change.kind === "mode" && store.mode === "chat")) {
      const agent = active();
      if (agent?.state === "asleep") wake(agent);
      queueMicrotask(() => {
        if (!active() || store.mode !== "chat") return;
        if (bannerWords(active()) === null) input.focus();
        else banner.focus();
      });
    }
    redraw();
  });
  redraw();
  return { focusInput: () => input.focus(), activate };
}
