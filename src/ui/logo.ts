// The Kvist wordmark for the claude skin's welcome block, from the glyphs
// splash.py draws (one "#" per pixel). Two sizes: the large mark paints each
// pixel as "██" (a cell is about twice as tall as wide, so the pixels come out
// square) and is 6 rows high; the compact one packs two pixel rows into one
// text row (▀ ▄ █) and is 3 rows high, for windows too narrow for the large one.
const GLYPHS: Record<string, readonly string[]> = {
  K: ["#    #", "#   # ", "#  #  ", "###   ", "#   # ", "#    #"],
  v: ["     ", "     ", "#   #", "#   #", " # # ", "  #  "],
  i: ["#", " ", "#", "#", "#", "#"],
  s: ["     ", " ####", "#    ", "#####", "    #", " ####"],
  t: ["   ", " # ", "###", " # ", " # ", " ##"],
}

// The colours of assets/exit_logo.png: "Kvi" in blue, "st" in cyan.
export const LOGO_COLORS = { shade: "#1f6fe0", main: "#12c8ff" } as const

export type LogoSize = "large" | "compact"

// Width in cells, and the narrowest window that leaves room for the text beside it.
export const LOGO_WIDTH: Record<LogoSize, number> = { large: 48, compact: 24 }
export const LARGE_LOGO_MIN_COLUMNS = 84

export interface LogoRow {
  shade: string
  main: string
}

function pixels(word: string): string[] {
  return Array.from({ length: 6 }, (_, row) => [...word].map((letter) => GLYPHS[letter]?.[row] ?? "").join(" "))
}

function halfBlocks(grid: string[]): string[] {
  const width = grid[0]?.length ?? 0
  const rows: string[] = []
  for (let row = 0; row < grid.length; row += 2) {
    let line = ""
    for (let col = 0; col < width; col++) {
      const top = grid[row]?.[col] === "#"
      const bottom = grid[row + 1]?.[col] === "#"
      line += top && bottom ? "█" : top ? "▀" : bottom ? "▄" : " "
    }
    rows.push(line)
  }
  return rows
}

function wide(grid: string[]): string[] {
  return grid.map((line) => [...line].map((pixel) => (pixel === "#" ? "██" : "  ")).join(""))
}

export function logoRows(size: LogoSize = "compact"): LogoRow[] {
  const paint = size === "large" ? wide : halfBlocks
  // The gap between "i" and "s" stays with the shade run.
  const shade = paint(pixels("Kvi").map((line) => `${line} `))
  const main = paint(pixels("st"))
  return shade.map((line, i) => ({ shade: line, main: main[i] ?? "" }))
}
