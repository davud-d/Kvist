// The task screen, designed for a phone first: status and the meter on top, the agent's steps
// below, a plain question when it waits for the person, the result at the end.
import { api, openStream } from "../api.js";
import { h, modal, replace, skeleton } from "../dom.js";
import { apply, initialFeed, isEmpty } from "../events.js";
import { exportButton } from "../exporter.js";
import { hoursMinutes } from "../format.js";
import { t } from "../i18n.js";
import { diff, groupNode, markdown } from "../render.js";
import { errorBox, liveRegion, taskPill } from "../ui.js";

const FINISHED = new Set(["done", "cancelled", "failed"]);
const SHOWS_RESULT = new Set(["done", "cancelled", "failed", "paused"]);

function askNode(request, answer) {
  const busy = (form) => form.querySelectorAll("button, input, textarea").forEach((el) => { el.disabled = true; });
  const form = h("form.ask", { "aria-labelledby": "ask-title" });
  // Not uppercased: the title often quotes a command, which must read exactly as it will run.
  const title = h("p", { id: "ask-title", style: "font-weight:700" }, request.title || "");
  const body = request.body ? h("pre", {}, request.body) : null;
  const send = (payload) => { busy(form); answer({ request_id: request.request_id, ...payload }); };
  if (request.kind === "confirm") {
    form.append(title, body || "", h("div.row", {},
      h("button.btn", { type: "button", onclick: () => send({ approved: true }), text: t("task.allow") }),
      h("button.btn.ghost", { type: "button", onclick: () => send({ approved: false }), text: t("task.deny") })));
    return form;
  }
  if (request.kind === "choice") {
    form.append(title, body || "", h("div.stack", {}, (request.options || []).map((option) =>
      h("button.btn.ghost.wide", { type: "button", onclick: () => send({ value: option.value }) },
        option.label || option.value))));
    return form;
  }
  const field = request.kind === "secret"
    ? h("input.input", { type: "password", autocomplete: "off", "aria-label": request.title })
    : h("textarea.input", { rows: 3, placeholder: t("task.answerPlaceholder"), "aria-label": request.title });
  field.value = request.kind === "secret" ? "" : request.current_value || "";
  form.addEventListener("submit", (event) => { event.preventDefault(); send({ value: field.value }); });
  form.append(title, body || "", field, h("button.btn", { type: "submit", text: t("task.send") }));
  return form;
}

export async function taskScreen(root, { params, navigate }) {
  const id = Number(params[0]);
  replace(root, skeleton(5));
  let task = await api(`/api/tasks/${id}`);
  const feed = initialFeed();
  let synced = { at: Date.now(), used: task.used_seconds };

  const announce = liveRegion();
  const pill = h("span");
  const meter = h("p.meter", { "aria-live": "off" });
  const controls = h("div.row");
  const connection = h("p.small", { "aria-live": "polite" });
  const notice = h("div");
  const ask = h("div");
  const live = h("p.notice", { "aria-live": "off" });
  const list = h("ol.feed", { "aria-label": t("task.goal") });
  const empty = h("p.notice", {}, t("task.emptyFeed"));
  const result = h("div");
  const nodes = new Map();

  const goal = h("p.goal.clamped", {}, task.goal);
  goal.addEventListener("click", () => goal.classList.toggle("clamped"));

  replace(root,
    h("div.taskbar", {}, h("div.stack", {},
      h("div.spread", {}, h("a.small", { href: "/dashboard", "data-link": "" }, `← ${t("task.back")}`), pill),
      meter, controls)),
    h("section.stack", { style: "margin-top:16px" },
      h("div", {}, h("p.section-title", {}, t("task.goal")), goal,
        h("p.small", {}, `${t("task.repo")}: `, task.repo_url || t("task.noRepo"))),
      connection, notice, ask,
      empty, list, live, result, announce));

  const shownSeconds = () => {
    if (task.status !== "running") return task.used_seconds;
    // Between heartbeats the minute ticks over on time; the server's number wins at each update.
    return synced.used + Math.min(60, (Date.now() - synced.at) / 1000);
  };

  const drawMeter = () => {
    const running = task.status === "running";
    replace(meter,
      h("span", {}, `${t("task.thisTask")}: `, h("b", {}, hoursMinutes(shownSeconds(), t))),
      h("span", {}, `${t("task.left")}: `, h("b", {}, hoursMinutes(task.balance_seconds, t))),
      h(`span${running ? ".ticking" : ""}`, {}, running ? t("task.meterRunning") : t("task.meterStopped")));
  };

  const act = (path) => async () => {
    try {
      task = { ...task, ...(await api(`/api/tasks/${id}/${path}`, { method: "POST" })) };
      draw();
    } catch (error) {
      replace(notice, errorBox(error));
    }
  };
  const confirmStop = () => modal((close) => h("div.stack", {},
    h("h2.h3", {}, t("task.stopTitle")),
    h("p.dim", {}, t("task.stopBody")),
    h("div.row", {},
      h("button.btn.danger", { type: "button", onclick: () => { close(); act("cancel")(); }, text: t("task.stopYes") }),
      h("button.btn.ghost", { type: "button", autofocus: true, onclick: close, text: t("task.keepGoing") }))));

  const drawControls = () => {
    const status = task.status;
    const buttons = [];
    if (status === "running" || status === "waiting_for_user") {
      buttons.push(h("button.btn.ghost", { type: "button", onclick: act("pause"), text: t("task.pause") }));
    }
    if (status === "paused") buttons.push(h("button.btn", { type: "button", onclick: act("resume"), text: t("task.resume") }));
    if (!FINISHED.has(status)) buttons.push(h("button.btn.ghost", { type: "button", onclick: confirmStop, text: t("task.stop") }));
    if (FINISHED.has(status)) {
      buttons.push(h("a.btn", { href: "/new", "data-link": "", text: t("task.newTask") }));
    }
    replace(controls, buttons);
  };

  const drawNotice = () => {
    const status = task.status;
    if (status === "waiting_for_user") {
      replace(notice, h("div.banner.warn", { role: "status" },
        h("p.title", {}, t("task.waitingTitle")), h("p.dim", {}, t("task.waitingBody"))));
    } else if (status === "queued") {
      replace(notice, h("div.banner", {}, h("p", {}, t("task.queuedNote"))));
    } else if (status === "starting") {
      replace(notice, h("div.banner", {}, h("p", {}, t("task.startingNote"))));
    } else if ((status === "paused" || status === "failed" || status === "cancelled") && task.reason) {
      const text = t(`reason.${task.reason}`);
      replace(notice, h(`div.banner${status === "failed" ? ".err" : ".warn"}`, {},
        h("p", {}, text.startsWith("reason.") ? t(`taskStatus.${status}`) : text)));
    } else {
      replace(notice);
    }
  };

  const answer = async (response) => {
    try {
      await api(`/api/tasks/${id}/input`, { method: "POST", body: response });
      feed.input = null;
      drawAsk();
    } catch (error) {
      replace(ask, errorBox(error));
    }
  };
  const drawAsk = () => {
    replace(ask, feed.input && task.status === "waiting_for_user" ? askNode(feed.input, answer) : null);
  };

  let changesShown = false;
  const drawResult = async () => {
    if (!SHOWS_RESULT.has(task.status) || changesShown) return;
    changesShown = true;
    let files = [];
    try {
      files = await api(`/api/tasks/${id}/changes`);
    } catch (error) {
      replace(result, errorBox(error));
      return;
    }
    replace(result, h("section.card", { "aria-labelledby": "result-title" },
      h("h2.h3", { id: "result-title" }, t("task.resultTitle")),
      task.final_message ? markdown(task.final_message) : null,
      h("h3.section-title", {}, t("task.changedFiles")),
      files.length
        ? h("ul.files", {}, files.map((file) => h("li", {}, h("details.fold", {},
            h("summary", {}, file.path, " ", h("span.count", {}, `+${file.lines_added} −${file.lines_removed}`)),
            file.patches.map(diff)))))
        : h("p.dim", {}, t("task.noChanges")),
      exportButton(`/api/tasks/${id}/export`, t("task.download"))));
  };

  const draw = () => {
    empty.hidden = feed.groups.length > 0 || FINISHED.has(task.status) || task.status === "queued";
    replace(pill, taskPill(task.status));
    drawMeter();
    drawControls();
    drawNotice();
    drawAsk();
    drawResult();
  };

  let frame = 0;
  const dirty = new Set();
  const flush = () => {
    frame = 0;
    for (const group of dirty) {
      if (isEmpty(group)) continue;
      const node = groupNode(group);
      const old = nodes.get(group);
      if (old) old.replaceWith(node);
      else list.append(node);
      nodes.set(group, node);
    }
    dirty.clear();
    if (feed.groups.length) empty.hidden = true;
    live.textContent = feed.live;
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(flush); };

  draw();
  const stop = openStream(id, {
    onState: (state) => {
      connection.textContent = state === "reconnecting" ? t("task.reconnecting") : "";
      if (state === "signed_out") replace(notice, errorBox({ code: "not_signed_in" }));
    },
    onMessage: (message) => {
      if (message.type === "task") {
        const before = task.status;
        task = { ...task, ...message.task };
        synced = { at: Date.now(), used: task.used_seconds };
        if (before !== task.status) announce.textContent = t(`taskStatus.${task.status}`);
        draw();
      } else if (message.type === "usage") {
        task.used_seconds = message.used_seconds;
        task.balance_seconds = message.balance_seconds;
        synced = { at: Date.now(), used: task.used_seconds };
        drawMeter();
      } else if (message.type === "event") {
        const group = apply(feed, message.event);
        if (group) dirty.add(group);
        if (["input_request", "input_closed", "final", "turn_end"].includes(message.event.type)) drawAsk();
        schedule();
      } else if (message.type === "resync") {
        navigate(`/tasks/${id}`, { replace: true });
      }
    },
  });
  const ticker = setInterval(() => { if (task.status === "running") drawMeter(); }, 1000);
  return () => { stop(); clearInterval(ticker); if (frame) cancelAnimationFrame(frame); };
}
