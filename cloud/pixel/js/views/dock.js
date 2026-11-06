// The left rail: repositories (and the empty folder) that open into their tasks, an "add repository" button and,
// bottom left, the gear that opens the settings. Picking a task shows it (its stream opens when it is used);
// a running one carries a dot, and its cross only hides it from the page.

import { closeTab, newAgent, openSession, refreshSessions, toggleProject } from "../actions.js";
import { account, onAccount, projectName, workspaceOf } from "../cloud.js";
import { fill, frameThrottle, h, icon, keyedList } from "../dom.js";
import { t } from "../i18n.js";
import { enter } from "../motion.js";
import { buildTree } from "../projects.js";
import { store, subscribe } from "../store.js";
import { openProjectDialog } from "./project-dialog.js";
import { toggleSettings } from "./settings.js";

function sessionRow(project, session) {
  const dot = h("span", { class: `state-dot ${session.state ?? "hollow"}` });
  const stop = session.agentId
    ? h("button", { class: "sess-close", type: "button", title: t("close tab"), onclick: (event) => { event.stopPropagation(); closeTab(session.agentId); } }, icon("close", 10))
    : null;
  const row = h(
    "button",
    { class: `sess${session.active ? " on" : ""}${session.attention ? " attention" : ""}`, type: "button", title: session.title },
    dot,
    h("span", { class: "sess-title" }, session.title || t("new task")),
    stop,
  );
  row.addEventListener("click", () => openSession(project.workspace, session.session, session.title));
  return row;
}

function paintProject(el, project) {
  el.classList.toggle("open", project.open);
  const chev = icon("chev", 12);
  chev.classList.add("chev");
  fill(
    el.querySelector(".proj-head"),
    chev,
    icon("folder", 16),
    h("span", { class: "proj-name", title: project.name }, project.name),
    project.running ? h("span", { class: "state-dot running" }) : null,
    project.sessions.length ? h("span", { class: "proj-count" }, String(project.sessions.length)) : null,
  );
  const create = h("button", { class: "sess new", type: "button" }, icon("plus", 12), h("span", { class: "sess-title" }, t("new task")));
  create.addEventListener("click", () => newAgent(project.workspace));
  // The older tasks have no project to start a new one in.
  fill(el.querySelector(".proj-body > div"), project.sessions.map((session) => sessionRow(project, session)), project.workspace ? create : null);
}

function makeProject(project) {
  const head = h("button", { class: "proj-head", type: "button", "aria-expanded": String(project.open) });
  head.addEventListener("click", () => toggleProject(project.workspace));
  const el = h("div", { class: "proj" }, head, h("div", { class: "proj-body" }, h("div")));
  paintProject(el, project);
  return el;
}

// The rail lists the person's projects, and the older tasks' group only while there are such tasks.
function workspaces() {
  const own = account.projects.map((project) => String(project.id));
  return account.tasks.some((task) => workspaceOf(task) === "") ? [...own, ""] : own;
}

export function mountDock(root) {
  const list = h("div", { class: "tree" });
  const add = h("button", { class: "btn wide", type: "button" }, icon("plus", 14), h("span", {}, t("add project")));
  const gear = h("button", { class: "btn wide", type: "button", title: t("settings"), "aria-haspopup": "dialog" }, icon("gear", 16), h("span", {}, t("settings")));
  root.classList.add("px");
  root.append(h("div", { class: "section-label" }, t("projects")), list, h("div", { class: "dock-foot" }, add, gear));

  add.addEventListener("click", openProjectDialog);
  gear.addEventListener("click", () => toggleSettings(gear));

  const draw = keyedList(list, {
    key: (project) => project.workspace,
    make: makeProject,
    update: paintProject,
    onEnter: (el) => enter(el, { from: "translateX(-12px)" }),
    // A cheap fingerprint: what the rows show, so an unchanged folder is not repainted.
    rev: (project) => JSON.stringify([project.open, project.running, project.sessions.map((s) => [s.session, s.title, s.state, s.active, s.attention, s.closed])]),
  });
  const redraw = frameThrottle(() => {
    const tree = buildTree({
      projects: workspaces(),
      nameOf: (workspace) => projectName(workspace, account.projects, t("no project")),
      agents: store.order.map((id) => store.agents.get(id)),
      listings: store.listings,
      expanded: store.prefs.expanded,
      activeId: store.activeId,
    });
    draw(tree);
    for (const project of tree) if (project.open && !store.listings.has(project.workspace)) refreshSessions(project.workspace);
  });
  subscribe(redraw);
  onAccount(redraw);
  redraw();
}
