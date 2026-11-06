// Funnel step 3: start the trial (free) or pay for a plan on Platega's page; a stub with a "skip (dev)" button
// when billing is off. The trial's terms are above both.
import { chargeLabel, startCheckout, syncPayments } from "../billing.js";
import { choosePayment } from "../paydialog.js";
import { api } from "../api.js";
import { fromBoot } from "../boot.js";
import { h, replace, skeleton } from "../dom.js";
import { t } from "../i18n.js";
import { watchMachine } from "../machine.js";
import { errorBox, liveRegion, machinePill } from "../ui.js";

async function waitForSubscription(tries = 60) {
  for (let i = 0; i < tries; i += 1) {
    const view = await syncPayments();
    if (view.status !== "none") return view;
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  throw Object.assign(new Error("timeout"), { code: "billing_unavailable" });
}

function stubForm(onSkip) {
  return h("div.card", {},
    h("p.h3", { text: t("start.stubTitle") }),
    h("p.small", { text: t("start.stubNote") }),
    h("label.field", {}, h("span", { text: t("start.cardNumber") }),
      h("input.input", { inputmode: "numeric", autocomplete: "off", placeholder: "4242 4242 4242 4242", disabled: true })),
    h("div.row", {},
      h("label.field", { style: "flex:1" }, h("span", { text: t("start.expiry") }),
        h("input.input", { placeholder: "12 / 30", disabled: true })),
      h("label.field", { style: "flex:1" }, h("span", { text: t("start.cvc") }),
        h("input.input", { placeholder: "123", disabled: true }))),
    h("button.btn.wide", { type: "button", onclick: onSkip, text: t("start.skipDev") }));
}

export async function startScreen(root, { me, navigate }) {
  replace(root, skeleton(4));
  const [config, subscription] = await Promise.all([
    fromBoot("billing", () => api("/api/billing/config")),
    fromBoot("subscription", () => api("/api/billing/subscription")),
  ]);
  if (subscription.status !== "none") {
    navigate("/onboarding", { replace: true });
    return null;
  }
  const status = liveRegion();
  const action = h("div");
  const machineLine = h("p.row.small", { "aria-live": "polite" });
  replace(root,
    h("section.stack", {},
      h("p.kicker", { text: t("start.kicker") }),
      h("h1.h2", { text: t("start.title") }),
      h("p.banner", { text: t("start.terms") }),
      action,
      machineLine,
      status));

  const done = () => navigate("/onboarding");
  const fail = (error) => replace(action, errorBox(error, () => location.reload()));

  if (!config.enabled && config.dev) {
    replace(action, stubForm(async () => {
      try {
        api("/api/billing/checkout-opened", { method: "POST" }).catch(() => {});
        await api("/api/billing/dev/skip-checkout", { method: "POST" });
        done();
      } catch (error) {
        fail(error);
      }
    }));
  } else if (config.enabled) {
    const eligible = await api("/api/billing/eligibility");
    if (eligible.payment_pending) {
      replace(action, h("p.hint.wait", { text: t("start.confirming") }));
      waitForSubscription().then(done).catch(() => {});
      return null;
    }
    const problem = h("div");
    const open = async (plan) => {
      status.textContent = t("start.opening");
      replace(problem);
      try {
        // A paid plan: pick how to pay, then Platega's page, which returns here. The free trial starts at once.
        if (plan !== "trial") { choosePayment(config, plan); status.textContent = ""; return; }
        if (await startCheckout(plan) === "started") done();
      } catch (error) {
        // Next to the choices, not instead of them: a refused trial still leaves the paid plans.
        replace(problem, errorBox(error));
      }
    };
    replace(action, h("div.stack", {},
      eligible.trial_eligible ? h("button.btn.wide", { type: "button", onclick: () => open("trial"), text: t("start.addCard") })
        : h("p.hint", { text: t("errors.trial_ineligible") }),
      h("div.row", {}, ...["start", "pro", "max"].map((plan) => h("button.btn.ghost", { type: "button",
        onclick: () => open(plan),
        text: `${plan.toUpperCase()} · ${chargeLabel(config, plan, config.plans.find((p) => p.id === plan).price_usd)}/`
          + t("start.month") }))),
      problem));
  } else {
    replace(action, errorBox({ code: "billing_unavailable" }));
  }

  return watchMachine((machine) => {
    if (!machine) return replace(machineLine, t("start.machineCreating"));
    const state = machine.display_status;
    const text = state === "error" ? t("start.machineError")
      : ["ready", "asleep"].includes(state) ? t("start.machineReady") : t("start.machineCreating");
    replace(machineLine, machinePill(state), h("span", { text }));
  });
}
