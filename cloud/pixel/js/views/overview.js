// Switch between the active task and the list of open tasks.

import { activate, newAgent } from "../actions.js";
import { openProjectDialog } from "./project-dialog.js";
import { account, projectName } from "../cloud.js";
import { fill, frameThrottle, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { enter } from "../motion.js";
import { summary } from "../reducer.js";
import { active, setMode, store, subscribe } from "../store.js";

function lastWords(agent) {
  if (agent.status.text && agent.state === "running") return agent.status.text;
  const last = [...agent.items].reverse().find((item) => ["reply", "final", "user"].includes(item.kind));
  return last ? last.text.replace(/[`*#>_-]/g, "").slice(0, 140) : "";
}

function tile(agent, index) {
  const state = summary(agent);
  const picture = agent.screen ? h("img", { class: "tile-screen", src: agent.screen.stream_url, alt: "" }) : null;
  const el = h(
    "div",
    { class: `tile${agent.id === store.activeId ? " on" : ""}`, role: "button", tabindex: "0", data: { id: agent.id } },
    h("div", { class: "tile-head" }, h("span", { class: `state-dot ${state}` }), h("span", { class: "tile-title" }, agent.title || t("new task")), h("span", { class: "tile-key" }, index < 9 ? String(index + 1) : "")),
    h("div", { class: "tile-sub" }, `${t(state)} · ${projectName(agent.workspace, account.projects, t("no project"))}`),
    picture,
    h("div", { class: "tile-text" }, lastWords(agent)),
    h("div", { class: "tile-foot" }, [...agent.helpers.values()].some((x) => x.state === "running") ? h("span", { class: "badge" }, icon("bot", 10), t("helpers")) : null, agent.changed.size ? h("span", { class: "badge" }, icon("file", 10), agent.changed.size) : null),
  );
  el.addEventListener("click", () => openTask(agent.id));
  el.addEventListener("keydown", (event) => ["Enter", " "].includes(event.key) && (event.preventDefault(), openTask(agent.id)));
  return el;
}

function openTask(id) {
  activate(id);
  setMode("chat");
}

export function mountOverview(root, pane) {
  const grid = h("div", { class: "tiles" });
  const plus = h("button", { class: "tile tile-new", type: "button" }, icon("plus", 24), h("span", {}, t("new task")));
  root.append(grid);
  // Another task in the same repository as the one in front.
  plus.addEventListener("click", () => {
    const workspace = active()?.workspace || String(account.projects[0]?.id ?? "");
    setMode("chat");
    if (workspace) newAgent(workspace);
    else openProjectDialog();
  });

  const paint = frameThrottle(() => {
    if (store.mode !== "overview") return;
    fill(grid, ...store.order.map((id, i) => tile(store.agents.get(id), i)), plus);
  });

  document.addEventListener("keydown", (event) => {
    if (store.mode !== "overview") return;
    const tiles = [...grid.querySelectorAll(".tile[data-id]")];
    const at = tiles.findIndex((el) => el === document.activeElement);
    if (event.key === "Escape") setMode("chat");
    else if (/^[1-9]$/.test(event.key) && tiles[Number(event.key) - 1]) tiles[Number(event.key) - 1].click();
    else if (event.key === "ArrowRight" || event.key === "ArrowDown") tiles[Math.min(tiles.length - 1, at + 1)]?.focus();
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") tiles[Math.max(0, at - 1)]?.focus();
    else return;
    event.preventDefault();
  });

  subscribe((change) => {
    const shown = store.mode === "overview";
    if (change.kind === "mode") {
      pane.hidden = shown;
      root.hidden = !shown;
      if (shown) enter(root);
    }
    if (shown) paint();
  });
}
