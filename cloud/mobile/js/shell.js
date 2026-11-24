// The app's one screen: a bar, the conversation (or the start of a new one), a dock with the composer, and the
// drawer. It decides what the rest do: open a session, send a message, pause, and which sheet a problem needs.

import { account, onAccount, refresh } from "./account.js";
import { api, post } from "./api.js";
import { createChat } from "./chat.js";
import { createComposer } from "./composer.js";
import { dialogFor } from "./dialogs.js";
import { fill, h, icon } from "./dom.js";
import { createDrawer } from "./drawer.js";
import { createFeed, working } from "./feed.js";
import { clock, titleOf } from "./format.js";
import { homeView } from "./home.js";
import { t } from "./i18n.js";
import * as native from "./native.js";
import { banner, cannotRun, machineState } from "./rules.js";
import { openSheet, toast } from "./sheet.js";
import { openStream } from "./session.js";
import { accountSheet } from "./sheets/account.js";
import { plansSheet } from "./sheets/billing.js";
import { keySheet } from "./sheets/key.js";
import { projectSheet } from "./sheets/project.js";

const TASK_REFRESH_MS = 30_000;

/** What a problem sending needs from the person: the sheet that fixes it. */
function fixFor(code) {
  if (code === "no_subscription" || code === "no_hours") return plansSheet;
  if (code === "no_key" || code === "chatgpt_login") return () => keySheet();
  return null;
}

export function startShell(root, { go, onAuth }) {
  const feedEl = h("main", { class: "feed", id: "feed" });
  const jump = h("button", { class: "jump hidden", type: "button", aria: { label: t("latest") } }, icon("arrow-down", 18));
  const chat = createChat(feedEl, jump);
  const homeEl = h("div", { class: "home-wrap" });
  feedEl.prepend(homeEl);

  const composer = createComposer({ onSend: (text) => send(text), onStop: () => pause() });
  const bannerEl = h("div", { class: "banner-slot" });
  const statusEl = h("div", { class: "status-line", role: "status" });
  const dialogEl = h("div", { class: "dialog-slot" });
  const dock = h("footer", { class: "dock" }, bannerEl, statusEl, dialogEl, composer.node);

  const title = h("div", { class: "t1" });
  const subtitle = h("div", { class: "t2" });
  const more = h("button", { class: "icon-btn", type: "button", aria: { label: t("more") }, onclick: () => sessionMenu() }, icon("more", 22));
  const bar = h("header", { class: "bar" },
    h("button", { class: "icon-btn", type: "button", aria: { label: t("sessions") }, onclick: () => drawer.open() }, icon("menu", 22)),
    h("div", { class: "bar-title" }, title, subtitle),
    more);

  const drawer = createDrawer({
    onNew: () => go("/m/"),
    onSession: (publicId) => go(`/m/s/${publicId}`),
    onProject: (id) => go(`/m/p/${id}`),
    onAddProject: () => projectSheet((project) => go(`/m/p/${project.id}`)),
    onAccount: accountSheet,
  });

  root.replaceChildren(bar, feedEl, jump, dock, drawer.node);

  /** {kind: "new", projectId} or {kind: "session", publicId, task, feed, stream, link}. */
  let view = { kind: "new", projectId: "" };

  const link = () => view.link ?? "up";

  function paintBar() {
    const task = view.task;
    const project = task?.project_id != null ? account.projects.find((p) => p.id === task.project_id)?.name : "";
    title.textContent = view.kind === "session" ? (titleOf(task ?? {}) || t("session")) : t("new session");
    const machine = machineState(account.machines);
    subtitle.textContent = [project, view.kind === "session" ? t(`machine ${machine}`) : ""].filter(Boolean).join(" · ");
    more.hidden = view.kind !== "session";
  }

  function paintBanner() {
    const found = banner({ account, task: view.task, link: link() });
    if (!found) return fill(bannerEl);
    const action = found.action === "plans" ? plansSheet : found.action === "key" ? () => keySheet() : null;
    fill(bannerEl, h("div", { class: `banner ${found.level}` },
      h("span", {}, t(found.text, found.params.seconds === undefined ? {} : { time: clock(found.params.seconds).replace(/^0:/, "0:") })),
      action ? h("button", { class: "banner-action", type: "button", onclick: action }, t(found.action === "plans" ? "see plans" : "add key")) : null));
  }

  function paintDock() {
    const feed = view.feed;
    const state = feed ? working(feed) : "";
    // "Pause" exists once the agent is running: before that (queued, starting) the server has nothing to pause yet.
    composer.setBusy(feed?.task?.status === "running" || state === "waiting");
    const dialog = feed?.dialogs[0];
    composer.node.hidden = Boolean(dialog);
    fill(dialogEl, dialog ? dialogFor(dialog, (reply) => answer(dialog, reply)) : null);
    if (!state) return fill(statusEl);
    const words = state === "waiting" ? t("waiting for you") : [t("working"), feed.status.step ? t("step n", { n: feed.status.step }) : "", feed.status.text].filter(Boolean).join(" · ");
    fill(statusEl, h("span", { class: "pulse" }), h("span", {}, words));
  }

  function paint() {
    paintBar();
    paintBanner();
    paintDock();
  }
  onAccount(paint);

  // -- showing -----------------------------------------------------------------------------------------

  function stop() {
    view.stream?.close();
    chat.reset();
  }

  function showNew(projectId = "") {
    stop();
    view = { kind: "new", projectId };
    drawer.setCurrent("");
    homeEl.hidden = false;
    chat.column.hidden = true;
    homeEl.replaceChildren(homeView({
      projectId,
      onChooseProject: () => chooseProject(projectId),
      onSuggest: (text) => composer.set(text),
      onSetup: (id) => (id === "plan" ? plansSheet() : id === "key" ? keySheet() : projectSheet((project) => go(`/m/p/${project.id}`))),
    }));
    paint();
  }

  async function showSession(publicId) {
    stop();
    drawer.setCurrent(publicId);
    homeEl.hidden = true;
    chat.column.hidden = false;
    const feed = createFeed();
    view = { kind: "session", publicId, feed, task: null, link: "up" };
    paint();
    let task;
    try {
      task = await api(`/api/sessions/${publicId}`);
    } catch (error) {
      if (error.status === 401) return onAuth();
      toast(error.status === 404 ? t("session not found") : error.localized, "error");
      return go("/m/", true);
    }
    if (view.publicId !== publicId) return undefined;
    attach(task, feed);
    return undefined;
  }

  /** Connects a task's stream to the feed on screen. */
  function attach(task, feed) {
    view = { ...view, kind: "session", publicId: task.public_id, task, feed };
    feed.task = task;
    drawer.setCurrent(task.public_id);
    homeEl.hidden = true;
    chat.column.hidden = false;
    let refreshTimer = 0;
    view.stream = openStream(task.id, feed, {
      onChange: (done) => {
        done ? chat.renderNow(feed) : chat.render(feed);
        paintDock();
      },
      onTask: (next) => {
        const finished = view.task?.status !== next.status;
        view.task = next;
        paint();
        if (finished) {
          clearTimeout(refreshTimer);
          refreshTimer = setTimeout(() => refresh("tasks", "sub", "machines"), 600);
        }
      },
      onAuth,
      onLink: (state) => {
        view.link = state;
        paintBanner();
      },
    });
  }

  // -- doing ---------------------------------------------------------------------------------------------------

  function guide(code) {
    const fix = fixFor(code);
    if (fix) fix();
    return Boolean(fix);
  }

  async function send(text) {
    const blocked = cannotRun(account);
    if (blocked && guide(blocked)) return;
    try {
      if (view.kind === "new") {
        const body = { goal: text, ...(view.projectId ? { project_id: Number(view.projectId) } : {}) };
        const task = await post("/api/tasks", body);
        composer.clear();
        const feed = createFeed();
        history.replaceState(null, "", `/m/s/${task.public_id}`);
        attach(task, feed);
        refresh("tasks", "machines");
        return;
      }
      await post(`/api/tasks/${view.task.id}/messages`, { text });
      composer.clear();
      refresh("tasks");
    } catch (error) {
      if (error.status === 401) return onAuth();
      if (!guide(error.code)) toast(error.localized || t("send failed"), "error");
    }
  }

  async function pause() {
    if (!view.task) return;
    try {
      await post(`/api/tasks/${view.task.id}/pause`);
    } catch (error) {
      toast(error.localized, "error");
    }
  }

  async function answer(request, reply) {
    view.feed.dialogs = view.feed.dialogs.filter((d) => d.request_id !== request.request_id);
    paintDock();
    try {
      await post(`/api/tasks/${view.task.id}/input`, { request_id: request.request_id, ...reply });
    } catch (error) {
      toast(error.localized, "error");
    }
  }

  function chooseProject(current) {
    openSheet({
      title: t("project"),
      build: (close) => [
        h("button", { class: `row${current ? "" : " on"}`, type: "button", onclick: () => {
          close();
          go("/m/", true);
        } }, icon("folder", 20), h("span", { class: "row-main" }, t("no project"))),
        account.projects.map((project) => h("button", { class: `row${String(project.id) === String(current) ? " on" : ""}`, type: "button", onclick: () => {
          close();
          go(`/m/p/${project.id}`, true);
        } }, icon("folder", 20), h("span", { class: "row-main" }, project.name))),
        h("button", { class: "row", type: "button", onclick: () => {
          close();
          projectSheet((project) => go(`/m/p/${project.id}`, true));
        } }, icon("plus", 20), h("span", { class: "row-main" }, t("new project"))),
      ],
    });
  }

  function sessionMenu() {
    if (view.kind !== "session" || !view.task) return;
    const task = view.task;
    openSheet({
      title: titleOf(task) || t("session"),
      build: (close) => [
        h("button", { class: "row", type: "button", onclick: () => {
          close();
          rename();
        } }, icon("edit", 20), h("span", { class: "row-main" }, t("rename"))),
        task.status === "paused"
          ? h("button", { class: "row", type: "button", onclick: async () => {
            close();
            try {
              await post(`/api/tasks/${task.id}/resume`);
            } catch (error) {
              toast(error.localized, "error");
            }
          } }, icon("play", 20), h("span", { class: "row-main" }, t("continue")))
          : null,
        h("button", { class: "row", type: "button", onclick: async () => {
          close();
          await native.copy(`${location.origin}/m/s/${task.public_id}`);
          toast(t("link copied"));
        } }, icon("copy", 20), h("span", { class: "row-main" }, t("copy link"))),
      ],
    });
  }

  function rename() {
    const task = view.task;
    openSheet({
      title: t("rename"),
      build: (close) => {
        const input = h("input", { class: "field", value: titleOf(task), maxlength: "120", enterkeyhint: "done" });
        const save = async () => {
          try {
            view.task = await api(`/api/tasks/${task.id}`, { method: "PATCH", body: { title: input.value } });
            refresh("tasks");
            close();
            paint();
          } catch (error) {
            toast(error.localized, "error");
          }
        };
        input.addEventListener("keydown", (event) => event.key === "Enter" && save());
        setTimeout(() => input.focus(), 250);
        return [input, h("button", { class: "btn primary", type: "button", onclick: save }, t("save"))];
      },
    });
  }

  setInterval(() => !document.hidden && refresh("tasks", "sub", "machines").catch(() => {}), TASK_REFRESH_MS);
  document.addEventListener("visibilitychange", () => !document.hidden && refresh("tasks", "sub", "machines").catch(() => {}));

  return {
    showNew,
    showSession,
    /** The phone's back key: true when something closed. */
    back: () => {
      if (drawer.isOpen()) {
        drawer.close();
        return true;
      }
      return false;
    },
    machineState: () => machineState(account.machines),
  };
}
