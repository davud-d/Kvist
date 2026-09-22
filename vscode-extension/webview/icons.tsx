interface Glyph {
  d: string
  filled?: boolean
}

const glyphs: Record<string, Glyph> = {
  settings: { d: "M2.5 5h6M11.5 5h2M2.5 11h2M7.5 11h6M10 3v4M6 9v4" },
  add: { d: "M8 3.5v9M3.5 8h9" },
  back: { d: "M10 3 5 8l5 5" },
  check: { d: "M3.5 8.5l3 3 6-7" },
  cross: { d: "M4.5 4.5l7 7M11.5 4.5l-7 7" },
  chevron: { d: "M4.5 6.5l3.5 3.5 3.5-3.5" },
  arrowUp: { d: "M8 13V3.5M4 7l4-3.5L12 7" },
  stop: { d: "M4.5 4.5h7v7h-7z", filled: true },
  copy: { d: "M6 6h6.5v6.5H6zM3.5 9.5v-6H10" },
}

export type IconName = keyof typeof glyphs

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  const glyph = glyphs[name]
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill={glyph.filled ? "currentColor" : "none"} stroke={glyph.filled ? "none" : "currentColor"} stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d={glyph.d} />
    </svg>
  )
}

// How full the context window is; the exact numbers live in the tooltip.
export function Ring({ value }: { value: number }) {
  const circumference = 2 * Math.PI * 6
  const filled = Math.min(1, Math.max(0, value)) * circumference
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke-width="2" aria-hidden="true">
      <circle cx="8" cy="8" r="6" stroke="currentColor" opacity="0.22" />
      <circle cx="8" cy="8" r="6" stroke="var(--kv)" stroke-linecap="round" stroke-dasharray={`${filled} ${circumference}`} transform="rotate(-90 8 8)" />
    </svg>
  )
}
