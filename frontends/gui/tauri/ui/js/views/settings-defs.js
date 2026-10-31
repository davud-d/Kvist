// What the settings popover offers, as data. A "command" row does exactly what
// the slash command does (docs/UI_API.md §8c), so the core stays the one place
// that knows how; an "interface" row only changes how this window looks.

import { runCommand } from "../actions.js";
import { restartApp, setTransparent } from "../bridge.js";
import { THEMES, windowState } from "../theme.js";
import { toast } from "./toast.js";
import { t } from "../i18n.js";
import { setPref } from "../store.js";
import { openCatalog, openRename, openSessions } from "./settings-dialogs.js";

const values = (list, fallback) => (list?.length ? list.map((item) => item.value) : fallback);

export const SETTINGS = [
  {
    section: "agent",
    rows: [
      { id: "role", label: "role", kind: "segment", options: (a) => values(a.manifest?.roles, ["auto", "coder", "reader", "devops", "chat"]), get: (a) => a.role, set: (a, v) => runCommand(a, "role", [v]) },
      { id: "effort", label: "effort", kind: "segment", options: (a) => values(a.manifest?.effort_levels, ["false", "low", "medium", "high", "xhigh", "max"]), get: (a) => a.effort, set: (a, v) => runCommand(a, "effort", [v]), compact: true },
      { id: "style", label: "style", kind: "select", options: (a) => values(a.manifest?.styles, []), get: () => "", set: (a, v) => runCommand(a, "style", [v]) },
      { id: "models", label: "models", kind: "action", set: (a) => openCatalog(a) },
      { id: "provider", label: "provider", kind: "action", set: (a) => runCommand(a, "provider") },
      { id: "sessions", label: "sessions", kind: "action", set: (a) => openSessions(a) },
      { id: "rename", label: "rename", kind: "action", set: (a) => openRename(a) },
      { id: "resume", label: "resume", kind: "action", set: (a) => runCommand(a, "resume") },
      { id: "changes", label: "changes", kind: "action", set: (a) => runCommand(a, "changes") },
      { id: "revert", label: "revert", kind: "action", set: (a) => runCommand(a, "revert") },
      { id: "export", label: "export", kind: "action", set: (a) => runCommand(a, "export") },
      { id: "api", label: "api settings", kind: "action", set: (a) => runCommand(a, "settings-api") },
      { id: "key", label: "change key", kind: "action", set: (a) => runCommand(a, "change-api") },
      { id: "verbose", label: "verbose", kind: "toggle", get: (a) => a.verbose, set: (a, v) => runCommand(a, "verbose", [String(v)]) },
    ],
  },
  {
    section: "interface",
    rows: [
      { id: "theme", label: "theme", kind: "segment", options: () => THEMES, get: (_, p) => p.theme, set: (_, v) => setPref("theme", v), local: true },
      { id: "size", label: "size", kind: "segment", options: () => ["1", "1.15", "1.3"], get: (_, p) => String(p.scale), set: (_, v) => setPref("scale", Number(v)), local: true, words: { 1: "100%", 1.15: "115%", 1.3: "130%" } },
      { id: "animations", label: "animations", kind: "toggle", get: (_, p) => p.speed > 0, set: (_, v) => setPref("speed", v ? 1 : 0), local: true },
      {
        id: "window", label: "window transparency", kind: "toggle", local: true,
        get: () => windowState.transparent,
        // The window is made once, so the choice is saved and the app starts again.
        set: async (_, value) => {
          await setTransparent(value);
          toast(t("restarting"));
          setTimeout(restartApp, 900);
        },
      },
      { id: "glass", label: "glass", kind: "slider", min: 0.3, max: 1, step: 0.02, get: (_, p) => p.glass, set: (_, v) => setPref("glass", v), local: true, when: () => windowState.transparent },
    ],
  },
];
