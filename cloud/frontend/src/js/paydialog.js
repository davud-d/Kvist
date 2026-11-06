// The payment picker: the way to pay on the left, what is bought and its total on the right.
// It only chooses; the server decides the amount and the methods it accepts (see billing.py).
import { startCheckout } from "./billing.js";
import { h, modal, replace } from "./dom.js";
import { METHOD_ICONS, icon } from "./icons.js";
import { locale, t } from "./i18n.js";
import { errorBox } from "./ui.js";

// Platega's method ids without a logo set (see icons.js) get a short mark in the site's own type.
const MARKS = { 3: "ЕРИП", 14: "СБ" };
// Methods that run through Russian banks: they tend to fail from a foreign address or behind a VPN.
const RUSSIAN_METHODS = new Set([2, 3, 11, 14]);

function plate(id) {
  const logos = METHOD_ICONS[id];
  return h("span.pay-plate", {}, ...(logos ? logos.map(icon) : [h("b", { text: MARKS[id] || "?" })]));
}

// "3 900 ₽" in the page's language; a currency the browser does not know stays as "3900.00 XYZ".
function money(amount, currency) {
  try {
    return new Intl.NumberFormat(locale(), { style: "currency", currency, maximumFractionDigits: 2,
      minimumFractionDigits: Number(amount) % 1 === 0 ? 0 : 2 }).format(Number(amount));
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return `${amount} ${currency}`;
  }
}

function summary(config, plan) {
  const charge = config.charge;
  if (plan === "hours") {
    return { name: t("pay.pack"), hours: config.hour_pack.hours, price: charge.prices.hours, period: t("pay.thisPeriod") };
  }
  if (plan === "remote") {
    return { name: t("pricing.planNames.remote"), hours: null, price: charge.prices.remote, period: t("pay.oneYear") };
  }
  const found = config.plans.find((item) => item.id === plan);
  return { name: t(`pricing.planNames.${plan}`), hours: found.hours, price: charge.prices[plan], period: t("pay.thirtyDays") };
}

export function choosePayment(config, plan) {
  const { methods, currency } = config.charge;
  const bought = summary(config, plan);
  modal((close) => {
    let chosen = methods[0];
    const problem = h("div", { "aria-live": "polite" });
    const warning = h("p.banner.err.pay-warning", { role: "note", text: t("pay.ruIp") });
    const showWarning = () => { warning.hidden = !RUSSIAN_METHODS.has(chosen); };
    const go = h("button.btn.wide", { type: "button", text: t("pay.go") });
    const tiles = methods.map((id) => h("button.pay-tile", { type: "button", role: "radio", "data-method": id,
      "aria-checked": String(id === chosen), onclick: () => {
        chosen = id;
        for (const tile of tiles) tile.setAttribute("aria-checked", String(Number(tile.dataset.method) === id));
        showWarning();
      } },
      plate(id),
      h("span.pay-name", { text: t(`pay.methods.${id}.name`) }),
      h("small", { text: t(`pay.methods.${id}.sub`) })));
    go.addEventListener("click", async () => {
      go.disabled = true;
      replace(problem);
      try {
        await startCheckout(plan, chosen);
      } catch (error) {
        go.disabled = false;
        replace(problem, errorBox(error));
      }
    });
    showWarning();
    return h("div.stack", {},
      h("h2.h3", {}, t("pay.title")),
      h("div.pay", {},
        h("div.stack", {},
          h("p.kicker", { text: t("pay.method") }),
          h("div.pay-methods", { role: "radiogroup", "aria-label": t("pay.method") }, ...tiles),
          warning),
        h("div.pay-total", {},
          h("p.kicker", { text: t("pay.total") }),
          h("p.pay-sum", { text: money(bought.price, currency) }),
          h("div.pay-rows", {},
            h("div", {}, h("span", { text: t("pay.item") }), h("span", { text: bought.name })),
            bought.hours === null
              ? h("div", {}, h("span", { text: t("pay.machine") }), h("span", { text: t("pay.yours") }))
              : h("div", {}, h("span", { text: t("pay.hours") }), h("span", { text: `${bought.hours} ${t("pay.h")}` })),
            h("div", {}, h("span", { text: t("pay.valid") }), h("span", { text: bought.period }))),
          go,
          h("p.hint", { text: t("pay.note") }))),
      problem,
      h("div.row", {}, h("button.btn.ghost", { type: "button", onclick: close, text: t("common.cancel") })));
  });
}
