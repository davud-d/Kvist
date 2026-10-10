// What colours the terminal can show, and how a colour is written for it.
export type RGB = readonly [number, number, number]

// "none" means the picture is skipped and only bold, dim and inverse video are used.
export type ColorLevel = "truecolor" | "256" | "none"

const TRUECOLOR_TERMS = /kitty|alacritty|foot|ghostty|wezterm|direct|contour/i

export function detectColorLevel(env: NodeJS.ProcessEnv = process.env): ColorLevel {
  if (env.NO_COLOR !== undefined && env.NO_COLOR !== "") return "none"
  const term = env.TERM ?? ""
  if (term === "dumb" || term === "linux") return "none"
  if (/truecolor|24bit/i.test(env.COLORTERM ?? "")) return "truecolor"
  if (env.WT_SESSION || env.KONSOLE_VERSION || ["iTerm.app", "WezTerm", "vscode", "ghostty"].includes(env.TERM_PROGRAM ?? "")) return "truecolor"
  if (TRUECOLOR_TERMS.test(term)) return "truecolor"
  // VTE gained 24-bit colour in 0.36.
  if (Number(env.VTE_VERSION ?? 0) >= 3600) return "truecolor"
  return /256color/i.test(term) ? "256" : "none"
}

// Nearest xterm-256 entry: the 6x6x6 cube or the grey ramp, whichever is closer.
export function toXterm256([r, g, b]: RGB): number {
  const level = (v: number) => (v < 48 ? 0 : v < 115 ? 1 : Math.min(5, Math.floor((v - 35) / 40)))
  const steps = [0, 95, 135, 175, 215, 255]
  const [ri, gi, bi] = [level(r), level(g), level(b)]
  const cube: RGB = [steps[ri]!, steps[gi]!, steps[bi]!]
  const grey = Math.max(0, Math.min(23, Math.round((((r + g + b) / 3) - 8) / 10)))
  const greyValue = 8 + 10 * grey
  const distance = (a: RGB, c: RGB) => (a[0] - c[0]) ** 2 + (a[1] - c[1]) ** 2 + (a[2] - c[2]) ** 2
  return distance([r, g, b], [greyValue, greyValue, greyValue]) < distance([r, g, b], cube)
    ? 232 + grey
    : 16 + 36 * ri + 6 * gi + bi
}

// SGR parameters selecting a foreground (`bg` false) or background colour.
export function colorCode(level: ColorLevel, rgb: RGB, bg: boolean): string {
  if (level === "truecolor") return `${bg ? 48 : 38};2;${rgb[0]};${rgb[1]};${rgb[2]}`
  return `${bg ? 48 : 38};5;${toXterm256(rgb)}`
}

export function mix(a: RGB, b: RGB, amount: number): RGB {
  return [
    Math.round(a[0] + (b[0] - a[0]) * amount),
    Math.round(a[1] + (b[1] - a[1]) * amount),
    Math.round(a[2] + (b[2] - a[2]) * amount),
  ]
}

export function scale(rgb: RGB, factor: number): RGB {
  return [Math.min(255, Math.round(rgb[0] * factor)), Math.min(255, Math.round(rgb[1] * factor)), Math.min(255, Math.round(rgb[2] * factor))]
}

export function pack(rgb: RGB): number {
  return (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
}

export function unpack(value: number): RGB {
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255]
}
