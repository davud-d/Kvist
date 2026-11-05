// Funnel step 3: attach a card. Paddle.js overlay on top of this page when billing is on; a stub
// with the same form and a "skip (dev)" button when it is off. The trial's terms are above both.
import { api } from "../api.js";
import { fromBoot } from "../boot.js";
import { h, replace, skeleton } from "../dom.js";
import { t } from "../i18n.js";
import { watchMachine } from "../machine.js";
import { errorBox, liveRegion, machinePill } from "../ui.js";

const PADDLE_JS = "https://cdn.paddle.com/paddle/v2/paddle.js";

function loadPaddle() {
  if (window.Paddle) return Promise.resolve(window.Paddle);
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = PADDLE_JS;
    script.onload = () => resolve(window.Paddle);
    script.onerror = () => reject(Object.assign(new Error("paddle"), { code: "billing_unavailable" }));
    document.head.append(script);
  });
}

async function waitForSubscription(tries = 60) {
  for (let i = 0; i < tries; i += 1) {
    const view = await api("/api/billing/subscription");
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

  if (!config.enabled) {
    replace(action, stubForm(async () => {
      try {
        api("/api/billing/checkout-opened", { method: "POST" }).catch(() => {});
        await api("/api/billing/dev/skip-checkout", { method: "POST" });
        done();
      } catch (error) {
        fail(error);
      }
    }));
  } else {
    const open = async () => {
      status.textContent = t("start.opening");
      try {
        const Paddle = await loadPaddle();
        if (config.paddle.environment === "sandbox") Paddle.Environment.set("sandbox");
        Paddle.Initialize({
          token: config.paddle.client_token,
          eventCallback: async (event) => {
            if (event.name !== "checkout.completed") return;
            replace(action, h("p.hint.wait", { text: t("start.confirming") }));
            try {
              await waitForSubscription();
              done();
            } catch (error) {
              fail(error);
            }
          },
        });
        api("/api/billing/checkout-opened", { method: "POST" }).catch(() => {});
        Paddle.Checkout.open({
          items: [{ priceId: config.paddle.prices.start, quantity: 1 }],
          customData: { user_id: String(me.id) },
          settings: { displayMode: "overlay", locale: document.documentElement.lang },
        });
      } catch (error) {
        fail(error);
      }
    };
    replace(action, h("button.btn.wide", { type: "button", onclick: open, text: t("start.addCard") }));
  }

  return watchMachine((machine) => {
    if (!machine) return replace(machineLine, t("start.machineCreating"));
    const state = machine.display_status;
    const text = state === "error" ? t("start.machineError")
      : ["ready", "asleep"].includes(state) ? t("start.machineReady") : t("start.machineCreating");
    replace(machineLine, machinePill(state), h("span", { text }));
  });
}
