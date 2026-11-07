// Dashboard: hours and their feed, the machine, recent tasks, the key (last four characters).
import { chargeLabel, syncPayments } from "../billing.js";
import { resetMachineDialog } from "../machinereset.js";
import { choosePayment } from "../paydialog.js";
import { api } from "../api.js";
import { h, modal, replace, skeleton } from "../dom.js";
import { exportButton } from "../exporter.js";
import { dateTime, hoursMinutes, percentLeft, shortDate } from "../format.js";
import { locale, t } from "../i18n.js";
import { emptyBox, errorBox, liveRegion, machinePill, taskPill } from "../ui.js";

function disputeDialog(row, onSent) {
  modal((close) => {
    const text = h("textarea.input", { rows: 4, placeholder: t("dashboard.disputePlaceholder"), autofocus: true,
      "aria-label": t("dashboard.disputePlaceholder") });
    const status = h("div", { "aria-live": "polite" });
    const send = h("button.btn", { type: "button", text: t("dashboard.disputeSend") });
    send.addEventListener("click", async () => {
      if (text.value.trim().length < 3) { text.focus(); return; }
      send.disabled = true;
      try {
        await api("/api/disputes", { method: "POST", body: { task_id: row.task_id, message: text.value.trim() } });
        close();
        onSent();
      } catch (error) {
        send.disabled = false;
        replace(status, errorBox(error));
      }
    });
    return h("div.stack", {},
      h("h2.h3", {}, t("dashboard.disputeTitle")),
      h("p.small", {}, `${row.goal} · ${hoursMinutes(Math.abs(row.seconds), t)}`),
      h("p.dim", {}, t("dashboard.disputeBody")),
      text, status,
      h("div.row", {}, send, h("button.btn.ghost", { type: "button", onclick: close, text: t("common.cancel") })));
  });
}

function hoursCard(view, config, refresh, announce) {
  const total = view.plan_seconds;
  const buy = h("button.btn.ghost", { type: "button",
    text: t("dashboard.buyHoursCharge", { h: config.hour_pack.hours,
      p: chargeLabel(config, "hours", config.hour_pack.price_usd) }) });
  buy.addEventListener("click", async () => {
    buy.disabled = true;
    try {
      if (config.enabled) {
        // Billing on: the one-off hour pack is paid on Platega's page, and this page is where it returns to.
        choosePayment(config, "hours");
        buy.disabled = false;
      } else {
        await api("/api/billing/dev/buy-hours", { method: "POST" });
        announce.textContent = t("dashboard.bought", { h: config.hour_pack.hours });
        refresh();
      }
    } catch (error) {
      buy.disabled = false;
      buy.after(errorBox(error));
    }
  });
  return h("section.card", { "aria-labelledby": "hours-title" },
    h("div.spread", {}, h("h2.h3", { id: "hours-title" }, t("dashboard.hoursTitle")),
      h("span.small", {}, t("dashboard.periodEnds", { date: shortDate(view.period_end, locale()) }))),
    h("p", {}, t("dashboard.hoursLeft", { left: hoursMinutes(view.balance_seconds, t), total: hoursMinutes(total, t) })),
    h("div.bar", { role: "img", "aria-label": `${percentLeft(view.balance_seconds, total)}%` },
      h("i", { style: `width:${percentLeft(view.balance_seconds, total)}%` })),
    view.purchased_seconds > 0
      ? h("p.small", {}, t("dashboard.purchased", { h: hoursMinutes(view.purchased_seconds, t) }))
      : null,
    h("p", {}, buy));
}

// The Remote plan: the machine is the person's own, so the page shows how to connect it, not hours.
function connectDialog(config) {
  const text = JSON.stringify(config, null, 2);
  modal((close) => {
    const copy = h("button.btn.ghost", { type: "button", text: t("dashboard.copy") });
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text);
        copy.textContent = t("dashboard.copied");
      } catch (error) {
        if (!(error instanceof DOMException)) throw error;
      }
    });
    return h("div.stack", {},
      h("h2.h3", {}, t("dashboard.connectTitle")),
      h("p.dim", {}, t("dashboard.connectBody")),
      h("pre.code", {}, text),
      h("p.hint", {}, t("dashboard.connectOnce")),
      h("div.row", {}, copy, h("button.btn", { type: "button", autofocus: true, onclick: close, text: t("common.close") })));
  });
}

function remoteCard(view, machines, reload) {
  const connect = h("button.btn", { type: "button",
    text: machines.length ? t("dashboard.reconnect") : t("dashboard.connect") });
  const holder = h("p", {}, connect);
  connect.addEventListener("click", async () => {
    connect.disabled = true;
    try {
      const answer = await api("/api/machines/remote/connect", { method: "POST" });
      connectDialog(answer.runner_config);
      reload();
    } catch (error) {
      holder.after(errorBox(error));
    }
    connect.disabled = false;
  });
  return h("section.card", { "aria-labelledby": "remote-title" },
    h("div.spread", {}, h("h2.h3", { id: "remote-title" }, t("dashboard.remoteTitle")),
      h("span.small", {}, t("dashboard.periodEnds", { date: shortDate(view.period_end, locale()) }))),
    h("p", {}, t("dashboard.remoteText")),
    holder);
}

function machineCard(machines, own, reload) {
  const machine = machines[0];
  return h("section.card", { "aria-labelledby": "machine-title" },
    h("h2.h3", { id: "machine-title" }, t("dashboard.machinesTitle")),
    machine
      ? h("div.stack", {},
          h("p.row", {}, machinePill(machine.display_status),
            h("span.small", {}, `${machine.vcpu} vCPU · ${machine.memory_gb} GB · ${machine.disk_gb} GB`)),
          // A machine in trouble is not mended by waiting: say so, and offer a new one.
          machine.display_status === "error" && !own
            ? h("div.banner.err", { role: "alert" }, h("p", {}, t("machine.deadNote")),
                h("p", {}, h("button.btn", { type: "button", text: t("machine.reset"),
                  onclick: () => resetMachineDialog(machine, reload) })))
            : null,
          exportButton(`/api/machines/${machine.id}/export`, t("dashboard.export")))
      : h("p.dim", {}, own ? t("dashboard.noRemoteMachine") : t("dashboard.noMachine")));
}

function tasksCard(tasks) {
  return h("section.card", { "aria-labelledby": "tasks-title" },
    h("div.spread", {}, h("h2.h3", { id: "tasks-title" }, t("dashboard.tasksTitle")),
      h("a.chip", { href: "/pixel/" }, t("nav.newTask"))),
    tasks.length
      ? h("ul.list", {}, tasks.slice(0, 20).map((task) => h("li", {},
          h("div.spread", {}, h("a.title", { href: `/tasks/${task.id}`, "data-link": "" }, task.goal.split("\n")[0]),
            taskPill(task.status)),
          h("p.meta", {}, `${dateTime(task.created_at, locale())} · ${hoursMinutes(task.used_seconds, t)}`))))
      : emptyBox(t("dashboard.noTasks"), h("a.btn", { href: "/pixel/" }, t("dashboard.firstTask"))));
}

// Under a minute the shown value is 0, and "−0" would read as a glitch.
const sign = (seconds) => (Math.abs(seconds) < 60 ? "" : seconds < 0 ? "−" : "+");

// What the customer chose to pay for the model: a key shows its last four, a ChatGPT sign-in its account.
function modelLine(key) {
  const name = key.provider === "other" ? t("onboarding.otherName") : key.label;
  return key.provider === "chatgpt" ? `${name}${key.chatgpt_email ? ` · ${key.chatgpt_email}` : ""}` : `${name} ••••${key.last4}`;
}

function feedCard(feed, refresh) {
  return h("section.card", { "aria-labelledby": "feed-title" },
    h("h2.h3", { id: "feed-title" }, t("dashboard.feedTitle")),
    feed.length
      ? h("ul.list", {}, feed.map((row) => h("li", {},
          h("div.spread", {},
            h("span", {}, row.kind === "usage" && row.task_id
              ? h("a.title", { href: `/tasks/${row.task_id}`, "data-link": "" }, row.goal.split("\n")[0] || t("dashboard.kind.usage"))
              : t(`dashboard.kind.${row.kind}`)),
            h("b", {}, `${sign(row.seconds)}${hoursMinutes(Math.abs(row.seconds), t)}`)),
          h("p.row.meta", {}, dateTime(row.at, locale()), row.kind === "refund" && row.reason ? ` · ${row.reason}` : "",
            row.kind === "usage" && row.task_id
              ? row.disputed
                ? h("span", {}, t("dashboard.disputeSent"))
                : h("button.linkbtn", { type: "button", onclick: () => disputeDialog(row, refresh), text: t("dashboard.dispute") })
              : null))))
      : h("p.dim", {}, t("dashboard.feedEmpty")));
}

export async function dashboardScreen(root) {
  const announce = liveRegion();
  const load = async () => {
    const [ledgerView, config, machines, tasks, key] = await Promise.all([
      api("/api/ledger"), api("/api/billing/config"), api("/api/machines"), api("/api/tasks"), api("/api/model-key"),
    ]);
    const view = ledgerView.subscription;
    if (view.status === "none") {
      replace(root, h("h1.h2", {}, t("dashboard.title")),
        emptyBox(t("dashboard.noPlan"), h("a.btn", { href: "/start", "data-link": "" }, t("dashboard.startTrial"))));
      return;
    }
    replace(root, h("div.stack-lg", {},
      h("h1.h2", {}, t("dashboard.title")),
      view.trial_reminder
        ? h("div.banner.warn", { role: "status" },
            h("p", {}, t("dashboard.trialBanner", { price: view.price_after_trial_usd })),
            h("p", {}, h("a.inline", { href: "/settings", "data-link": "" }, t("dashboard.toSettings"))))
        : null,
      view.own_machine ? remoteCard(view, machines, reload) : hoursCard(view, config, reload, announce),
      machineCard(machines, view.own_machine, reload),
      tasksCard(tasks),
      feedCard(ledgerView.feed, reload),
      h("section.card", {}, h("h2.h3", {}, t("dashboard.keyTitle")),
        h("p.row", {}, key.set
          ? modelLine(key)
          : t("dashboard.keyNone"),
          h("a.inline", { href: "/settings", "data-link": "" }, t("common.change")))),
      announce));
  };
  const reload = () => load().catch((error) => replace(root, errorBox(error, reload)));
  replace(root, skeleton(3), h("span.skeleton.block"), skeleton(4));
  // Back from paying: the server asks Platega, so a payment shows up even if its callback is late.
  await syncPayments().catch((error) => console.error(error));
  await load();
  const timer = setInterval(() => load().catch(() => {}), 15000);
  return () => clearInterval(timer);
}
