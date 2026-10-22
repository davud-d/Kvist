// OpenCode colours its agents with the theme's categorical hues, by position in the agent list;
// Kvist's roles take the same places.

import type { RGBA } from "@opentui/core"

import type { ComponentTheme } from "../oc/tui/theme/component"

export const ROLE_ORDER = ["auto", "coder", "reader", "devops", "chat", "universal"] as const

export function roleColor(theme: ComponentTheme, role: string): RGBA {
  const index = Math.max(0, ROLE_ORDER.indexOf(role as (typeof ROLE_ORDER)[number]))
  const scales = theme.categorical
  return scales[index % scales.length]![200]
}

export function titlecase(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1)
}
