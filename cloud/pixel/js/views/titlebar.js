// The top strip: the brand, the two numbers the person pays attention to (hours left and the server's state),
// and the buttons that matter. It is a web page, so there is no window frame to draw.

import { account, displayState, duration, onAccount, share } from "../cloud.js";
import { basename, frameThrottle, h, icon } from "../dom.js";
import { chooseLanguage, lang, t } from "../i18n.js";
import { active, setMode, setPref, store, subscribe } from "./../store.js";

const BLOCKS = 12;

export const machineDot = (status) => ({ ready: "idle", asleep: "hollow", working: "running", waiting_for_you: "asks", creating: "starting", waking: "starting", error: "gone", none: "hollow" })[status] ?? "hollow";

function serverSpec(machine) {
  return machine ? `${machine.vcpu} vCPU · ${machine.memory_gb} GB` : "";
}

export function mountTitlebar(root) {
  const hours = h("div", { class: "hours-meter", title: t("hours left") });
  const server = h("div", { class: "server-chip chip" });
  const session = h("span", { class: "session-name" });
  const crumb = h("div", { class: "crumb" }, hours, server, session);
  const overview = h("button", { class: "btn", type: "button", title: `${t("overview")} (Ctrl+\\)` }, icon("grid", 14));
  const inspector = h("button", { class: "btn", type: "button", title: t("inspector") }, icon("panel", 14));
  const language = h("button", { class: "btn lang", type: "button", title: t("language"), onclick: () => chooseLanguage(lang.current === "ru" ? "en" : "ru") }, lang.current === "ru" ? "EN" : "RU");
  root.append(
    h("div", { class: "brand" }, h("span", { class: "squirrel small" }), h("span", { class: "brand-name" }, "KVIST"), h("span", { class: "brand-sub" }, "CLOUD")),
    crumb,
    h("div", { class: "title-actions" }, language, overview, inspector),
  );
  overview.addEventListener("click", () => setMode(store.mode === "overview" ? "chat" : "overview"));
  inspector.addEventListener("click", () => setPref("inspector", !store.prefs.inspector));

  const redraw = frameThrottle(() => {
    const sub = account.sub;
    const known = sub && sub.status !== "none";
    const filled = known ? Math.round(share(sub.balance_seconds, sub.plan_seconds) * BLOCKS) : 0;
    hours.replaceChildren(
      h("span", { class: "meter-label" }, known ? duration(sub.balance_seconds) : t("no plan")),
      known ? h("span", { class: "meter-bar" }, ...Array.from({ length: BLOCKS }, (_, i) => h("i", { class: i < filled ? "on" : "" }))) : null,
    );
    hours.title = known ? `${t("hours left")}: ${duration(sub.balance_seconds)} ${t("of")} ${duration(sub.plan_seconds)}` : "";
    const status = displayState(account.machine);
    server.replaceChildren(h("span", { class: `state-dot ${machineDot(status)}` }), h("span", {}, `${t("status " + status)}${account.machine ? ` · ${serverSpec(account.machine)}` : ""}`));
    server.title = `${t("server")}: ${t("status " + status)}`;
    const agent = active();
    session.textContent = agent ? `${basename(agent.workspace)}${agent.title ? ` — ${agent.title}` : ""}` : "";
    overview.classList.toggle("on", store.mode === "overview");
    inspector.classList.toggle("on", store.prefs.inspector);
  });
  subscribe(redraw);
  onAccount(redraw);
  redraw();
}
