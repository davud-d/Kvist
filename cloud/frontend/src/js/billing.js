// The server creates every purchase and names the page to pay on; the browser only goes there.
// Whatever happens on that page proves nothing: access appears when the server has asked Platega.
import { api } from "./api.js";

export async function startCheckout(plan, method) {
  const result = await api("/api/billing/checkout", { method: "POST", body: method ? { plan, method } : { plan } });
  // The server answers {"started": "trial"}; callers only need to know that nothing is left to pay.
  if (!result.redirect) return "started";
  if (new URL(result.redirect).protocol !== "https:") {
    throw Object.assign(new Error("redirect"), { code: "billing_unavailable" });
  }
  location.assign(result.redirect);
  return "redirect";
}

// "Check my payments": the server asks Platega about this person's open payments and answers with the plan.
export function syncPayments() {
  return api("/api/billing/sync", { method: "POST" });
}

export function chargeLabel(config, key, usd) {
  const charge = config.charge;
  return charge ? `${charge.prices[key]} ${charge.currency}` : `$${usd}`;
}
