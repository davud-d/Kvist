// Two locales, both bundled (a few KB). The choice: an explicit pick (cookie kc_lang, also read
// by the server for the pricing page and emails), else the browser's language.
import en from "../i18n/en.json";
import ru from "../i18n/ru.json";

const LOCALES = { en, ru };
let current = en;

export function detectLocale(cookie = document.cookie, languages = navigator.languages || [navigator.language]) {
  const chosen = /(?:^|;\s*)kc_lang=(en|ru)/.exec(cookie || "");
  if (chosen) return chosen[1];
  return (languages || []).some((lang) => String(lang).toLowerCase().startsWith("ru")) ? "ru" : "en";
}

export function setLocale(lang) {
  current = LOCALES[lang] || en;
  document.documentElement.lang = current.lang;
  return current.lang;
}

export function locale() {
  return current.lang;
}

export function rememberLocale(lang) {
  document.cookie = `kc_lang=${lang}; path=/; max-age=31536000; samesite=lax`;
}

function lookup(table, key) {
  return key.split(".").reduce((node, part) => (node == null ? undefined : node[part]), table);
}

/** t("task.step", {n: 2}) → "Step 2". Missing keys show the key, so a gap is visible, not silent. */
export function t(key, vars = {}) {
  const value = lookup(current, key) ?? lookup(en, key);
  if (typeof value !== "string") return value ?? key;
  return value.replace(/\{(\w+)\}/g, (_, name) => (name in vars ? String(vars[name]) : `{${name}}`));
}

export function forLocale(lang) {
  return LOCALES[lang] || en;
}
