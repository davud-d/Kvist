// The web workspace's t() and counted() for the files vendored beside this one (items.js), in the phone page's
// language. The words are the web's own (cloud/workspace/js/i18n.js), so the two say the same thing.

import { lang } from "../i18n.js";

const WORDS = {
  en: { thinking: "Thinking", "thought for": "Thought for", sec: "s", "working now": "Working", "work done": "Done", "task plan": "Plan", paused: "paused", photo: "Photo" },
  ru: { thinking: "Думаю", "thought for": "Думала", sec: "с", "working now": "Работает", "work done": "Сделано", "task plan": "План", paused: "пауза", photo: "Фото" },
};

export function t(key) {
  return WORDS[lang.current]?.[key] ?? WORDS.en[key] ?? key;
}

/** "5 действий" / "5 actions": `ru` is [one, few, many], `en` is [one, many]. */
export function counted(n, ru, en, language = lang.current) {
  if (language !== "ru") return `${n} ${n === 1 ? en[0] : en[1]}`;
  const tens = n % 100;
  const ones = n % 10;
  const form = tens > 10 && tens < 20 ? 2 : ones === 1 ? 0 : ones >= 2 && ones <= 4 ? 1 : 2;
  return `${n} ${ru[form]}`;
}
