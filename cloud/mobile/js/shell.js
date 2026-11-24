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
import { createStatus } from "./status.js";
import { providerMark } from "./mark.js";
import { effortSheet, effortOf } from "./sheets/effort.js";
import { imageGoal } from "./attachments.js";
import { openPopover } from "./popover.js";
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
  const status = createStatus();
  const chat = createChat(feedEl, jump, status.node);
  const homeEl = h("div", { class: "home-wrap" });
  feedEl.prepend(homeEl);

  const chips = h("div", { class: "chips" });
  const composer = createComposer({ onSend: (text, images) => send(text, images), onStop: () => pause(), chips, onPlus: (anchor) => plusMenu(anchor) });
  const bannerEl = h("div", { class: "banner-slot" });
  const dialogEl = h("div", { class: "dialog-slot" });
  const dock = h("footer", { class: "dock" }, bannerEl, dialogEl, composer.node);

  const title = h("div", { class: "t1" });
  const menuDot = h("i", { class: "dot", hidden: true });
  // New session and more: only once there is a session to leave or to act on; an empty start has neither.
  const pill = h("div", { class: "glass-pill", hidden: true },
    h("button", { type: "button", aria: { label: t("new session") }, onclick: () => go("/m/") }, icon("new-chat", 20)),
    h("button", { type: "button", aria: { label: t("more") }, onclick: () => sessionMenu() }, icon("more", 20)));
  const bar = h("header", { class: "bar" },
    h("button", { class: "glass-btn", type: "button", aria: { label: t("sessions") }, onclick: () => drawer.open() }, icon("menu", 20), menuDot),
    h("div", { class: "bar-title" }, h("button", { class: "title-btn", type: "button", "aria-haspopup": "menu", onclick: (event) => projectMenu(event.currentTarget) }, title, icon("chevron-down", 16))),
    pill);

  const drawer = createDrawer({
    onNew: () => go("/m/"),
    onSession: (publicId) => go(`/m/s/${publicId}`),
    onProject: (id) => go(`/m/p/${id}`),
    onAddProject: () => projectSheet((project) => go(`/m/p/${project.id}`)),
    onAccount: accountSheet,
  });

  root.replaceChildren(bar, feedEl, jump, dock, drawer.node);
  // The bars are glass over the conversation, so the conversation leaves room for the dock by its height.
  new ResizeObserver(() => root.style.setProperty("--dock-h", `${dock.offsetHeight}px`)).observe(dock);

  /** {kind: "new", projectId} or {kind: "session", publicId, task, feed, stream, link}. */
  let view = { kind: "new", projectId: "" };

  const link = () => view.link ?? "up";

  function paintBar() {
    const task = view.task;
    const projectId = view.kind === "session" ? task?.project_id : view.projectId;
    title.textContent = account.projects.find((p) => String(p.id) === String(projectId))?.name || "Kvist Cloud";
    // The dot on the menu: another session is waiting for an answer.
    pill.hidden = view.kind !== "session";
    menuDot.hidden = !account.tasks.some((item) => item.status === "waiting_for_user" && item.public_id !== view.publicId);
  }

  /** The menu behind the plus: a photo from the camera or the library, and how hard the model thinks. */
  function plusMenu(anchor) {
    openPopover(anchor, [
      { icon: icon("camera", 20), label: t("camera"), onSelect: () => composer.attachments.takePhoto() },
      { icon: icon("image", 20), label: t("photo"), onSelect: () => composer.attachments.pickPhoto() },
      ...(account.key?.set ? [{ icon: icon("gauge", 20), label: t("think"), detail: t(`effort ${effortOf()}`), onSelect: effortSheet }] : []),
    ]);
  }

  /** The row under the field: the project (a new session only), the model with its mark, how hard it thinks. */
  function paintChips() {
    const key = account.key;
    const model = key?.set ? (key.model || key.label || key.provider) : t("model key");
    fill(chips,
      h("button", { class: "chip small", type: "button", onclick: () => keySheet() },
        key?.set ? providerMark(key.provider, key.label, 14) : null, h("span", {}, model)),
      null);
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
    status.update(feed);
  }

  function paint() {
    paintBar();
    paintChips();
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
    homeEl.replaceChildren(homeView());
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

  async function send(rawText, images = []) {
    const text = imageGoal(rawText, images);
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

  /** Under the project's name: the projects to look through, and a new one (made the way the web workspace makes it). */
  function projectMenu(anchor) {
    const current = view.kind === "session" ? view.task?.project_id : view.projectId;
    openPopover(anchor, [
      { icon: icon("folder", 20), label: t("no project"), on: !current, check: icon("check", 18), onSelect: () => go("/m/", true) },
      ...account.projects.map((project) => ({ icon: icon("folder", 20), label: project.name, on: String(project.id) === String(current), check: icon("check", 18), onSelect: () => go(`/m/p/${project.id}`, true) })),
      { divider: true },
      { icon: icon("plus", 20), label: t("new project"), onSelect: () => projectSheet((project) => go(`/m/p/${project.id}`, true)) },
    ], { below: true });
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
