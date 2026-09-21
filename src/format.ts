export function formatSeconds(seconds: number): string {
  const total = Math.round(seconds)
  return total < 60 ? `${total}s` : `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, "0")}s`
}

// Short spans keep a decimal ("1.2s"): most thoughts take a second or two,
// and rounding them to whole seconds would make every one read the same.
export function formatThought(seconds: number): string {
  if (seconds <= 0) return "Thought"
  return `Thought: ${seconds < 10 ? `${seconds.toFixed(1)}s` : formatSeconds(seconds)}`
}

export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1000) return `${(value / 1000).toFixed(1)}K`
  return String(value)
}

export function contextLabel(context: number, limit: number): string {
  if (context <= 0) return ""
  if (limit <= 0) return formatTokens(context)
  return `${formatTokens(context)} (${Math.round((context / limit) * 100)}%)`
}

export function lastLines(text: string, count: number): string {
  const lines = text.split("\n")
  return lines.length <= count ? text : ["…", ...lines.slice(-count + 1)].join("\n")
}

const ANSI = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g

// Command output as printable lines: escape codes would corrupt the layout,
// and a carriage return means "overwrite the line" (progress bars).
export function outputLines(text: string): string[] {
  const clean = text.replace(ANSI, "").replace(/\r\n/g, "\n")
  const lines = clean.split("\n").map((line) => line.split("\r").at(-1) ?? "")
  while (lines.length && lines.at(-1)?.trim() === "") lines.pop()
  return lines
}

// The first `max` lines and how many were left out.
export function clipLines(lines: string[], max: number): { shown: string[]; more: number } {
  return { shown: lines.slice(0, max), more: Math.max(0, lines.length - max) }
}

// Exactly `width` cells: a background painted over a row only reaches the
// edge if the text is padded to it, and a long line must not wrap into the
// next row. Counted in code points, so a wide glyph may still overhang.
export function fit(text: string, width: number): string {
  if (width <= 0) return ""
  const chars = [...text.replace(/\t/g, "    ")]
  if (chars.length <= width) return chars.join("") + " ".repeat(width - chars.length)
  return chars.slice(0, width - 1).join("") + "…"
}

export function clip(text: string, max: number): string {
  const flat = text.replace(/\s+/g, " ").trim()
  return flat.length > max ? flat.slice(0, max - 1) + "…" : flat
}

const CYRILLIC = /\p{Script=Cyrillic}/u
const FIRST_LETTER = /\p{L}/u

// The core writes bilingual "English / translation" messages; this interface is
// English only, so the translation is dropped. The split point is the " / "
// whose right side starts with a Cyrillic letter and whose left side has none,
// so a slash inside either half ("auto / coder") is not mistaken for it. A line
// without such a point is shown whole.
export function englishOnly(text: string): string {
  return text
    .split("\n")
    .map((line) => {
      const parts = line.split(" / ")
      for (let i = 1; i < parts.length; i++) {
        const left = parts.slice(0, i).join(" / ")
        const first = parts.slice(i).join(" / ").match(FIRST_LETTER)?.[0] ?? ""
        if (!CYRILLIC.test(left) && CYRILLIC.test(first)) return left
      }
      return line
    })
    .join("\n")
}

// A long report (/changes) must not scroll the whole screen away.
export function clipText(text: string, maxLines: number): string {
  const { shown, more } = clipLines(text.replace(/\s+$/, "").split("\n"), maxLines)
  return more ? [...shown, `… ${more} more lines`].join("\n") : shown.join("\n")
}
