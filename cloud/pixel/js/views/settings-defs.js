// What the settings popover offers, as data. In the cloud there is no core to configure per session:
// the model is the person's own key, the plan and hours are the account's, and the rest is how this page looks.

import { account, signOut } from "../cloud.js";
import { chooseLanguage, lang, t } from "../i18n.js";
import { active, setPref } from "../store.js";
import { THEMES, switchTheme, themeOf } from "../theme.js";
import { modelName } from "../models.js";
import { effortDial } from "./effort.js";
import { openKeyDialog, openRename } from "./settings-dialogs.js";

function keyLabel() {
  const key = account.key;
  if (!key?.set) return `${t("model key")}: ${t("key not set")}`;
  if (key.provider === "chatgpt") return `${t("model key")}: ${key.label}${key.chatgpt_email ? ` · ${key.chatgpt_email}` : ""}`;
  const name = key.provider === "other" ? (key.model ? modelName(key.model) : t("model key")) : key.label;
  return `${t("model key")}: ${name} ••••${key.last4}`;
}

// Every row is `local`: none of them depends on what the agent is doing.
export const SETTINGS = [
  {
    section: "account",
    rows: [
      { id: "key", label: keyLabel, kind: "action", set: () => openKeyDialog(), local: true },
      { id: "effort", label: "effort", kind: "dial", build: () => effortDial(), local: true },
      { id: "rename", label: "rename", kind: "action", set: () => openRename(active()), local: true, when: () => Boolean(active()?.session) },
      { id: "plan", label: "manage plan", kind: "action", set: () => location.assign("/dashboard"), local: true },
      { id: "signout", label: "sign out", kind: "action", set: () => signOut(), local: true },
    ],
  },
  {
    section: "interface",
    rows: [
      { id: "language", label: "language", kind: "segment", options: () => ["en", "ru"], get: () => lang.current, set: (_, v) => v !== lang.current && chooseLanguage(v), local: true, words: { en: "EN", ru: "RU" } },
      { id: "theme", label: "theme", kind: "segment", options: () => THEMES, get: (_, p) => themeOf(p), set: (_, v, button) => switchTheme(v, button), local: true, words: { system: "theme system", light: "theme light", dark: "theme dark" } },
      { id: "size", label: "size", kind: "segment", options: () => ["1", "1.15", "1.3"], get: (_, p) => String(p.scale), set: (_, v) => setPref("scale", Number(v)), local: true, words: { 1: "100%", 1.15: "115%", 1.3: "130%" } },
      { id: "animations", label: "animations", kind: "toggle", get: (_, p) => p.speed > 0, set: (_, v) => setPref("speed", v ? 1 : 0), local: true },
    ],
  },
];
