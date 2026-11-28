// Plans and paying. The price on the button is the server's: the phone asks the server for a checkout, the server
// names the amount, and only then does the operating system's sheet open. Nothing here holds a card.

import { account, refresh, walletToUse } from "../account.js";
import { post } from "../api.js";
import { h, icon } from "../dom.js";
import { money } from "../format.js";
import { t } from "../i18n.js";
import * as native from "../native.js";
import { paymentProblem } from "../rules.js";
import { openSheet, toast } from "../sheet.js";
import { walletButton } from "../walletbutton.js";

// Platega's methods as the person knows them (the server lists which of them it offers).
const METHODS = { 2: "method sbp", 3: "method erip", 11: "method card", 12: "method intl", 13: "method crypto", 14: "method sber" };

const planName = (id) => t(`plan ${id}`);
const hoursLabel = (hours) => t("n hours", { n: hours });

function spec(plan) {
  return [`${plan.vcpu} vCPU`, `${plan.memory_gb} GB`, `${plan.disk_gb} GB`, t("parallel", { n: plan.max_parallel_tasks })].join(" · ");
}

/** What a purchase gives, one line, for the pay sheet. */
function whatYouGet(kind) {
  if (kind === "searches") return t("searches pack gives", { n: account.billing.search_pack.searches });
  if (kind === "hours") return t("hours pack gives", { n: account.billing.hour_pack.hours });
  const plan = account.billing.plans.find((p) => p.id === kind);
  return `${hoursLabel(plan.hours)} · ${spec(plan)}`;
}

/** The wallet price when the server offers a wallet here, else the card price the other methods charge. */
function priceOf(kind) {
  const wallet = account.wallet;
  if (wallet?.enabled && wallet.prices?.[kind]) return money(wallet.prices[kind], wallet.currency);
  const packs = { hours: account.billing.hour_pack, searches: account.billing.search_pack };
  const plan = packs[kind] ?? account.billing.plans.find((p) => p.id === kind);
  return money(plan.price_usd, "USD");
}

async function afterPaid(close) {
  await refresh("sub", "machines", "searches");
  native.haptic("success");
  toast(t("pay done"));
  close();
}

/** One purchase from start to finish through the phone's wallet. Resolves true when it was paid. */
async function payWithWallet(kind, wallet) {
  const checkout = await post("/api/wallet/checkout", { plan: kind, wallet });
  const result = await native.pay(wallet, checkout.request, account.wallet.google_environment);
  if (result.cancelled) return false;
  try {
    await post("/api/wallet/pay", { checkout: checkout.checkout, wallet, token: result.token });
  } catch (error) {
    // The sheet is still open (Apple's) and must be told it failed, or it would hang until it gives up by itself.
    await native.payDone(wallet, false);
    throw error;
  }
  await native.payDone(wallet, true);
  return true;
}

export function payRows(kind, closeAll) {
  const wallet = walletToUse();
  const status = h("p", { class: "pay-status", role: "status" });
  const say = (key) => status.replaceChildren(key ? t(key) : "");
  const rows = [];
  if (wallet) {
    const button = walletButton(wallet, async () => {
      say("");
      try {
        if (await payWithWallet(kind, wallet)) await afterPaid(closeAll);
      } catch (error) {
        native.haptic("warn");
        say(paymentProblem(error));
        if (error.localized) status.append(` ${error.localized}`);
      }
    }, account.wallet.google_environment);
    rows.push(h("div", { class: "wallet-slot" }, button));
  }
  const other = otherWays(kind, say, closeAll);
  if (other) rows.push(other);
  if (account.billing?.dev) rows.push(devSkip(kind, closeAll));
  if (!rows.length) rows.push(h("p", { class: "dim" }, t("pay unavailable")));
  rows.push(status);
  return rows;
}

/** Opens a hosted payment page in the system browser; when the person comes back, asks the server what became of it. */
async function payOnPage(path, body, kind, say, closeAll) {
  try {
    const { redirect } = await post(path, body);
    await native.open(redirect);
    say("pay opened");
    const settle = async () => {
      if (document.hidden) return;
      document.removeEventListener("visibilitychange", settle);
      await post("/api/billing/sync");
      await refresh("sub", "searches");
      if (account.sub?.plan === kind || kind === "hours" || kind === "searches") closeAll();
    };
    document.addEventListener("visibilitychange", settle);
  } catch (error) {
    say(paymentProblem(error));
  }
}

function otherWays(kind, say, closeAll) {
  const charge = account.billing?.enabled ? account.billing.charge : null;
  const freedom = account.billing?.freedompay;
  if (!charge && !freedom) return null;
  const rows = [];
  // The search pack is optional on a server: offered only where its price is set.
  if (freedom && (kind !== "searches" || freedom.prices?.searches)) {
    // Freedom Pay's own page shows its cards and, where connected, Apple Pay and Google Pay.
    rows.push(h("button", { class: "row", type: "button", onclick: () => payOnPage("/api/freedompay/checkout", { plan: kind }, kind, say, closeAll) },
      icon("card", 20),
      h("span", { class: "row-main" }, t("method freedom")),
      freedom.prices?.[kind] ? h("span", { class: "row-end" }, money(freedom.prices[kind], freedom.currency)) : null));
  }
  for (const method of charge?.methods ?? []) {
    const price = charge.prices?.[kind];
    if (kind === "searches" && !price) break;
    rows.push(h("button", { class: "row", type: "button", onclick: () => payOnPage("/api/billing/checkout", { plan: kind, method }, kind, say, closeAll) },
      icon("card", 20),
      h("span", { class: "row-main" }, t(METHODS[method] ?? "method card")),
      price ? h("span", { class: "row-end" }, money(price, charge.currency)) : null));
  }
  return h("div", { class: "ways" }, h("div", { class: "ways-label" }, account.wallets.length ? t("pay another way") : t("pay with")), rows);
}

function devSkip(kind, closeAll) {
  return h("button", { class: "row dev", type: "button", onclick: async () => {
    try {
      const paths = { hours: "/api/billing/dev/buy-hours", searches: "/api/billing/dev/buy-searches" };
      await post(paths[kind] ?? "/api/billing/dev/skip-checkout");
      await afterPaid(closeAll);
    } catch (error) {
      toast(error.localized, "error");
    }
  } }, icon("lock", 20), h("span", { class: "row-main" }, t("pay dev skip")));
}

function periodOf(kind) {
  if (kind === "searches") return t("until used");
  if (kind === "hours") return t("one time");
  return t("per period", { days: account.billing.plans.find((p) => p.id === kind)?.days ?? 30 });
}

/** The sheet where a plan, the hour pack or the search pack is paid for. */
export function paySheet(kind, parent) {
  openSheet({
    title: { hours: t("buy hours"), searches: t("buy searches") }[kind] ?? t("plan named", { name: planName(kind) }),
    build: (close) => [
      h("div", { class: "pay-summary" },
        h("div", { class: "pay-price" }, priceOf(kind)),
        h("div", { class: "dim" }, periodOf(kind)),
        h("p", { class: "pay-gets" }, whatYouGet(kind))),
      payRows(kind, () => {
        close();
        parent?.();
      }),
      h("p", { class: "fine" }, t("pay fine")),
    ],
  });
}

async function startTrial(close) {
  try {
    // With billing off (a local run) the trial starts through the development path, as the web app's stub does.
    await post(account.billing?.enabled ? "/api/billing/checkout" : "/api/billing/dev/skip-checkout", { plan: "trial" });
    await refresh("sub", "machines");
    toast(t("trial started"));
    close();
  } catch (error) {
    toast(error.localized, "error");
  }
}

/** Connecting GitHub happens in this browser's own session, which the apps' web views do not share. */
export const linkGithubHref = `/auth/github/login?link=1&next=${encodeURIComponent("/m/")}`;

function trialNeedsGithub(trial) {
  return h("div", { class: "plan-card static" },
    h("div", { class: "plan-top" }, h("strong", {}, t("plan trial")), h("span", { class: "plan-price" }, t("free"))),
    h("div", { class: "dim" }, `${hoursLabel(trial.hours)} · ${t("days n", { n: trial.trial_days })}`),
    h("p", { class: "fine" }, t(native.inApp() ? "trial needs github app" : "trial needs github")),
    native.inApp() ? null : h("a", { class: "btn small", href: linkGithubHref }, icon("github", 16), t("connect github")));
}

/** The plans, the hour pack and, if it is still free for this person, the trial. */
export function plansSheet() {
  openSheet({
    title: t("plans"),
    variant: "tall",
    build: (close) => {
      const sub = account.sub ?? { status: "none" };
      const catalog = account.billing?.plans ?? [];
      const canBuyPlan = sub.status === "none" || sub.plan === "trial" || sub.can_renew;
      const trial = catalog.find((p) => p.id === "trial");
      const items = [];
      if (sub.status === "none" && trial && account.me?.github === false) {
        // An account made with Apple: the trial waits for a GitHub account older than a month; the plans below
        // can be bought straight away without one.
        items.push(trialNeedsGithub(trial));
      } else if (sub.status === "none" && trial) {
        items.push(h("button", { class: "plan-card", type: "button", onclick: () => startTrial(close) },
          h("div", { class: "plan-top" }, h("strong", {}, t("plan trial")), h("span", { class: "plan-price" }, t("free"))),
          h("div", { class: "dim" }, `${hoursLabel(trial.hours)} · ${t("days n", { n: trial.trial_days })}`),
          h("div", { class: "dim small" }, spec(trial))));
      }
      for (const plan of catalog.filter((p) => p.price_usd > 0)) {
        const current = sub.plan === plan.id && sub.status !== "none";
        items.push(h("button", { class: `plan-card${current ? " current" : ""}`, type: "button", disabled: !canBuyPlan,
          onclick: () => paySheet(plan.id, close) },
        h("div", { class: "plan-top" }, h("strong", {}, planName(plan.id)), h("span", { class: "plan-price" }, priceOf(plan.id))),
        h("div", { class: "dim" }, `${hoursLabel(plan.hours)} · ${t("30 days")}`),
        h("div", { class: "dim small" }, spec(plan)),
        current ? h("div", { class: "tag" }, t("current")) : null));
      }
      const note = !canBuyPlan && sub.period_end
        ? h("p", { class: "fine" }, t("renew later", { date: new Date(sub.period_end).toLocaleDateString(document.documentElement.lang) }))
        : null;
      const pack = account.billing?.hour_pack;
      return [
        ...items,
        note,
        pack && sub.status !== "none" && !sub.unmetered ? h("button", { class: "plan-card pack", type: "button", onclick: () => paySheet("hours", close) },
          h("div", { class: "plan-top" }, h("strong", {}, t("buy hours")), h("span", { class: "plan-price" }, priceOf("hours"))),
          h("div", { class: "dim" }, t("hours pack gives", { n: pack.hours }))) : null,
      ];
    },
  });
}
