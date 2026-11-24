// What the account's state means for the person: can a message be sent, what is the machine doing, what to
// say before it bites. Pure (no DOM, no fetch); the state is whatever account.js has loaded.

export const FINISHED = new Set(["done", "cancelled", "failed"]);
export const WORKING = new Set(["queued", "starting", "running"]);

const LOW_SECONDS = 30 * 60;
const LOW_SHARE = 0.1;

/** The reason a message cannot be sent yet, from what the account looks like; null when it can. */
export function cannotRun(account) {
  if (!account.sub || account.sub.status === "none") return "no_subscription";
  if (account.sub.balance_seconds <= 0) return "no_hours";
  if (!account.key?.set) return account.key?.provider === "chatgpt" ? "chatgpt_login" : "no_key";
  return null;
}

/** What the machine is doing in one word the UI knows: ready | working | waking | asleep | creating | off | none. */
export function machineState(machines) {
  const machine = machines?.[0];
  if (!machine) return "none";
  if (machine.updating) return "updating";
  switch (machine.display_status) {
    case "working": case "waiting_for_you": return "working";
    case "ready": case "awake": return "ready";
    case "waking": return "waking";
    case "creating": return "creating";
    case "asleep": case "sleeping": return "asleep";
    case "suspended": return "suspended";
    default: return "off";
  }
}

/** Setup the person still owes before the first task: a plan, a model key, a project. In this order. */
export function setupSteps(account) {
  return [
    { id: "plan", done: Boolean(account.sub && account.sub.status !== "none") },
    { id: "key", done: Boolean(account.key?.set) },
    { id: "project", done: (account.projects?.length ?? 0) > 0 },
  ];
}

/** Hours nearly gone: under half an hour, or a tenth of the plan. */
export function hoursLow(sub) {
  if (!sub || sub.status === "none" || sub.unmetered) return false;
  if (sub.balance_seconds <= 0) return false;
  return sub.balance_seconds < LOW_SECONDS || sub.balance_seconds < (sub.plan_seconds || 0) * LOW_SHARE;
}

/** A session as the list shows it. The id in the address is the opaque public one. */
export function listItem(task) {
  return {
    id: task.id,
    publicId: task.public_id,
    projectId: task.project_id == null ? "" : String(task.project_id),
    title: task.title || String(task.goal ?? "").trim().split("\n")[0].slice(0, 120),
    at: task.finished_at ?? task.started_at ?? task.created_at ?? "",
    working: WORKING.has(task.status),
    paused: task.status === "paused",
    failed: task.status === "failed",
  };
}

/** The plan to suggest first: the cheapest paid one, because nothing here renews by itself. */
export const firstPaid = (plans) => plans.find((p) => p.price_usd > 0);

/** Why a payment attempt failed, as a key of the page's own words. */
export function paymentProblem(error) {
  switch (error?.code) {
    case "card_declined": return "pay declined";
    case "wallet_needs_authentication": return "pay needs auth";
    case "payment_needs_review": return "pay review";
    case "too_many_checkouts": return "pay too many";
    case "already_subscribed": return "pay already";
    case "wallet_cancelled": return "";
    default: return "pay failed";
  }
}

const PAUSE_REASONS = new Set(["hours_exhausted", "provider_unavailable", "invalid_key", "command_time_limit", "machine_restarted",
  "machine_sleeping", "machine_suspended", "core_updated", "agent_error", "user"]);

/**
 * The one line to show above the composer, or null. Most urgent first: a lost connection, then whatever stops the
 * next message from running (plan, hours, key), then why a task stopped, then the hours running low, then the machine.
 * {id, level: "info" | "warn", text: i18n key, params, action: "plans" | "key" | null}
 */
export function banner({ account, task = null, link = "up" }) {
  if (link === "down") return { id: "link", level: "warn", text: "banner reconnecting", params: {}, action: null };
  if (!account.ready) return null;
  const blocked = cannotRun(account);
  if (blocked === "no_subscription") return { id: "plan", level: "warn", text: "banner no plan", params: {}, action: "plans" };
  if (blocked === "no_hours") return { id: "hours", level: "warn", text: "banner no hours", params: {}, action: "plans" };
  if (blocked) return { id: "key", level: "warn", text: "banner no key", params: {}, action: "key" };
  if (task?.status === "paused") {
    const reason = PAUSE_REASONS.has(task.reason) ? task.reason : "user";
    return { id: "paused", level: "info", text: `pause ${reason}`, params: {}, action: null };
  }
  if (task?.status === "failed") return { id: "failed", level: "warn", text: "banner failed", params: {}, action: null };
  if (hoursLow(account.sub)) return { id: "low", level: "warn", text: "banner low hours", params: { seconds: account.sub.balance_seconds }, action: "plans" };
  const machine = machineState(account.machines);
  if (machine === "waking" || machine === "creating") return { id: "machine", level: "info", text: `machine ${machine} long`, params: {}, action: null };
  if (machine === "suspended") return { id: "suspended", level: "warn", text: "machine suspended long", params: {}, action: null };
  return null;
}
