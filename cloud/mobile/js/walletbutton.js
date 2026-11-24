// The payment buttons the two platforms require: Apple's is drawn by WebKit itself (-apple-pay-button), Google's by
// Google's own script, so each carries its maker's mark, size and wording and never an imitation of ours.
// Both only call `onClick`; the page then asks the app to open the operating system's sheet.

import { lang, t } from "./i18n.js";
import { h } from "./dom.js";

const GOOGLE_SCRIPT = "https://pay.google.com/gp/p/js/pay.js";
let googleLoading = null;

function loadGoogle() {
  googleLoading ??= new Promise((resolve, reject) => {
    if (window.google?.payments?.api) return resolve();
    const script = h("script", { src: GOOGLE_SCRIPT, async: true });
    script.addEventListener("load", () => resolve());
    script.addEventListener("error", () => {
      googleLoading = null;
      reject(new Error("pay.js"));
    });
    document.head.append(script);
    return undefined;
  });
  return googleLoading;
}

function applePayButton(onClick) {
  return h("button", { class: "apple-pay-button", type: "button", lang: lang.current, aria: { label: t("pay apple") }, onclick: onClick });
}

/** A plain button that says the wallet's name, for when Google's script cannot load (offline, blocked): it still works. */
function googleFallback(onClick) {
  return h("button", { class: "btn wallet-fallback", type: "button", onclick: onClick }, t("pay google"));
}

function googleButton(onClick, environment) {
  const slot = h("div", { class: "google-pay-slot" });
  loadGoogle().then(() => {
    const client = new window.google.payments.api.PaymentsClient({ environment });
    slot.replaceChildren(client.createButton({
      onClick,
      buttonType: "pay",
      // Light on a dark page, as Google's guidelines ask.
      buttonColor: "white",
      buttonSizeMode: "fill",
      buttonLocale: lang.current,
      allowedPaymentMethods: [{ type: "CARD", parameters: { allowedAuthMethods: ["PAN_ONLY", "CRYPTOGRAM_3DS"], allowedCardNetworks: ["AMEX", "DISCOVER", "JCB", "MASTERCARD", "VISA"] } }],
    }));
  }).catch(() => slot.replaceChildren(googleFallback(onClick)));
  return slot;
}

export function walletButton(wallet, onClick, environment = "TEST") {
  return wallet === "apple_pay" ? applePayButton(onClick) : googleButton(onClick, environment);
}
