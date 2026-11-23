// Shared by the top bar, settings and saved preferences.
export const THEMES = ["system", "light", "dark"];

export function workspaceTheme(theme) {
  if (theme === "paper") return "light";
  if (["dusk", "moss", "ember", "mono"].includes(theme)) return "dark";
  return THEMES.includes(theme) ? theme : "system";
}

export function applyPrefs(prefs) {
  const root = document.documentElement;
  root.dataset.theme = prefs.theme;
  root.dataset.motion = prefs.speed > 0 ? "on" : "off";
  root.style.setProperty("--zoom", String(prefs.scale));
}
