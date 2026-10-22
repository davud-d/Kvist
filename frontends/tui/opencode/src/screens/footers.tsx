// The rows under the prompt and at the bottom of the home screen (OpenCode's `prompt.footer` and
// `home.footer` plugins, which are built in there).

import { TextAttributes } from "@opentui/core"
import { useTerminalDimensions } from "@opentui/solid"
import { createMemo, Show } from "solid-js"

import { contextLabel } from "../../../ink/format"
import { Keymap } from "../oc/tui/context/keymap"
import { useTheme } from "../oc/tui/context/theme"
import { createColors, createFrames } from "../oc/tui/ui/spinner"
import { stringWidth } from "../oc/tui/util/string-width"
import { useKvist } from "../kvist"
import { abbreviateHome } from "../util"
import { PromptRunning } from "./prompt"
import { roleColor } from "./roles"

// What fits beside the location: usage and shortcuts, then usage alone, then nothing.
export function promptFooterLayout(input: { width: number; usage: string[]; shortcuts: string[] }) {
  const usage = input.usage.join(" · ")
  const shortcuts = input.shortcuts.join(" · ")
  const available = Math.max(0, input.width - Math.min(28, Math.floor(input.width / 2)))
  if (usage && shortcuts && stringWidth(`${usage} · ${shortcuts}`) <= available) return { usage: true, shortcuts: true }
  if (usage && stringWidth(usage) <= available) return { usage: true, shortcuts: false }
  if (!usage && shortcuts && stringWidth(shortcuts) <= available) return { usage: false, shortcuts: true }
  return { usage: false, shortcuts: false }
}

export function PromptFooter(props: { armed: boolean; stopArmedColor?: boolean }) {
  const kvist = useKvist()
  const theme = useTheme()
  const dimensions = useTerminalDimensions()
  const shortcuts = Keymap.useShortcuts()
  const state = kvist.state
  const role = () => (state().running ? (state().info.routedRole ?? state().info.role) : state().info.role) || "auto"
  const spinner = createMemo(() => {
    const color = roleColor(theme, role())
    return {
      frames: createFrames({ color, style: "blocks", inactiveFactor: 0.6, minAlpha: 0.3 }),
      colors: createColors({ color, style: "blocks", inactiveFactor: 0.6, minAlpha: 0.3 }),
    }
  })
  const usage = () => contextLabel(state().usage.context, state().usage.limit)
  const hints = () => [
    ["agent.cycle", "roles"],
    ["command.palette.show", "commands"],
  ] as const
  const layout = createMemo(() => {
    const first = shortcuts.get("command.palette.show")
    return promptFooterLayout({
      width: Math.max(0, dimensions().width - 8),
      usage: usage() ? [usage()] : [],
      shortcuts: first ? [`${first} commands`] : [],
    })
  })

  return (
    <>
      <box flexGrow={1} flexShrink={1} minWidth={0}>
        <Show
          when={state().running}
          fallback={
            <text fg={theme.text.muted} wrapMode="none" truncate flexGrow={1} flexShrink={1}>
              {state().command ? `running /${state().command}…` : state().taskState === "paused" ? "paused · /resume continues" : abbreviateHome(kvist.workspace)}
            </text>
          }
        >
          <PromptRunning armed={props.armed} color={roleColor(theme, role())} frames={spinner().frames} colors={spinner().colors} />
        </Show>
      </box>
      <Show when={state().running && state().status?.text}>
        <text fg={theme.text.muted} wrapMode="none" truncate flexShrink={1}>{state().status?.text}</text>
      </Show>
      <Show when={layout().usage && usage()}>
        <text fg={theme.text.muted} wrapMode="none" flexShrink={0}>{usage()}</text>
      </Show>
      <box flexDirection="row" gap={2} flexShrink={0}>
        <Show when={dimensions().width >= 44}>
          {hints().map(([id, label]) => (
            <Show when={shortcuts.get(id)}>
              <text fg={theme.text.base} attributes={TextAttributes.NONE} wrapMode="none" flexShrink={0}>
                {shortcuts.get(id)} <span style={{ fg: theme.text.muted }}>{label}</span>
              </text>
            </Show>
          ))}
        </Show>
      </box>
    </>
  )
}

export function HomeFooter() {
  const kvist = useKvist()
  const theme = useTheme()
  const dimensions = useTerminalDimensions()
  return (
    <Show when={dimensions().height >= 14 && dimensions().width >= 44}>
      <box
        width="100%"
        paddingTop={dimensions().height < 16 ? 0 : 1}
        paddingBottom={dimensions().height < 16 ? 0 : 1}
        paddingLeft={2}
        paddingRight={2}
        flexDirection="row"
        flexShrink={0}
        gap={2}
      >
        <box flexGrow={1} />
        <Show when={dimensions().width >= 64 && kvist.state().info.kvistVersion}>
          <box flexShrink={0}>
            <text fg={theme.text.muted}>{kvist.state().info.kvistVersion}</text>
          </box>
        </Show>
      </box>
    </Show>
  )
}
