// What Kvist Cloud knows about the signed-in person: the plan and hours, the model key, the machine, the projects
// and the sessions. The rest of the page reads `account` and listens for changes; only this module fetches them.

import { api } from "./api.js";
import * as native from "./native.js";

export const account = {
  me: null,
  sub: null,
  key: null,
  machines: [],
  projects: [],
  tasks: [],
  // The shared web search: free searches left, bought ones, the pack (search.py); enabled false = none here.
  searches: null,
  billing: null,
  wallet: { enabled: false },
  // What this phone's own wallet can pay with right now (the app answers; a browser has none).
  wallets: [],
  providers: [],
  ready: false,
};

const listeners = new Set();
export const onAccount = (fn) => {
  listeners.add(fn);
  return () => listeners.delete(fn);
};
export const changed = () => listeners.forEach((fn) => fn(account));

const loaders = {
  sub: () => api("/api/billing/subscription"),
  key: () => api("/api/model-key"),
  machines: () => api("/api/machines"),
  projects: () => api("/api/projects"),
  tasks: () => api("/api/tasks"),
  searches: () => api("/api/search/usage"),
};

/** Re-reads one part (or several) of the account; a part that fails keeps what it had. */
export async function refresh(...parts) {
  await Promise.all(parts.map(async (part) => {
    try {
      account[part] = await loaders[part]();
    } catch (error) {
      if (error.status === 401) throw error;
    }
  }));
  changed();
}

export const refreshHours = () => refresh("sub");

/** First load after sign-in. Static things (plans, providers, wallet) are read once. */
export async function load() {
  const [billing, wallet, providers, wallets] = await Promise.all([
    api("/api/billing/config").catch(() => null),
    api("/api/wallet/config").catch(() => ({ enabled: false })),
    api("/api/model-providers").catch(() => []),
    native.wallets(),
  ]);
  Object.assign(account, { billing, wallet, providers, wallets });
  await refresh("sub", "key", "machines", "projects", "tasks", "searches");
  account.ready = true;
  changed();
}

/** The wallet this phone will offer for paying, or "" when there is none (or the server has it switched off). */
export function walletToUse() {
  if (!account.wallet?.enabled) return "";
  return account.wallets.find((name) => account.wallet[name]) ?? "";
}

export const projectName = (id) => account.projects.find((p) => String(p.id) === String(id))?.name ?? "";
