// Start-up and addresses. /m/ is a new session, /m/s/<id> a session, /m/p/<id> a new session in a project.
// Before any of them: who is this (sign in), have they accepted the terms, and what does their account hold.

import { account, load } from "./account.js";
import { api, post } from "./api.js";
import { h, icon } from "./dom.js";
import { lang, t } from "./i18n.js";
import * as native from "./native.js";
import { closeTop, toast } from "./sheet.js";
import { startShell } from "./shell.js";
import { startStars } from "./stars.js";
import { watchViewport } from "./viewport.js";

const screen = document.getElementById("screen");
let shell = null;

/** The address a page address means: {kind: "session", id} | {kind: "project", id} | {kind: "new"}. */
export function parsePath(pathname) {
  const session = pathname.match(/^\/m\/s\/([A-Za-z0-9_-]{6,40})\/?$/);
  if (session) return { kind: "session", id: session[1] };
  const project = pathname.match(/^\/m\/p\/(\d+)\/?$/);
  if (project) return { kind: "project", id: project[1] };
  return { kind: "new", id: "" };
}

function go(path, replace = false) {
  if (location.pathname !== path) history[replace ? "replaceState" : "pushState"](null, "", path);
  route();
}

function route() {
  const where = parsePath(location.pathname);
  if (where.kind === "session") shell.showSession(where.id);
  else shell.showNew(where.kind === "project" ? where.id : "");
}

// -- screens before the app -------------------------------------------------------------------------------------

function centered(...children) {
  screen.replaceChildren(h("div", { class: "gate" }, ...children));
}

function signIn() {
  centered(
    h("img", { class: "mark", src: "/m/icon.svg", width: "56", height: "56", alt: "" }),
    h("div", { class: "wordmark big" }, "Kvist Cloud"),
    h("h1", {}, t("gate title")),
    h("p", { class: "lead" }, t("gate lead")),
    h("a", { class: "btn primary wide", href: `/auth/github/login?next=${encodeURIComponent("/m/")}`, onclick: async (event) => {
      event.preventDefault();
      const href = event.currentTarget.getAttribute("href");
      // In the apps the system browser signs in (GitHub's page, and Google's behind it, will not run in a web view)
      // and the app reloads this page signed in; anywhere else it is the ordinary redirect.
      if (native.inApp() && (await native.signIn())) return;
      location.assign(href);
    } }, icon("github", 20), t("continue github")),
    h("p", { class: "fine" }, t("gate fine")));
}

function terms(then) {
  const a = h("input", { type: "checkbox", id: "t1" });
  const b = h("input", { type: "checkbox", id: "t2" });
  const accept = h("button", { class: "btn primary wide", type: "button", disabled: true }, t("accept"));
  const sync = () => (accept.disabled = !(a.checked && b.checked));
  a.addEventListener("change", sync);
  b.addEventListener("change", sync);
  accept.addEventListener("click", async () => {
    accept.disabled = true;
    try {
      await post("/api/me/terms", { terms: true, privacy: true });
      then();
    } catch (error) {
      toast(error.localized, "error");
      sync();
    }
  });
  const legal = (path, label) => h("a", { href: path, onclick: (event) => {
    event.preventDefault();
    native.open(`${location.origin}${path}`);
  } }, label);
  centered(
    h("div", { class: "wordmark big" }, "Kvist Cloud"),
    h("h1", {}, t("terms title")),
    h("label", { class: "check" }, a, h("span", {}, t("i accept"), " ", legal("/terms", t("terms")))),
    h("label", { class: "check" }, b, h("span", {}, t("i accept"), " ", legal("/privacy", t("privacy")))),
    accept);
}

function problem(titleKey, bodyKey, retry) {
  centered(
    h("h1", {}, t(titleKey)),
    h("p", { class: "lead" }, t(bodyKey)),
    h("button", { class: "btn primary wide", type: "button", onclick: retry }, t("try again")));
}

// -- boot ---------------------------------------------------------------------------------------------------------------

async function boot() {
  let me;
  try {
    me = await api("/api/me");
  } catch (error) {
    if (error.status === 401) return signIn();
    return problem("offline title", "offline body", boot);
  }
  account.me = me;
  lang.adopt(me.locale);
  if (me.ban) return problem("banned title", "banned body", boot);
  if (!me.terms_accepted) return terms(boot);
  try {
    await load();
  } catch (error) {
    if (error.status === 401) return signIn();
    return problem("offline title", "offline body", boot);
  }
  screen.replaceChildren();
  shell = startShell(screen, { go, onAuth: signIn });
  window.addEventListener("popstate", route);
  route();
  return undefined;
}

// The phone's back key (the app calls this): a sheet closes first, then the drawer, then a session goes home.
window.kvistBack = () => {
  if (closeTop() || shell?.back()) return true;
  if (shell && parsePath(location.pathname).kind !== "new") {
    go("/m/");
    return true;
  }
  return false;
};

watchViewport();
startStars();
boot();
