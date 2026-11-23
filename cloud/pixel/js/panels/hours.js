// The hours: what is left of the plan, what this task has used, and where they went.
// Hours run only while the agent works (cloud/docs/ARCHITECTURE.md, "Hours ledger rules").

import { account, duration, share } from "../cloud.js";
import { fill, h, meterTrack } from "../dom.js";
import { lang, t } from "../i18n.js";

const SHOWN = 8;

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
    // What the number is, said next to it: the agent's working hours left in this period of the plan.
    fill(
      el,
      h("h3", { class: "sheet-heading" }, t("agent hours left")),
      h("div", { class: "hours-big" }, duration(sub.balance_seconds)),
      meterTrack(share(sub.balance_seconds, sub.plan_seconds), { big: true, warnBelow: 0.2 }),
      h("dl", { class: "spec" },
        h("div", { class: "kv" }, h("dt", { class: "kv-key" }, t("in the plan")), h("dd", { class: "kv-value" }, duration(sub.plan_seconds))),
        h("div", { class: "kv" }, h("dt", { class: "kv-key" }, t("period ends")), h("dd", { class: "kv-value" }, when(sub.period_end).split(",")[0])),
        sub.purchased_seconds > 0 ? h("div", { class: "kv" }, h("dt", { class: "kv-key" }, t("purchased")), h("dd", { class: "kv-value" }, duration(sub.purchased_seconds))) : null,
        agent?.cloud ? h("div", { class: "kv" }, h("dt", { class: "kv-key" }, t("this task")), h("dd", { class: "kv-value" }, duration(agent.cloud.used_seconds))) : null),
      h("p", { class: "dim" }, t("hours run note")),
      h("a", { class: "btn", href: "/dashboard" }, h("span", {}, t("manage plan"))),
      h("h3", { class: "sheet-heading" }, t("history")),
      h("div", {}, account.feed.length ? account.feed.slice(0, SHOWN).map(feedRow) : h("p", { class: "dim" }, t("no hours history"))),
    );
  },
};
