// Checkout identities come from the authenticated backend. Browser events only trigger a refresh.
import { api } from "./api.js";

let loading;
let initialized = false;
let completed = () => {};

function loadPaddle() {
  if (window.Paddle) return Promise.resolve(window.Paddle);
  if (!loading) loading = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://cdn.paddle.com/paddle/v2/paddle.js";
    script.onload = () => resolve(window.Paddle);
    script.onerror = () => { loading = null; reject(Object.assign(new Error("paddle"), { code: "billing_unavailable" })); };
    document.head.append(script);
  });
  return loading;
}

export async function openCheckout(config, plan, onComplete) {
  const checkout = await api("/api/billing/checkout", { method: "POST", body: { plan } });
  const Paddle = await loadPaddle();
  completed = onComplete;
  if (!initialized) {
    if (config.paddle.environment === "sandbox") Paddle.Environment.set("sandbox");
    Paddle.Initialize({ token: config.paddle.client_token, eventCallback: (event) => {
      if (event.name === "checkout.completed") completed();
    } });
    initialized = true;
  }
  Paddle.Checkout.open({ transactionId: checkout.transaction_id,
    settings: { displayMode: "overlay", locale: document.documentElement.lang, allowDiscountRemoval: false, showAddDiscounts: false } });
}
