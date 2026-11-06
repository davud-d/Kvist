// The hours: what is left of the plan, what this task has used, and where they went.
// Hours run only while the agent works (cloud/docs/ARCHITECTURE.md, "Hours ledger rules").

import { account, duration, share } from "../cloud.js";
import { fill, h, icon } from "../dom.js";
import { lang, t } from "../i18n.js";

const BLOCKS = 24;
const SHOWN = 14;

function when(iso) {
  return iso ? new Date(iso).toLocaleString(lang.current === "ru" ? "ru-RU" : "en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
}

const sign = (seconds) => (Math.abs(seconds) < 60 ? "" : seconds < 0 ? "−" : "+");

function feedRow(entry) {
  const what = entry.kind === "usage" && entry.goal ? entry.goal.split("\n")[0] : t(`kind ${entry.kind}`);
  return h("div", { class: `feed-row ${entry.seconds < 0 ? "minus" : "plus"}`, title: what },
    h("span", { class: "feed-what" }, what),
    h("b", {}, `${sign(entry.seconds)}${duration(Math.abs(entry.seconds))}`),
    h("span", { class: "feed-when" }, when(entry.at)));
}

export const hours = {
  id: "hours",
  account: true,
  title: "hours",
  icon: "clock",
  badge: () => "",
  render(agent, el) {
    const sub = account.sub;
    if (!sub || sub.status === "none") {
      fill(el, h("p", { class: "dim pad" }, t("no plan")), h("a", { class: "btn primary", href: "/start" }, t("start trial")));
      return;
    }
    const filled = Math.round(share(sub.balance_seconds, sub.plan_seconds) * BLOCKS);
    fill(
      el,
      h("div", { class: "ctx-card px" },
        h("div", { class: "card-title" }, t("hours left").toUpperCase()),
        h("div", { class: "hours-big" }, duration(sub.balance_seconds)),
        h("div", { class: "meter-bar big" }, ...Array.from({ length: BLOCKS }, (_, i) => h("i", { class: i < filled ? "on" : "" }))),
        h("div", { class: "dim" }, `${t("of")} ${duration(sub.plan_seconds)} · ${t("period ends")} ${when(sub.period_end).split(",")[0]}`),
        sub.purchased_seconds > 0 ? h("div", { class: "dim" }, `${t("purchased")}: ${duration(sub.purchased_seconds)}`) : null,
        agent?.cloud ? h("div", { class: "dim" }, `${t("this task")}: ${duration(agent.cloud.used_seconds)}`) : null,
      ),
      h("div", { class: "pad" }, h("a", { class: "btn", href: "/dashboard" }, icon("plus", 14), h("span", {}, t("manage plan")))),
      h("div", { class: "card-title pad" }, t("history").toUpperCase()),
      ...(account.feed.length ? account.feed.slice(0, SHOWN).map(feedRow) : [h("p", { class: "dim pad" }, t("no hours history"))]),
    );
  },
};
