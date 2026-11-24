// Small pure helpers: how long, how long ago, how much. No DOM, no fetch (node --test covers them).

import { lang } from "./i18n.js";

const UNITS = { en: ["h", "min"], ru: ["ч", "мин"] };

export const wholeMinutes = (seconds) => Math.floor(Math.max(0, Number(seconds) || 0) / 60);

/** "4 h 48 min": minutes are rounded down, as they are billed. */
export function duration(seconds, language = lang.current) {
  const minutes = wholeMinutes(seconds);
  const [h, m] = UNITS[language] ?? UNITS.en;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest} ${m}`;
  return rest === 0 ? `${hours} ${h}` : `${hours} ${h} ${rest} ${m}`;
}

/** "2:05" or "1:02:05" for a running clock. */
export function clock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const s = String(total % 60).padStart(2, "0");
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** The share of the plan's hours still left, 0..1. */
export function share(balance, total) {
  if (!total) return 0;
  return Math.max(0, Math.min(1, Math.max(0, balance) / total));
}

/** A relative day for the list of sessions: today, yesterday, this week, or a date. */
export function dayBucket(iso, now = new Date()) {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "older";
  const startOf = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(then)) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 7) return "week";
  return "older";
}

/** Money as the server states it: "39.00" in USD becomes "$39". */
export function money(amount, currency) {
  const value = Number(amount);
  if (!Number.isFinite(value)) return "";
  const symbol = { USD: "$", EUR: "€", GBP: "£" }[currency] ?? `${currency} `;
  return `${symbol}${Number.isInteger(value) ? value : value.toFixed(2)}`;
}

export const firstLine = (text) => String(text ?? "").trim().split("\n")[0].slice(0, 120);

/** The name the person gave it, or the first line of what they asked. */
export const titleOf = (task) => task.title || firstLine(task.goal);

/** "owner/repo" out of a repository link. */
export function repoLabel(url) {
  if (!url) return "";
  return String(url).replace(/\.git$/, "").split("/").filter(Boolean).slice(-2).join("/");
}

/** The messages of the server are "English. / Русский."; this takes the half in the page's language. */
export function localized(message, language = lang.current) {
  const text = String(message ?? "");
  const parts = text.split(" / ");
  if (parts.length < 2) return text;
  return language === "ru" ? parts.slice(1).join(" / ") : parts[0];
}
