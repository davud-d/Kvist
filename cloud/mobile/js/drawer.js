// The side drawer: new session, the sessions by day or by project, and the account strip at the bottom with the hours
// left. It is the app's only navigation; everything else is a sheet over the page.

import { account, onAccount as subscribe } from "./account.js";
import { fill, h, icon } from "./dom.js";
import { dayBucket, duration, share } from "./format.js";
import { t } from "./i18n.js";
import { listItem } from "./rules.js";

const DAYS = ["today", "yesterday", "week", "older"];

export function createDrawer({ onNew, onSession, onProject, onAddProject, onAccount }) {
  const scrim = h("div", { class: "drawer-scrim", onclick: () => close() });
  const list = h("div", { class: "drawer-list" });
  const foot = h("button", { class: "drawer-account", type: "button", onclick: () => {
    close();
    onAccount();
  } });
  const tabs = h("div", { class: "segments tabs" });
  const panel = h("aside", { class: "drawer", "aria-label": t("sessions") },
    h("div", { class: "drawer-head" }, h("span", { class: "wordmark" }, "Kvist Cloud"),
      h("button", { class: "icon-btn", type: "button", aria: { label: t("close") }, onclick: () => close() }, icon("close", 20))),
    h("button", { class: "btn primary wide", type: "button", onclick: () => {
      close();
      onNew();
    } }, icon("plus", 18), t("new session")),
    tabs, list, foot);
  const node = h("div", { class: "drawer-root" }, scrim, panel);
  let tab = "days";
  let current = "";
  const openProjects = new Set();

  const row = (item) => h("button", { class: `session${item.publicId === current ? " on" : ""}`, type: "button", onclick: () => {
    close();
    onSession(item.publicId);
  } },
  h("span", { class: "session-title" }, item.title || t("untitled")),
  item.working ? h("span", { class: "pulse", aria: { label: t("working") } }) : item.paused ? icon("pause", 14) : item.failed ? icon("alert", 14) : null);

  function byDays(items) {
    const groups = Object.fromEntries(DAYS.map((d) => [d, []]));
    for (const item of items) groups[dayBucket(item.at)].push(item);
    return DAYS.filter((d) => groups[d].length).flatMap((d) => [h("div", { class: "drawer-label" }, t(`day ${d}`)), ...groups[d].map(row)]);
  }

  function byProjects(items) {
    const rows = account.projects.map((project) => {
      const mine = items.filter((item) => item.projectId === String(project.id));
      const isOpen = openProjects.has(project.id);
      return h("div", { class: "project" },
        h("div", { class: "project-head" },
          h("button", { class: "project-name", type: "button", aria: { expanded: isOpen }, onclick: () => {
            isOpen ? openProjects.delete(project.id) : openProjects.add(project.id);
            paint();
          } }, icon("folder", 18), h("span", {}, project.name), h("small", { class: "dim" }, String(mine.length))),
          h("button", { class: "icon-btn", type: "button", aria: { label: t("new session in", { name: project.name }) }, onclick: () => {
            close();
            onProject(project.id);
          } }, icon("plus", 18))),
        isOpen ? h("div", { class: "project-sessions" }, mine.length ? mine.map(row) : h("div", { class: "dim small pad" }, t("no sessions"))) : null);
    });
    const loose = items.filter((item) => !item.projectId);
    return [
      ...rows,
      h("button", { class: "row", type: "button", onclick: () => {
        close();
        onAddProject();
      } }, icon("plus", 18), h("span", { class: "row-main" }, t("new project"))),
      loose.length ? [h("div", { class: "drawer-label" }, t("no project")), ...loose.map(row)] : null,
    ];
  }

  function paint() {
    const items = account.tasks.map(listItem);
    fill(tabs, [["days", "by day"], ["projects", "projects"]].map(([id, label]) =>
      h("button", { type: "button", class: tab === id ? "on" : "", onclick: () => {
        tab = id;
        paint();
      } }, t(label))));
    fill(list, items.length || tab === "projects" ? (tab === "days" ? byDays(items) : byProjects(items)) : h("p", { class: "dim pad" }, t("no sessions yet")));
    const sub = account.sub;
    const left = sub && sub.status !== "none" && !sub.unmetered ? Math.max(0, sub.balance_seconds) : null;
    fill(foot,
      h("span", { class: "avatar" }, (account.me?.login ?? "?").slice(0, 1).toUpperCase()),
      h("span", { class: "foot-main" },
        h("span", { class: "foot-name" }, account.me?.name || account.me?.login || ""),
        h("small", { class: "dim" }, !sub || sub.status === "none" ? t("no plan") : left === null ? t("unmetered") : `${duration(left)} ${t("left")}`),
        left === null ? null : h("span", { class: "meter thin" }, h("i", { style: { width: `${share(left, sub.plan_seconds + (sub.purchased_seconds ?? 0)) * 100}%` } }))),
      icon("chevron-right", 18));
  }

  function open() {
    paint();
    node.classList.add("open");
    panel.focus?.();
  }
  function close() {
    node.classList.remove("open");
  }

  // A swipe to the left on the panel closes it.
  let startX = null;
  panel.addEventListener("touchstart", (event) => (startX = event.touches[0].clientX), { passive: true });
  panel.addEventListener("touchend", (event) => {
    if (startX !== null && startX - event.changedTouches[0].clientX > 70) close();
    startX = null;
  });

  subscribe(() => node.classList.contains("open") && paint());

  return {
    node,
    open,
    close,
    isOpen: () => node.classList.contains("open"),
    setCurrent(publicId) {
      current = publicId ?? "";
    },
  };
}
