// The app shell: header, router, footer. Each screen is a function (root, ctx) → cleanup.
import { api } from "./api.js";
import { append, h, replace } from "./dom.js";
import { detectLocale, locale, rememberLocale, setLocale, t } from "./i18n.js";
import { errorBox } from "./ui.js";
import { routes } from "./routes.js";

const state = { me: null, cleanup: null };

function match(path) {
  for (const route of routes) {
    const found = route.pattern.exec(path);
    if (found) return { route, params: found.slice(1) };
  }
  return null;
}

export function navigate(path, { replace: swap = false } = {}) {
  if (swap) history.replaceState(null, "", path);
  else history.pushState(null, "", path);
  render();
}

function header(active, funnel) {
  // Inside the funnel there is one way forward; the app's navigation would only be a way out.
  const items = state.me && !funnel
    ? [["/dashboard", "nav.dashboard"], ["/new", "nav.newTask"], ["/settings", "nav.settings"],
       ...(state.me.is_admin ? [["/admin", "nav.admin"]] : [])]
    : [];
  return h("header.top", {},
    h("div.wrap.top-row", {},
      h("a.mark", { href: state.me ? "/dashboard" : "/", "data-link": "" },
        h("span.dot", { "aria-hidden": "true" }), t("common.brand"), h("span.sub", { text: t("common.cloud") })),
      h("nav.nav", { "aria-label": "main" },
        items.map(([href, key]) => h("a", { href, "data-link": "", "aria-current": active === href ? "page" : null,
          text: t(key) })),
        h("button.chip", { type: "button", lang: locale() === "ru" ? "en" : "ru", "aria-label": t("common.otherLangName"),
          onclick: switchLanguage, text: t("common.otherLang") }))));
}

function footer() {
  return h("footer.foot", {},
    h("div.wrap.row", {},
      h("a", { href: "https://kvist.site", text: t("common.site") }),
      h("a", { href: "https://github.com/davud-d/Kvist/tree/main/cloud", text: t("common.source") })));
}

async function switchLanguage() {
  const next = locale() === "ru" ? "en" : "ru";
  setLocale(next);
  rememberLocale(next);
  if (state.me) {
    api("/api/me", { method: "PATCH", body: { locale: next } }).catch(() => {});
  }
  render();
}

async function render() {
  if (state.cleanup) state.cleanup();
  state.cleanup = null;
  const path = location.pathname;
  const found = match(path);
  const app = document.getElementById("app");
  const main = h("main.wrap", { id: "main", tabindex: "-1" });
  replace(app, header(found ? found.route.nav : null, found ? found.route.funnel : false), main, footer());
  if (!found) {
    append(main, [errorBox({ code: "not_found" })]);
    return;
  }
  document.title = `${t(found.route.title)} · Kvist Cloud`;
  try {
    state.cleanup = (await found.route.screen(main, { me: state.me, params: found.params, navigate, refreshMe })) || null;
  } catch (error) {
    replace(main, errorBox(error, render));
  }
}

async function refreshMe() {
  try {
    state.me = await api("/api/me");
  } catch {
    state.me = null;
  }
  return state.me;
}

async function boot() {
  setLocale(detectLocale());
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-link]");
    if (!link || event.metaKey || event.ctrlKey || event.shiftKey || link.origin !== location.origin) return;
    event.preventDefault();
    navigate(link.pathname + link.search);
  });
  window.addEventListener("popstate", render);
  await refreshMe();
  if (state.me && state.me.locale && !/kc_lang=/.test(document.cookie)) setLocale(state.me.locale);
  await render();
}

boot();
