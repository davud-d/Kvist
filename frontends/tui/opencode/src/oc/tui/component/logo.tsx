import { RGBA, TextAttributes } from "@opentui/core"
import { For, type JSX } from "solid-js"
import { useTerminalDimensions } from "@opentui/solid"
import { useTheme } from "../context/theme"
import { tint } from "../theme/color"
import { go } from "../logo"
import { LOGO_COLORS, LOGO_WIDTH, logoRows, type LogoSize } from "../../../../../ink/ui/logo"

// Kvist: the wordmark is the one Kvist prints when it exits (the Ink interface's `logoRows`, pixels two
// cells wide so they come out square), in its own two blues whatever the theme says. Only a window too
// small for it gets OpenCode's tiny mark.
const SHADE = RGBA.fromHex(LOGO_COLORS.shade)
const MAIN = RGBA.fromHex(LOGO_COLORS.main)
const CYAN = RGBA.fromHex("#22c7ff")

export function Logo() {
  const theme = useTheme()
  const dimensions = useTerminalDimensions()

  const renderLine = (line: string, fg: RGBA, bold: boolean): JSX.Element[] => {
    const shadow = tint(theme.background.base, fg, 0.25)
    const attrs = bold ? TextAttributes.BOLD : undefined
    return Array.from(line).map((char) => {
      if (char === "_") {
        return (
          <text fg={fg} bg={shadow} attributes={attrs} selectable={false}>
            {" "}
          </text>
        )
      }
      if (char === "^") {
        return (
          <text fg={fg} bg={shadow} attributes={attrs} selectable={false}>
            ▀
          </text>
        )
      }
      if (char === "~") {
        return (
          <text fg={shadow} attributes={attrs} selectable={false}>
            ▀
          </text>
        )
      }
      if (char === ",") {
        return (
          <text fg={shadow} attributes={attrs} selectable={false}>
            ▄
          </text>
        )
      }
      return (
        <text fg={fg} attributes={attrs} selectable={false}>
          {char}
        </text>
      )
    })
  }

  const size = (): LogoSize | undefined => {
    const { width, height } = dimensions()
    if (height < 12) return undefined
    if (width >= LOGO_WIDTH.large + 4 && height >= 24) return "large"
    return width >= LOGO_WIDTH.compact + 4 ? "compact" : undefined
  }

  return (
    <box>
      {dimensions().height < 12 ? null : size() ? (
        <For each={logoRows(size())}>
          {(row) => (
            <text selectable={false}>
              <span style={{ fg: SHADE }}>{row.shade}</span>
              <span style={{ fg: MAIN }}>{row.main}</span>
            </text>
          )}
        </For>
      ) : (
        <For each={go.right.slice(1)}>
          {(line) => <box flexDirection="row">{renderLine(line, CYAN, true)}</box>}
        </For>
      )}
    </box>
  )
}
