const INK = {
  accent: "#4fc3f7",
  text: "#d5dde3",
  dim: "#7b8b96",
  faint: "#4a5a65",
  ok: "#3fc47a",
  warn: "#e0b23c",
  err: "#f2565b",
  code: "#93d7ff",
  thought: "#b8954a",
  panel: "#182229",
  // Text on an accent-filled row (the highlighted dialog item) and its muted twin.
  onAccent: "#08141a",
  onAccentDim: "#2b5163",
  delBg: "#3a1d22",
  addBg: "#173324",
  // The horizontal rules around the input in the claude skin; unused by the ink skin.
  rule: "#4a5a65",
  // The highlighted suggestion or dialog option in the claude skin.
  select: "#4fc3f7",
  // The bullet of a call that changes a file, and the number and sign of an added or removed line
  // in the claude skin's diff.
  file: "#4fc3f7",
  addFg: "#3fc47a",
  delFg: "#f2565b",
}

export type Palette = { [K in keyof typeof INK]: string }

// The claude skin copies the look of Claude Code's dark theme.
const CLAUDE: Palette = {
  accent: "#d77757",
  text: "#f2f2f2",
  dim: "#999999",
  faint: "#6c6c6c",
  ok: "#4eba65",
  warn: "#ffc107",
  err: "#ff6b80",
  code: "#b1b9f9",
  thought: "#999999",
  panel: "#373737",
  onAccent: "#000000",
  onAccentDim: "#3a3a3a",
  delBg: "#4d161c",
  addBg: "#1a4a25",
  rule: "#888888",
  select: "#b1b9f9",
  file: "#3d8ff0",
  addFg: "#4eba65",
  delFg: "#ff6b80",
}

export type Skin = "ink" | "claude"

export const SKINS: readonly Skin[] = ["ink", "claude"]

// One skin per process, chosen before the first render: the shared components
// read `color` directly, so switching it re-themes all of them at once.
export const color: Palette = { ...INK }

let current: Skin = "ink"

export function applySkin(skin: Skin): void {
  current = skin
  Object.assign(color, skin === "claude" ? CLAUDE : INK)
}

export function currentSkin(): Skin {
  return current
}

// A bar down the left edge only: the panel look without a full frame.
export const BAR = { topLeft: "", top: "", topRight: "", left: "┃", bottomLeft: "", bottom: "", bottomRight: "", right: "" }

export const planStepColor: Record<string, string> = {
  inspect: "#7aa2f7",
  plan: "#bb9af7",
  edit: INK.warn,
  verify: INK.ok,
  done: INK.ok,
}

export const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
