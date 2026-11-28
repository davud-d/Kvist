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

// The Apple mark for its sign-in button (filled, unlike the page's stroked icons).
const APPLE_MARK = "M16.37 12.6c-.03-2.7 2.2-4 2.3-4.07-1.25-1.83-3.2-2.08-3.9-2.1-1.66-.17-3.24.97-4.08.97-.85 0-2.15-.95-3.53-.92-1.8.03-3.48 1.06-4.4 2.68-1.9 3.27-.48 8.1 1.35 10.76.9 1.3 1.96 2.75 3.36 2.7 1.35-.06 1.86-.87 3.49-.87 1.62 0 2.08.87 3.5.84 1.45-.02 2.37-1.32 3.25-2.63 1.03-1.5 1.45-2.97 1.47-3.05-.03-.01-2.82-1.08-2.85-4.3zM13.7 4.67c.74-.9 1.24-2.15 1.1-3.4-1.07.05-2.37.72-3.13 1.61-.68.79-1.28 2.06-1.12 3.28 1.19.09 2.41-.6 3.15-1.5z";

function appleMark() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "20");
  svg.setAttribute("height", "20");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", APPLE_MARK);
  path.setAttribute("fill", "currentColor");
  svg.append(path);
  return svg;
}

/** Apple's sheet in the iOS app, Apple's page in a browser; none in the Android app or when the server has it off. */
function appleWay(config) {
  if (native.platform() === "ios") return config?.apple_app ? "sheet" : "";
  return !native.inApp() && config?.apple_web ? "page" : "";
}

async function signInWithApple(way, button) {
  if (way === "page") return location.assign(`/auth/apple/login?next=${encodeURIComponent("/m/")}`);
  button.disabled = true;
  try {
    const { nonce } = await post("/api/auth/apple/nonce");
    const result = await native.appleSignIn(nonce);
    if (!result || result.cancelled) return undefined;
    if (result.error) throw Object.assign(new Error(result.error), { localized: t("apple failed") });
    await post("/api/auth/apple", { identity_token: result.identityToken, authorization_code: result.authorizationCode ?? "",
      nonce, name: result.name ?? "" });
    return boot();
  } catch (error) {
    toast(error.localized ?? t("apple failed"), "error");
    return undefined;
  } finally {
    button.disabled = false;
  }
}

async function signIn() {
  const config = await api("/api/auth/config").catch(() => null);
  const way = appleWay(config);
  // Apple's rules for its button: the Apple mark and "Continue with Apple", as large as the other ways in.
  const apple = way ? h("button", { class: "btn apple wide", type: "button", onclick: (event) => signInWithApple(way, event.currentTarget) },
    appleMark(), t("continue apple")) : null;
  centered(
    h("img", { class: "mark", src: "/m/icon.svg", width: "56", height: "56", alt: "" }),
    h("div", { class: "wordmark big" }, "Kvist Cloud"),
    h("h1", {}, t("gate title")),
    h("p", { class: "lead" }, t("gate lead")),
    apple,
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
  sayIfDeletionCancelled();
  return undefined;
}

// Signing in again during the grace period cancels a deletion (the server leaves a notice); say so once, here.
const SEEN_NOTICE = "kc_seen_deletion_notice";

async function sayIfDeletionCancelled() {
  const list = await api("/api/notifications").catch(() => null);
  const notice = list?.items.find((item) => item.kind === "deletion_cancelled" && !item.read);
  if (!notice || localStorage.getItem(SEEN_NOTICE) === String(notice.id)) return;
  localStorage.setItem(SEEN_NOTICE, String(notice.id));
  toast(t("deletion cancelled"));
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
