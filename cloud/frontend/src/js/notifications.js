// The bell: notices from the server (an admin gave hours or a plan, a machine was stopped, ...).
// The server sends a kind and parameters; the words are ours, in the reader's language.
import { api } from "./api.js";
import { h, modal, replace } from "./dom.js";
import { dateTime, hoursMinutes, shortDate } from "./format.js";
import { locale, t } from "./i18n.js";

export function noticeText(item) {
  const p = item.params || {};
  const text = t(`notify.kind.${item.kind}`, {
    plan: p.plan ? t(`pricing.planNames.${p.plan}`) : "",
    date: p.until ? shortDate(p.until, locale()) : "",
    time: p.minutes ? hoursMinutes(p.minutes * 60, t) : "",
  });
  return p.note ? `${text} ${t("notify.note", { note: p.note })}` : text;
}

function openList(items, button) {
  modal((close) => h("div.stack", {},
    h("h2.h3", {}, t("notify.title")),
    items.length
      ? h("ul.list", {}, items.map((item) => h("li", {},
          h("p", {}, item.read ? noticeText(item) : h("b", {}, noticeText(item))),
          h("p.small.dim", {}, dateTime(item.at, locale())))))
      : h("p.dim", {}, t("notify.none")),
    h("div.row", {}, h("button.btn", { type: "button", autofocus: true, text: t("common.close"), onclick: close }))));
  if (items.some((item) => !item.read)) {
    api("/api/notifications/read", { method: "POST" })
      .then(() => paint(button, 0))
      .catch(() => {});
  }
}

function paint(button, unread) {
  button.textContent = unread ? `${t("notify.title")} · ${unread}` : t("notify.title");
  button.setAttribute("aria-label", t("notify.label", { n: unread }));
}

/** The header button; it fills in its count once the server answers, and never blocks the page. */
export function bellButton() {
  const button = h("button.chip", { type: "button", hidden: true });
  paint(button, 0);
  api("/api/notifications").then((answer) => {
    button.hidden = false;
    paint(button, answer.unread);
    button.onclick = () => openList(answer.items, button);
  }).catch(() => {});
  return button;
}

export function banScreen(root, ban, signOut) {
  replace(root, h("div.banner.err", { role: "alert" },
    h("p.title", {}, t("ban.title")),
    h("p", {}, ban.permanent ? t("ban.permanent") : t("ban.until", { date: shortDate(ban.until, locale()) })),
    ban.reason ? h("p", {}, t("ban.reason", { reason: ban.reason })) : null,
    h("p", {}, h("button.btn.ghost", { type: "button", text: t("common.signOut"), onclick: signOut }))));
}
