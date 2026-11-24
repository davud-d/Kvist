// The rail: the only thing always on screen besides the work itself. Home (start work, see what changed),
// every task (search), and at the bottom the server and the settings. Each has a word under its icon, because
// an icon alone does not say "server". The home button carries the one dot that says something needs a look:
// yellow when a task waits for the person, blue when one finished since they last looked.

import { railSignal } from "../attention.js";
import { goHome } from "../actions.js";
import { account, displayState, onAccount } from "../cloud.js";
import { fill, frameThrottle, h, icon } from "../dom.js";
import { t } from "../i18n.js";
import { setSheet, store, subscribe } from "../store.js";
import { openPalette } from "./palette.js";
import { toggleSettings } from "./settings.js";

const MACHINE_DOT = { ready: "idle", working: "running", waiting_for_you: "asks", creating: "starting", waking: "starting", error: "gone" };

function railButton(iconName, label, onclick, extra = {}) {
  const dot = h("span", { class: "rail-dot" });
  // Home wears Kvist's own mark (branding/icon.svg), not a line icon.
  const mark = iconName === "kvist" ? h("img", { src: "/workspace/icon.svg", alt: "", width: 32, height: 32 }) : icon(iconName, 20);
  const button = h("button", { class: "rail-btn", type: "button", onclick, ...extra }, h("span", { class: "rail-icon" }, mark, dot), h("span", { class: "rail-label" }, label));
  button.dot = dot;
  return button;
}

export function mountRail(root) {
  const home = railButton("kvist", t("home"), () => goHome(), { title: `${t("home")} (Ctrl+\\)` });
  home.classList.add("rail-home");
  const tasks = railButton("list", t("tasks"), () => openPalette(), { title: `${t("all tasks")} (Ctrl+K)` });
  const server = railButton("server", t("server"), () => setSheet({ kind: "server" }));
  const gear = railButton("gear", t("settings"), () => toggleSettings(gear), { "aria-haspopup": "dialog" });
  root.append(h("div", { class: "rail-top" }, home, tasks), h("div", { class: "rail-bottom" }, server, gear));

  const redraw = frameThrottle(() => {
    home.classList.toggle("on", store.mode === "home");
    home.setAttribute("aria-current", store.mode === "home" ? "page" : "false");
    home.dot.className = `rail-dot ${railSignal(account.tasks, store.seen)}`;
    const status = displayState(account.machine);
    server.dot.className = `rail-dot machine ${MACHINE_DOT[status] ?? "quiet"}`;
    server.title = `${t("server")}: ${t(`status ${status}`)}`;
    server.classList.toggle("on", store.sheet?.kind === "server");
    fill(server.querySelector(".rail-label"), t("server"));
  });
  subscribe(redraw);
  onAccount(redraw);
  redraw();
}
