// The page's door to the phone app around it. In a plain browser there is no app: wallets are absent, links open
// in a tab, haptics do nothing. In the Android and iOS apps the host injects `window.KvistHost`:
//
//   KvistHost.platform                       "android" | "ios"
//   KvistHost.call(id, name, json)           starts an async request; the host answers with
//                                            window.__kvistReply(id, resultJson) when it is done
//
// Names the host understands: "wallets" (-> ["google_pay"] or ["apple_pay"], what this phone can pay with now),
// "pay" ({wallet, request, environment} -> {token} | {cancelled: true} | {error}), "open" ({url}), "haptic" ({kind}),
// "payDone" ({wallet, ok}), "signIn" (-> {started} once the app has taken the sign-in over), "share" ({text}), "copy" ({text}). The page never holds a payment card: the OS sheet hands it a token.

//
// Framed in an IDE panel (VS Code, JetBrains: frontends/ide/vscode/webview/cloud.tsx) the page has no injected host;
// the panel around it is the host, spoken to with postMessage: {kvistCloud: {id, name, args}} up, and
// {kvistCloudReply: {id, result}} back. It answers "signIn", "open" and "copy"; anything else is answered null.

let counter = 0;
const waiting = new Map();

window.__kvistReply = (id, payload) => {
  const resolve = waiting.get(id);
  if (!resolve) return;
  waiting.delete(id);
  resolve(typeof payload === "string" ? JSON.parse(payload) : payload);
};

/** The page is the cloud half of an IDE panel. */
export const inIde = () => window.parent !== window;

const ideHost = {
  platform: "ide",
  call(id, name, json) {
    window.parent.postMessage({ kvistCloud: { id, name, args: JSON.parse(json) } }, "*");
  },
};

window.addEventListener("message", (event) => {
  const reply = event.data?.kvistCloudReply;
  if (event.source !== window.parent || !reply) return;
  window.__kvistReply(reply.id, reply.result ?? null);
});

export const host = () => window.KvistHost ?? (inIde() ? ideHost : null);
export const inApp = () => host() !== null;
export const platform = () => host()?.platform ?? "web";
/** An app from Google Play or the App Store: nothing is sold in it and nothing points to buying elsewhere (Apple's
 * 3.1.3(f) for a free companion of a paid web service; Google Play's payments policy). Plans and hours bought on the
 * site still work here. A browser and the sideloaded apps sell as usual. */
export const storeBuild = () => host()?.store === true;

function call(name, args = {}) {
  const app = host();
  if (!app) return Promise.resolve(null);
  const id = `c${++counter}`;
  return new Promise((resolve) => {
    waiting.set(id, resolve);
    app.call(id, name, JSON.stringify(args));
  });
}

/** The wallets this phone can pay with right now, e.g. ["google_pay"]. */
export async function wallets() {
  const found = await call("wallets");
  return Array.isArray(found) ? found.filter((name) => name === "google_pay" || name === "apple_pay") : [];
}

/**
 * Opens the OS payment sheet. `environment` is the server's word for Google Pay ("TEST" or "PRODUCTION").
 * Resolves to {token} or {cancelled: true}; rejects with the host's error code.
 */
export async function pay(wallet, request, environment = "TEST") {
  const result = await call("pay", { wallet, request, environment });
  if (!result) throw Object.assign(new Error("no wallet"), { code: "wallet_unavailable" });
  if (result.error) throw Object.assign(new Error(result.error), { code: result.error });
  return result;
}

/**
 * Tells the app whether the server charged the wallet's token. Apple's sheet stays open, spinning, until it hears it,
 * and then shows its checkmark or its failure; Google's sheet closes when it hands over the token, so for it this is nothing.
 */
export const payDone = (wallet, ok) => (inApp() ? call("payDone", { wallet, ok }) : Promise.resolve());

/**
 * Signs in through the system browser's own session (GitHub's and Google's pages refuse to run in an app's web view).
 * Resolves true when the app took it over (it reloads the page signed in), false when the host cannot: the page
 * then signs in the ordinary way.
 */
export async function signIn() {
  const result = await call("signIn");
  return Boolean(result);
}

/**
 * Apple's own sign-in sheet (the iOS app only). `nonce` is the server's: the app puts its SHA-256 into the request.
 * Resolves to {identityToken, authorizationCode, name} | {cancelled: true} | {error} | null (no such sheet here).
 */
export const appleSignIn = (nonce) => call("appleSignIn", { nonce });

/** A link goes to the system browser from the app (a payment page or a repository), to a new tab elsewhere. */
export function open(url) {
  if (inApp()) return call("open", { url });
  window.open(url, "_blank", "noopener,noreferrer");
  return Promise.resolve();
}

/** A tap's tiny vibration: "tick" | "success" | "warn". */
export const haptic = (kind = "tick") => (inApp() ? call("haptic", { kind }) : Promise.resolve());

export async function copy(text) {
  if (inApp()) return call("copy", { text });
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Not allowed outside a secure page; the text stays selectable.
  }
  return null;
}

export const share = (text) => (inApp() ? call("share", { text }) : Promise.resolve());
