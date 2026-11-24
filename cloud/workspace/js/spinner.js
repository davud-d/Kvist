// The line shown while a turn runs, ported from the Ink app's Claude-style skin
// (frontends/tui/ink/ui/skins/claude-code/shimmer.ts and verbs.ts): a word that
// changes now and then, a ripple of colour travelling along it, and a colour that
// warms from blue through orange and gold to red and violet as the wait for the model grows.

import { VERBS, VERBS_RU } from "./verbs.js";

const COOL = [79, 195, 247];
const COOL_LIGHT = [150, 225, 255];
const ORANGE = [255, 140, 50];
const GOLD = [255, 200, 40];
const RED = [255, 95, 80];
const RED_DEEP = [225, 40, 50];
const ULTRA = [205, 50, 235];

// Seconds of waiting: orange starts to mix in at WARM_FROM and is complete at WARM_TO; gold takes
// over between GOLD_FROM and GOLD_TO, red between RED_FROM and RED_TO, the deep red between
// DEEP_FROM and DEEP_TO and the violet between ULTRA_FROM and ULTRA_TO.
const WARM_FROM = 1, WARM_TO = 5, GOLD_FROM = 5, GOLD_TO = 25;
const RED_FROM = 45, RED_TO = 100, DEEP_FROM = 120, DEEP_TO = 180, ULTRA_FROM = 300, ULTRA_TO = 600;
// Orange that already shimmers through the blue before the wait grows.
const IDLE_ORANGE = 0.4;
const PERIOD_MS = 2400;
const PHASE_PER_CHAR = 0.28;
// The gap to the real wait closes by a factor of e every this many seconds.
const COOL_DOWN_SECONDS = 1;
const ROTATE_SECONDS = 8;
const CYRILLIC = /\p{Script=Cyrillic}/u;

function smooth(from, to, value) {
  const x = Math.min(1, Math.max(0, (value - from) / (to - from)));
  return x * x * (3 - 2 * x);
}

function mix(a, b, amount) {
  return [0, 1, 2].map((i) => Math.round(a[i] + (b[i] - a[i]) * amount));
}

const hex = (rgb) => `#${rgb.map((v) => v.toString(16).padStart(2, "0")).join("")}`;

/** Colour of one character of the line, as a `#rrggbb` string. */
export function shimmerColor(waitingSeconds, nowMs, index) {
  const ripple = 0.5 + 0.5 * Math.sin((2 * Math.PI * nowMs) / PERIOD_MS - index * PHASE_PER_CHAR);
  const warmth = smooth(WARM_FROM, WARM_TO, waitingSeconds);
  const gold = smooth(GOLD_FROM, GOLD_TO, waitingSeconds);
  const cool = mix(COOL, COOL_LIGHT, 0.5 * ripple * (1 - warmth));
  const deep = mix(RED, RED_DEEP, smooth(DEEP_FROM, DEEP_TO, waitingSeconds));
  const red = mix(deep, ULTRA, smooth(ULTRA_FROM, ULTRA_TO, waitingSeconds));
  const hot = mix(mix(ORANGE, GOLD, gold), red, smooth(RED_FROM, RED_TO, waitingSeconds));
  const amount = warmth * (0.25 + 0.75 * ripple) + (1 - warmth) * IDLE_ORANGE * ripple * ripple;
  return hex(mix(cool, hot, amount));
}

/** The wait to colour with: rises at once with the real wait, falls back gradually once the model answers. */
export function coolDown(shown, target, elapsedSeconds) {
  if (target >= shown) return target;
  return target + (shown - target) * Math.exp(-elapsedSeconds / COOL_DOWN_SECONDS);
}

export function formatSeconds(seconds) {
  const total = Math.round(seconds);
  return total < 60 ? `${total}s` : `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, "0")}s`;
}

export function tokenCount(count) {
  return count < 1000 ? String(count) : `${(count / 1000).toFixed(1)}k`;
}

/** What the agent is "doing", in words: chat generates a reply, an unrouted turn picks a role, the rest rotate. */
export function spinnerWord(role, startedAt, elapsedSeconds, request = "") {
  const russian = CYRILLIC.test(request);
  if (role === "chat") return russian ? "Генерирую ответ" : "Generating a response";
  if (role === "auto" || role === "") return russian ? "Выбираю роль" : "Picking a role";
  const list = russian ? VERBS_RU : VERBS;
  const step = Math.floor(Math.max(0, elapsedSeconds) / ROTATE_SECONDS);
  return list[(Math.floor(startedAt / 1000) + step) % list.length] ?? (russian ? "Работаю" : "Working");
}

