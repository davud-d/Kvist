// Admin: people (look up, give a plan or hours, admin rights, stop a machine, ban), the funnel, open disputes.
import { api } from "../api.js";
import { h, modal, replace, skeleton } from "../dom.js";
import { dateTime, hoursMinutes, shortDate } from "../format.js";
import adminEn from "../../i18n/admin.en.json";
import adminRu from "../../i18n/admin.ru.json";
import { extend, locale, t } from "../i18n.js";
import { emptyBox, errorBox } from "../ui.js";

// Only admins open this screen, so its strings and code are a separate chunk, not part of the first screen.
extend("admin", { en: adminEn, ru: adminRu });

const post = (path, body) => api(path, { method: "POST", body });
const planName = (plan) => (plan ? t(`pricing.planNames.${plan}`) : t("admin.noPlan"));

function percent(share) {
  return share === null ? "—" : `${(share * 100).toFixed(1)}%`;
}

function disputeRow(item, reload) {
  const minutes = h("input.input", { type: "number", min: 1, value: Math.max(1, Math.ceil(item.charged_seconds / 60)),
    "aria-label": t("admin.refundMinutes"), style: "max-width:8em" });
  const note = h("input.input", { type: "text", value: "", placeholder: "note", "aria-label": "note" });
  const status = h("div");
  const send = (path, body) => async () => {
    try {
      await api(`/api/admin/disputes/${item.id}/${path}`, { method: "POST", body });
      reload();
    } catch (error) {
      replace(status, errorBox(error));
    }
  };
  return h("li.stack", {},
    h("p", {}, h("b", {}, `#${item.id} ${item.user}`), ` · ${hoursMinutes(item.charged_seconds, t)}`,
      item.task_id ? [" · ", h("span.small", {}, `task ${item.task_id}`)] : null),
    h("p.dim", {}, item.message),
    h("div.row", {}, minutes, note,
      h("button.btn", { type: "button", text: t("admin.refund"),
        onclick: () => send("refund", { minutes: Number(minutes.value), note: note.value || "refund" })() }),
      h("button.btn.ghost", { type: "button", text: t("admin.reject"), onclick: send("close") })),
    status);
}

function confirmDialog(text, onYes) {
  modal((close) => h("div.stack", {},
    h("h2.h3", {}, t("admin.sure")),
    h("p.dim", {}, text),
    h("div.row", {},
      h("button.btn", { type: "button", text: t("admin.yes"), onclick: () => { close(); onYes(); } }),
      h("button.btn.ghost", { type: "button", autofocus: true, text: t("common.cancel"), onclick: close }))));
}

function field(label, control) {
  return h("label.stack", {}, h("span.small", {}, label), control);
}

/** One thing an admin can do to the person: its inputs, a button, and the error if the server refused. */
function actionBlock({ title, hint, controls, button, run, refresh, confirm }) {
  const status = h("div", { "aria-live": "polite" });
  const go = async () => {
    try {
      await run();
      refresh();
    } catch (error) {
      replace(status, errorBox(error));
    }
  };
  return h("div.stack", {},
    h("p", {}, h("b", {}, title)),
    hint ? h("p.small.dim", {}, hint) : null,
    controls.length ? h("div.row", {}, controls) : null,
    h("div", {}, h("button.btn", { type: "button", text: button, onclick: () => (confirm ? confirmDialog(confirm, go) : go()) })),
    status);
}

function planBlock(user, plans, refresh) {
  const plan = h("select.input", { "aria-label": t("admin.plan") },
    plans.map((id) => h("option", { value: id, selected: id === "pro" }, planName(id))));
  const days = h("input.input", { type: "number", min: 1, max: 3660, style: "max-width:8em" });
  const note = h("input.input", { type: "text", maxLength: 300 });
  return actionBlock({
    title: t("admin.givePlan"), hint: t("admin.givePlanHint"), button: t("admin.give"), refresh,
    controls: [field(t("admin.plan"), plan), field(t("admin.days"), days), field(t("admin.noteForPerson"), note)],
    run: () => post(`/api/admin/users/${user.id}/plan`,
      { plan: plan.value, days: days.value ? Number(days.value) : null, note: note.value }),
  });
}

function hoursBlock(user, refresh) {
  if (!user.plan || user.own_machine) return h("p.small.dim", {}, t(user.plan ? "admin.noHoursOwn" : "admin.noHoursNoPlan"));
  const minutes = h("input.input", { type: "number", min: -100000, max: 100000, style: "max-width:9em" });
  const note = h("input.input", { type: "text", maxLength: 300 });
  return actionBlock({
    title: t("admin.hours"), hint: t("admin.hoursHint"), button: t("admin.apply"), refresh,
    controls: [field(t("admin.minutes"), minutes), field(t("admin.hoursNote"), note)],
    run: () => post(`/api/admin/users/${user.id}/hours`, { minutes: Number(minutes.value), note: note.value }),
  });
}

function rightsBlock(user, me, refresh) {
  if (user.is_owner) return h("p.small.dim", {}, t("admin.isOwner"));
  if (!me.is_owner) return h("p.small.dim", {}, t(user.is_admin ? "admin.isAdmin" : "admin.ownersOnly"));
  return actionBlock({
    title: t("admin.rights"), hint: t(user.is_admin ? "admin.isAdmin" : "admin.notAdmin"), controls: [], refresh,
    button: t(user.is_admin ? "admin.removeAdmin" : "admin.makeAdmin"),
    run: () => post(`/api/admin/users/${user.id}/admin`, { value: !user.is_admin }),
  });
}

function machineBlock(user, refresh) {
  if (user.own_machine) return h("p.small.dim", {}, t("admin.machineOwn"));
  if (!user.machine) return h("p.small.dim", {}, t("admin.noMachine"));
  const stopped = user.machine.suspended;
  return actionBlock({
    title: t("admin.machine"), hint: t(stopped ? "admin.machineStopped" : "admin.machineHint"), controls: [], refresh,
    button: t(stopped ? "admin.resumeMachine" : "admin.stopMachine"),
    confirm: stopped ? "" : t("admin.stopConfirm", { login: user.login }),
    run: () => post(`/api/admin/users/${user.id}/machine/${stopped ? "resume" : "suspend"}`),
  });
}

function banBlock(user, refresh) {
  if (user.ban) {
    return actionBlock({
      title: t("admin.banned"), refresh, button: t("admin.unban"), controls: [],
      hint: [user.ban.permanent ? t("admin.bannedForever") : t("admin.bannedUntil", { date: shortDate(user.ban.until, locale()) }),
        user.ban.reason ? ` · ${user.ban.reason}` : ""].join(""),
      run: () => post(`/api/admin/users/${user.id}/unban`),
    });
  }
  const days = h("input.input", { type: "number", min: 1, max: 3650, value: 7, style: "max-width:8em" });
  const forever = h("input", { type: "checkbox", id: "ban-forever" });
  forever.addEventListener("change", () => { days.disabled = forever.checked; });
  const reason = h("input.input", { type: "text", maxLength: 300 });
  return actionBlock({
    title: t("admin.ban"), hint: t("admin.banHint"), refresh, button: t("admin.banDo"),
    confirm: t("admin.banConfirm", { login: user.login }),
    controls: [field(t("admin.banDays"), days),
      h("label.row", {}, forever, t("admin.permanent")),
      field(t("admin.banReason"), reason)],
    run: () => post(`/api/admin/users/${user.id}/ban`,
      forever.checked ? { permanent: true, reason: reason.value } : { days: Number(days.value), reason: reason.value }),
  });
}

function history(user) {
  const lang = locale();
  return h("div.stack", {},
    h("p", {}, h("b", {}, t("admin.recentHours"))),
    user.ledger.length
      ? h("ul.list", {}, user.ledger.map((row) => h("li", {},
          h("div.spread", {}, h("span", {}, t(`dashboard.kind.${row.kind}`), row.reason ? ` · ${row.reason}` : ""),
            h("b", {}, `${row.negative ? "−" : "+"}${hoursMinutes(row.minutes * 60, t)}`)),
          h("p.small.dim", {}, dateTime(row.at, lang)))))
      : h("p.small.dim", {}, t("admin.nothing")),
    h("p", {}, h("b", {}, t("admin.recentActions"))),
    user.actions.length
      ? h("ul.list", {}, user.actions.map((row) => h("li", {},
          h("span", {}, `${t(`admin.act.${row.action}`)} · ${row.admin}`),
          h("p.small.dim", {}, dateTime(row.at, lang)))))
      : h("p.small.dim", {}, t("admin.nothing")));
}

function userFlags(user) {
  return [
    user.is_owner ? t("admin.flagOwner") : user.is_admin ? t("admin.flagAdmin") : null,
    user.ban ? t("admin.flagBanned") : null,
    user.machine && user.machine.suspended ? t("admin.flagStopped") : null,
  ].filter(Boolean).join(" · ");
}

function userSummary(user) {
  const left = user.plan && !user.own_machine ? ` · ${hoursMinutes(user.balance_minutes * 60, t)}` : "";
  return `${planName(user.plan)}${left}`;
}

function peopleSection(me) {
  const box = h("section.card", { "aria-labelledby": "people-title" });
  const query = h("input.input", { type: "search", placeholder: t("admin.searchHint"), "aria-label": t("admin.search") });
  let rows = [];
  let more = false;

  async function showUser(id) {
    replace(box, skeleton(5));
    try {
      const user = await api(`/api/admin/users/${id}`);
      const refresh = () => showUser(id);
      replace(box,
        h("button.linkbtn", { type: "button", onclick: () => showList(), text: t("admin.back") }),
        h("div.stack", {},
          h("h2.h3", { id: "people-title" }, user.login),
          h("p.dim", {}, [user.name, user.email, `#${user.id}`, shortDate(user.created_at, locale())].filter(Boolean).join(" · ")),
          h("p", {}, `${planName(user.plan)}${user.plan_ends_at ? ` · ${t("admin.until", { date: shortDate(user.plan_ends_at, locale()) })}` : ""}`,
            user.plan && !user.own_machine ? ` · ${hoursMinutes(user.balance_minutes * 60, t)}` : "",
            userFlags(user) ? ` · ${userFlags(user)}` : ""),
          planBlock(user, user.plans, refresh),
          hoursBlock(user, refresh),
          rightsBlock(user, me, refresh),
          machineBlock(user, refresh),
          banBlock(user, refresh),
          history(user)));
    } catch (error) {
      replace(box, errorBox(error, () => showUser(id)));
    }
  }

  function drawList() {
    replace(box,
      h("h2.h3", { id: "people-title" }, t("admin.usersTitle")),
      h("form.row", { onsubmit: (event) => { event.preventDefault(); showList(query.value); } },
        query, h("button.btn", { type: "submit", text: t("admin.search") })),
      rows.length
        ? h("ul.list", {}, rows.map((user) => h("li", {},
            h("div.spread", {},
              h("button.linkbtn", { type: "button", onclick: () => showUser(user.id), text: user.login }),
              h("span.small", {}, userSummary(user))),
            userFlags(user) ? h("p.small.dim", {}, userFlags(user)) : null)))
        : emptyBox(t("admin.noUsers")),
      more ? h("p", {}, h("button.btn.ghost", { type: "button", text: t("admin.more"), onclick: () => loadMore(query.value) })) : null);
  }

  async function fetchPage(term, offset) {
    return api(`/api/admin/users?q=${encodeURIComponent(term)}&offset=${offset}`);
  }

  async function showList(term = query.value) {
    replace(box, skeleton(5));
    try {
      const page = await fetchPage(term, 0);
      rows = page.users;
      more = page.more;
      drawList();
    } catch (error) {
      replace(box, errorBox(error, () => showList(term)));
    }
  }

  async function loadMore(term) {
    try {
      const page = await fetchPage(term, rows.length);
      rows = rows.concat(page.users);
      more = page.more;
      drawList();
    } catch (error) {
      box.append(errorBox(error));
    }
  }

  showList("");
  return box;
}

export async function adminScreen(root, { me }) {
  const load = async () => {
    const [funnel, disputes] = await Promise.all([api("/api/admin/funnel"), api("/api/admin/disputes")]);
    replace(root, h("div.stack-lg", {},
      h("h1.h2", {}, t("admin.title")),
      peopleSection(me),
      h("section.card", {}, h("h2.h3", {}, t("admin.funnel")),
        h("div.table-scroll", {}, h("table.plans", {},
          h("thead", {}, h("tr", {}, [t("admin.step"), t("admin.people"), t("admin.fromPrevious")]
            .map((label) => h("th", { scope: "col" }, label)))),
          h("tbody", {}, funnel.steps.map((row) => h("tr", {},
            h("th", { scope: "row" }, row.step), h("td", {}, String(row.people)), h("td", {}, percent(row.from_previous)))))))),
      h("section.card", {}, h("h2.h3", {}, t("admin.disputes")),
        disputes.length ? h("ul.list", {}, disputes.map((item) => disputeRow(item, reload))) : emptyBox(t("admin.noDisputes")))));
  };
  const reload = () => load().catch((error) => replace(root, errorBox(error, reload)));
  replace(root, skeleton(6));
  await load();
  return null;
}
