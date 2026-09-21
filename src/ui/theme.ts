// One accent, one neutral ramp, three signal colours. Ink downgrades hex
// colours on terminals without truecolor.
export const color = {
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
} as const

// A bar down the left edge only: the panel look without a full frame.
export const BAR = { topLeft: "", top: "", topRight: "", left: "┃", bottomLeft: "", bottom: "", bottomRight: "", right: "" }

export const planStepColor: Record<string, string> = {
  inspect: "#7aa2f7",
  plan: "#bb9af7",
  edit: color.warn,
  verify: color.ok,
  done: color.ok,
}

export const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
