// The account: the hours left and when they renew, the machine, the model, the language, the legal pages, sign out.
// The one place that answers "what am I paying for and what is left".

import { account, refresh } from "../account.js";
import { post, api } from "../api.js";
import { h, icon } from "../dom.js";
import { duration, share } from "../format.js";
import { lang, setLang, t } from "../i18n.js";
import * as native from "../native.js";
import { machineState } from "../rules.js";
import { openSheet, toast } from "../sheet.js";
import { linkGithubHref, paySheet, plansSheet } from "./billing.js";
import { keySheet } from "./key.js";

export function hoursCard(sub, closeSheet) {
  // A store app shows what the account has and sells nothing (native.storeBuild).
  const sells = !native.storeBuild();
  if (!sub || sub.status === "none") {
    return h("div", { class: "card hours" }, h("div", { class: "dim" }, t("no plan")),
      sells ? h("button", { class: "btn primary", type: "button", onclick: plansSheet }, t("choose plan")) : null);
  }
  if (sub.unmetered) return h("div", { class: "card hours" }, h("div", { class: "hours-big" }, t("unmetered")), h("div", { class: "dim" }, t("plan named", { name: t(`plan ${sub.plan}`) })));
  const left = Math.max(0, sub.balance_seconds);
  const ratio = share(left, sub.plan_seconds + (sub.purchased_seconds ?? 0));
  const renews = sub.period_end ? new Date(sub.period_end).toLocaleDateString(document.documentElement.lang, { day: "numeric", month: "long" }) : "";
  return h("div", { class: "card hours" },
    h("div", { class: "hours-line" }, h("span", { class: "hours-big" }, duration(left)), h("span", { class: "dim" }, t("left"))),
    h("div", { class: "meter", role: "progressbar", aria: { valuemin: 0, valuemax: 100, valuenow: Math.round(ratio * 100) } }, h("i", { style: { width: `${ratio * 100}%` } })),
    h("div", { class: "hours-meta dim" },
      h("span", {}, t("plan named", { name: t(`plan ${sub.plan}`) })),
      renews ? h("span", {}, sub.status === "trialing" ? t("trial ends", { date: renews }) : t("renews", { date: renews })) : null),
    sells ? h("div", { class: "btn-row" },
      h("button", { class: "btn", type: "button", onclick: plansSheet }, t("plans")),
      h("button", { class: "btn", type: "button", onclick: plansSheet }, t("buy hours"))) : null,
    searchesPart(account.searches, closeSheet, sells));
}

/** Under the hours: the free web searches left, the bought ones, and the pack's button. Paying closes the account
 * sheet too, so it is opened again with the new count rather than left showing the old one. */
function searchesPart(usage, closeSheet, sells) {
  if (!usage?.enabled) return null;
  const out = (usage.day === 0 || usage.week === 0) && usage.bought === 0;
  const left = t("searches left", { day: usage.day, perDay: usage.per_day, week: usage.week, perWeek: usage.per_week });
  return h("div", { class: "searches" },
    out ? h("p", { class: "searches-out", role: "status" }, t(sells ? "searches out" : "searches out store")) : null,
    h("div", { class: "hours-meta dim" },
      h("span", {}, left),
      usage.bought > 0 ? h("span", {}, t("searches bought", { n: usage.bought })) : null),
    sells ? h("div", { class: "btn-row" },
      h("button", { class: `btn${out ? " primary" : ""}`, type: "button", onclick: () => paySheet("searches", closeSheet) },
        t("buy searches"))) : null);
}

function machineRow() {
  const state = machineState(account.machines);
  const machine = account.machines[0];
  const action = (() => {
    if (!machine || machine.suspended) return null;
    if (state === "asleep") return { label: t("wake"), run: () => post(`/api/machines/${machine.id}/wake`) };
    if (state === "ready") return { label: t("sleep"), run: () => post(`/api/machines/${machine.id}/sleep`) };
    return null;
  })();
  return h("div", { class: "row static" },
    icon("server", 20),
    h("span", { class: "row-main" }, h("span", {}, t("machine")), h("small", { class: "dim" }, t(`machine ${state}`))),
    action ? h("button", { class: "btn small", type: "button", onclick: async (event) => {
      event.currentTarget.disabled = true;
      try {
        await action.run();
        setTimeout(() => refresh("machines"), 1500);
      } catch (error) {
        toast(error.localized, "error");
      }
    } }, action.label) : null);
}

/** Only for an account made with Apple and no GitHub yet: connecting it opens repositories and the trial. */
function githubRow() {
  if (account.me?.github !== false) return null;
  return h("div", { class: "row static" },
    icon("github", 20),
    h("span", { class: "row-main" }, h("span", {}, t("github")), h("small", { class: "dim" }, t("github not connected"))),
    native.inApp() ? null : h("a", { class: "btn small", href: linkGithubHref }, t("connect github")));
}

export function accountSheet() {
  openSheet({
    title: account.me?.name || account.me?.login || t("account"),
    variant: "tall",
    build: (close) => [
      hoursCard(account.sub, close),
      h("div", { class: "group" },
        machineRow(),
        githubRow(),
        h("button", { class: "row", type: "button", onclick: () => keySheet() },
          icon("key", 20),
          h("span", { class: "row-main" }, h("span", {}, t("model key")),
            h("small", { class: "dim" }, account.key?.set ? `${account.key.label || account.key.provider}${account.key.model ? ` · ${account.key.model}` : ""} · •••• ${account.key.last4}` : t("not set"))),
          icon("chevron-right", 18))),
      h("div", { class: "group" },
        h("div", { class: "row static" }, icon("sliders", 20), h("span", { class: "row-main" }, t("language")),
          h("div", { class: "segments inline" }, ["en", "ru"].map((code) => h("button", { type: "button", class: lang.current === code ? "on" : "",
            onclick: async () => {
              setLang(code);
              api("/api/me", { method: "PATCH", body: { locale: code } }).catch(() => {});
              close();
              location.reload();
            } }, code.toUpperCase())))),
        h("a", { class: "row", href: "/terms", onclick: (event) => { event.preventDefault(); native.open(`${location.origin}/terms`); } }, icon("file", 20), h("span", { class: "row-main" }, t("terms")), icon("external", 16)),
        h("a", { class: "row", href: "/privacy", onclick: (event) => { event.preventDefault(); native.open(`${location.origin}/privacy`); } }, icon("lock", 20), h("span", { class: "row-main" }, t("privacy")), icon("external", 16))),
      h("button", { class: "row danger", type: "button", onclick: async () => {
        await fetch("/auth/logout", { method: "POST", headers: { "X-Kvist-Client": "1" }, credentials: "same-origin" });
        location.assign("/m/");
      } }, icon("logout", 20), h("span", { class: "row-main" }, t("sign out"))),
      h("button", { class: "link-danger", type: "button", onclick: deleteSheet }, t("delete account")),
    ],
  });
}

/** Asked twice: this sheet is the second question. The account is erased only after the grace period, and signing in
 * again before then keeps it; the sheet says both before and after. */
function deleteSheet() {
  openSheet({
    title: t("delete account q"),
    build: (close, body) => {
      const go = h("button", { class: "btn danger wide", type: "button", onclick: async () => {
        go.disabled = true;
        try {
          const { erase_after: after } = await post("/api/me/delete", { confirm: true });
          const date = new Date(after).toLocaleDateString(document.documentElement.lang, { day: "numeric", month: "long", year: "numeric" });
          body.replaceChildren(
            h("p", { class: "lead" }, t("delete account done", { date })),
            h("button", { class: "btn primary wide", type: "button", onclick: () => location.assign("/m/") }, t("accept")));
        } catch (error) {
          toast(error.localized, "error");
          go.disabled = false;
        }
      } }, t("delete account yes"));
      return [
        h("p", { class: "lead" }, t("delete account body")),
        h("div", { class: "stack" }, go, h("button", { class: "btn wide", type: "button", onclick: close }, t("cancel"))),
      ];
    },
  });
}
