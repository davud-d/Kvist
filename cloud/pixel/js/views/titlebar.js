// The top strip: the brand, the two numbers the person pays attention to (hours left and the server's state),
// and the buttons that matter. It is a web page, so there is no window frame to draw.

import { account, displayState, duration, onAccount } from "../cloud.js";
import { frameThrottle, h, icon } from "../dom.js";
import { chooseLanguage, lang, t } from "../i18n.js";
import { active, setMode, setPref, store, subscribe } from "../store.js";
import { THEMES } from "../theme.js";

export const machineDot = (status) => ({ ready: "idle", asleep: "hollow", working: "running", waiting_for_you: "asks", creating: "starting", waking: "starting", error: "gone", none: "hollow" })[status] ?? "hollow";

function serverSpec(machine) {
  return machine ? `${machine.vcpu} vCPU · ${machine.memory_gb} GB` : "";
}

export function mountTitlebar(root) {
  const hours = h("div", { class: "hours-meter", title: t("hours left") });
  const server = h("div", { class: "server-chip chip" });
  const app = document.getElementById("app");
  const projects = h("button", { class: "btn projects-toggle", type: "button", title: t("projects"), "aria-controls": "dock", "aria-expanded": "false" }, icon("menu"));
  const overview = h("button", { class: "btn", type: "button", title: `${t("overview")} (Ctrl+\\)`, "aria-label": t("overview"), "aria-controls": "overview" },
    icon("grid"), h("span", { class: "utility-label" }, t("overview")));
  const inspector = h("button", { class: "btn", type: "button", title: t("inspector"), "aria-controls": "inspector" },
    icon("panel"), h("span", { class: "utility-label" }, t("inspector")));
  const language = h("button", { class: "btn", type: "button", title: t("language"), "aria-label": t("language"), onclick: () => chooseLanguage(lang.current === "ru" ? "en" : "ru") }, lang.current === "ru" ? "EN" : "RU");
  const theme = h("select", { class: "theme-select", "aria-label": t("theme"), title: t("theme"), onchange: (event) => setPref("theme", event.target.value) },
    THEMES.map((value) => h("option", { value }, t(value))));
  root.append(
    projects,
    h("a", { class: "brand", href: "/dashboard", "aria-label": "Kvist Cloud" },
      h("span", { class: "brand-mark", "aria-hidden": "true" }, "K"), h("span", { class: "brand-text" }, "Kvist Cloud")),
    h("div", { class: "crumb" }, server, hours),
    h("div", { class: "title-actions" }, theme, language, overview, inspector),
  );

  function showProjects(open) {
    app.classList.toggle("show-projects", open);
    projects.setAttribute("aria-expanded", String(open));
  }
  projects.addEventListener("click", () => {
    const opening = !app.classList.contains("show-projects");
    if (opening && store.prefs.inspector) setPref("inspector", false);
    showProjects(opening);
  });
  overview.addEventListener("click", () => setMode(store.mode === "overview" ? "chat" : "overview"));
  inspector.addEventListener("click", () => {
    showProjects(false);
    setPref("inspector", !store.prefs.inspector);
  });
  subscribe((change) => {
    if (change.kind === "active" || change.kind === "mode" || (change.kind === "prefs" && change.key === "inspector" && store.prefs.inspector)) {
      showProjects(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || event.defaultPrevented || !app.classList.contains("show-projects")) return;
    event.preventDefault();
    showProjects(false);
    projects.focus();
  });

  const redraw = frameThrottle(() => {
    const sub = account.sub;
    hours.hidden = !sub;
    hours.textContent = sub?.status && sub.status !== "none" ? duration(sub.balance_seconds) : t("no plan");
    hours.title = t("hours left");
    const status = displayState(account.machine);
    server.replaceChildren(h("span", { class: `state-dot ${machineDot(status)}` }),
      h("span", {}, t("status " + status)),
      account.machine ? h("span", { class: "server-spec" }, `· ${serverSpec(account.machine)}`) : null);
    server.title = `${t("server")}: ${t("status " + status)}`;
    theme.value = store.prefs.theme;
    overview.classList.toggle("on", store.mode === "overview");
    overview.setAttribute("aria-expanded", String(store.mode === "overview"));
    inspector.classList.toggle("on", store.prefs.inspector);
    inspector.setAttribute("aria-expanded", String(store.prefs.inspector));
  });
  // Not throttled by a frame: a background tab gets no animation frames, and that is where the tab's name matters.
  const paintPageTitle = () => {
    const name = active()?.title;
    document.title = name ? `Kvist Cloud | ${name}` : "Kvist Cloud";
  };
  subscribe(paintPageTitle);
  paintPageTitle();
  subscribe(redraw);
  onAccount(redraw);
  redraw();
}
