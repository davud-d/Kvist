// Zoom out: every agent as a live tile. Picking one zooms the chat back in
// from that tile's place; leaving the chat zooms it out into the grid.

import { activate, newAgent } from "../actions.js";
import { repoLabel } from "../cloud.js";
import { fill, frameThrottle, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { animate, enter, finished, zoomIn, zoomOut } from "../motion.js";
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
    { class: `tile px${agent.id === store.activeId ? " on" : ""}`, role: "button", tabindex: "0", data: { id: agent.id } },
    h("div", { class: "tile-head" }, h("span", { class: `state-dot ${state}` }), h("span", { class: "tile-title" }, agent.title || t("new task")), h("span", { class: "tile-key" }, index < 9 ? String(index + 1) : "")),
    h("div", { class: "tile-sub" }, `${t(state)} · ${repoLabel(agent.workspace) || t("empty folder")}`),
    picture,
    h("div", { class: "tile-text" }, lastWords(agent)),
    h("div", { class: "tile-foot" }, [...agent.helpers.values()].some((x) => x.state === "running") ? h("span", { class: "badge" }, icon("bot", 10), t("helpers")) : null, agent.changed.size ? h("span", { class: "badge" }, icon("file", 10), agent.changed.size) : null),
  );
  el.addEventListener("click", () => zoomInto(agent.id, el));
  el.addEventListener("keydown", (event) => event.key === "Enter" && zoomInto(agent.id, el));
  return el;
}

let rectOfOpened = null;

async function zoomInto(id, el) {
  rectOfOpened = el.getBoundingClientRect();
  await activate(id);
  setMode("chat");
}

export function mountOverview(root, pane) {
  const grid = h("div", { class: "tiles" });
  const plus = h("button", { class: "tile tile-new px", type: "button" }, icon("plus", 24), h("span", {}, t("new task")));
  root.append(grid);
  // Another task in the same repository as the one in front.
  plus.addEventListener("click", () => {
    newAgent(active()?.workspace ?? "");
    setMode("chat");
  });

  const paint = frameThrottle(() => {
    if (store.mode !== "overview") return;
    fill(grid, ...store.order.map((id, i) => tile(store.agents.get(id), i)), plus);
  });

  async function show() {
    root.hidden = false;
    paint();
    const [first] = [...grid.children];
    const target = grid.querySelector(".tile.on") ?? first;
    // The chat lens moves out into its tile while the grid blooms around it.
    const from = target?.getBoundingClientRect();
    const leaving = from ? zoomOut(pane, from) : null;
    await finished(leaving);
    pane.hidden = true;
    // A fill-forwards animation would keep overriding the pane once it is back.
    leaving?.cancel();
    [...grid.children].forEach((el, i) => enter(el, { from: "scale(0.9)", delay: i * 30 }));
  }

  async function hide() {
    pane.hidden = false;
    if (rectOfOpened) animate(grid, [{ opacity: 1 }, { opacity: 0 }], { curve: "in", ms: 160, fill: "forwards" });
    if (rectOfOpened) await finished(zoomIn(pane, rectOfOpened));
    rectOfOpened = null;
    root.hidden = true;
    grid.style.opacity = "";
  }

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

  let shown = false;
  subscribe((change) => {
    if (change.kind === "mode") {
      const on = store.mode === "overview";
      if (on && !shown) show();
      else if (!on && shown) hide();
      shown = on;
    } else if (store.mode === "overview") paint();
  });
}
