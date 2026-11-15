// Pure formatting helpers; no DOM, so node --test covers them.

/** Billed per second, shown in whole minutes rounded down. */
export function wholeMinutes(seconds) {
  return Math.floor(Math.max(0, Number(seconds) || 0) / 60);
}

/** "48 min" or "4 h 48 min" through the locale's units. */
export function hoursMinutes(seconds, tr) {
  const minutes = wholeMinutes(seconds);
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return tr("units.min", { m });
  if (m === 0) return tr("units.h", { h });
  return tr("units.hm", { h, m });
}

// A month-long plan ends "6 November"; a year-long one needs the year, or it reads as the coming one.
const FAR = 60 * 24 * 3600 * 1000;

export function shortDate(iso, lang) {
  if (!iso) return "";
  const date = new Date(iso);
  const far = date.getTime() - Date.now() > FAR;
  return date.toLocaleDateString(lang === "ru" ? "ru-RU" : "en-GB",
    far ? { day: "numeric", month: "long", year: "numeric" } : { day: "numeric", month: "long" });
}

export function dateTime(iso, lang) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(lang === "ru" ? "ru-RU" : "en-GB", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

export function percentLeft(balance, total) {
  if (!total) return 0;
  return Math.max(0, Math.min(100, Math.round((Math.max(0, balance) / total) * 100)));
}

// A forecast is a hint, not a promise: the pace of the whole period carried forward.
const MIN_PACE_MS = 60 * 60 * 1000; // under an hour of history a pace is noise
const TIGHT_MS = 3 * 3600 * 1000; // "a couple of hours earlier" before the next charge
const LOWER_ELAPSED = 0.2; // before a fifth of the period a downgrade hint would be a guess
const LOWER_SHARE = 1 / 3; // the plan tiers differ about threefold

/**
 * When the balance runs out at this period's pace, for the line under the hours.
 * `usedSeconds` is what the period's tasks used; `periodStart`/`periodEnd` are ISO strings.
 * Returns null when a forecast would be a guess (no hours, no usage, under an hour of history),
 * else { tone, at, used, total, projected }; `at` is epoch ms and for "lower" it is the next charge.
 * "empty" is the balance at zero; "lower" means the pace fits a cheaper plan; "plenty", "tight"
 * and "early" compare the runout with the next charge.
 */
export function runoutForecast({ balanceSeconds, usedSeconds, planSeconds, periodStart, periodEnd,
  lowerable = false, nowMs = Date.now() }) {
  if (!(Number(planSeconds) > 0)) return null;
  const balance = Math.max(0, Number(balanceSeconds) || 0);
  const used = Math.max(0, Number(usedSeconds) || 0);
  const total = balance + used;
  if (balance === 0) return { tone: "empty", at: null, used, total, projected: 0 };
  const start = Date.parse(periodStart);
  const end = Date.parse(periodEnd);
  const now = Number(nowMs) || Date.now();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= now || used === 0) return null;
  const elapsed = now - start;
  if (elapsed < MIN_PACE_MS) return null;
  const runout = now + (balance * elapsed) / used;
  const span = end - start;
  const projected = span > 0 ? (used * span) / elapsed : used;
  if (runout >= end) {
    const lower = lowerable && elapsed >= span * LOWER_ELAPSED && projected < total * LOWER_SHARE;
    return { tone: lower ? "lower" : "plenty", at: lower ? end : runout, used, total, projected };
  }
  return { tone: end - runout <= TIGHT_MS ? "tight" : "early", at: runout, used, total, projected };
}

/** A one-line command from Kvist's argv. */
export function commandLine(args) {
  const command = args && args.command;
  if (Array.isArray(command)) return command.map((part) => (/\s/.test(part) ? JSON.stringify(part) : part)).join(" ");
  return typeof command === "string" ? command : "";
}

/** Split a unified diff into typed lines for display. */
export function diffLines(patch) {
  return String(patch || "")
    .split("\n")
    .filter((line, index, all) => !(index === all.length - 1 && line === ""))
    .map((line) => {
      if (line.startsWith("+++") || line.startsWith("---")) return { kind: "file", text: line };
      if (line.startsWith("@@")) return { kind: "hunk", text: line };
      if (line.startsWith("+")) return { kind: "add", text: line };
      if (line.startsWith("-")) return { kind: "del", text: line };
      return { kind: "ctx", text: line };
    });
}
