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

export function shortDate(iso, lang) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString(lang === "ru" ? "ru-RU" : "en-GB", { day: "numeric", month: "long" });
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
