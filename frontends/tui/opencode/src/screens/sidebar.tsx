// OpenCode's session sidebar (routes/session/sidebar.tsx and the built-in sidebar plugins):
// the title, the context, and at the bottom where the session runs. What OpenCode shows as
// todos and MCP servers is Kvist's background jobs and the files the agent changed.

import { RGBA, TextAttributes } from "@opentui/core"
import { useTerminalDimensions } from "@opentui/solid"
import { createMemo, createSignal, For, Show, type JSX } from "solid-js"

import { type Item } from "../../../ink/state"
import { useConfig } from "../oc/tui/config"
import { useTheme } from "../oc/tui/context/theme"
import { FilePath } from "../oc/tui/ui/file-path"
import { SESSION_SIDEBAR_WIDTH } from "../oc/tui/ui/layout"
import { getScrollAcceleration } from "../oc/tui/util/scroll"
import { useKvist } from "../kvist"
import { abbreviateHome } from "../util"
import { blockOf, str } from "../tools"

// The sidebar docks beside the transcript on a wide terminal; on a narrower one it opens over it.
export const SIDEBAR_WIDE_COLUMNS = 120

const [overlay, setOverlay] = createSignal(false)
export const sidebarOverlay = overlay

export function toggleSidebar(config: ReturnType<typeof useConfig>, columns: number) {
  const wide = columns > SIDEBAR_WIDE_COLUMNS
  const shown = wide ? (config.data.session?.sidebar ?? "auto") === "auto" : overlay()
  if (wide) {
    void config.update((draft) => {
      draft.session = { ...draft.session, sidebar: shown ? "hide" : "auto" }
    })
    return
  }
  setOverlay(!shown)
}

export function sidebarDocked(config: ReturnType<typeof useConfig>, columns: number) {
  return columns > SIDEBAR_WIDE_COLUMNS && (config.data.session?.sidebar ?? "auto") === "auto"
}

// Files the agent wrote or edited in the session on screen, in order of first change.
export function changedFiles(items: readonly Item[]): string[] {
  const seen = new Set<string>()
  for (const item of items) {
    if (item.kind !== "tool" || !item.result.ok || blockOf(item.action) === "shell" || blockOf(item.action) === null) continue
    const path = item.result.path ?? str(item.args.path)
    if (path) seen.add(path)
  }
  return [...seen]
}

function Section(props: { title: string; children: JSX.Element }) {
  const theme = useTheme()
  return (
    <box>
      <text fg={theme.text.base}>
        <b>{props.title}</b>
      </text>
      {props.children}
    </box>
  )
}

export function Sidebar() {
  const kvist = useKvist()
  const theme = useTheme()
  const config = useConfig().data
  const state = kvist.state
  const files = createMemo(() => changedFiles(state().items))
  const running = () => state().jobs.filter((job) => job.status === "running")
  const title = () => state().info.title || state().info.session || "New session"
  const scrollAcceleration = createMemo(() => getScrollAcceleration(config))
  const usage = () => state().usage
  const percent = () => (usage().limit > 0 ? Math.round((usage().context / usage().limit) * 100) : undefined)

  return (
    <box
      backgroundColor={theme.background.raised.base}
      width={SESSION_SIDEBAR_WIDTH}
      height="100%"
      paddingTop={1}
      paddingBottom={1}
      paddingLeft={2}
      paddingRight={2}
      position="relative"
    >
      <box flexShrink={0} paddingRight={2} paddingBottom={1}>
        <text fg={theme.text.base} attributes={TextAttributes.BOLD} wrapMode="word">{title()}</text>
      </box>
      <scrollbox
        flexGrow={1}
        minHeight={0}
        scrollAcceleration={scrollAcceleration()}
        horizontalScrollbarOptions={{ visible: false }}
        verticalScrollbarOptions={{
          position: "absolute",
          right: 0,
          top: 0,
          width: 1,
          height: "100%",
          trackOptions: { backgroundColor: theme.background.raised.base, foregroundColor: theme.scrollbar.base },
        }}
      >
        <box flexShrink={0} gap={1} paddingRight={1}>
          <Show when={usage().context > 0}>
            <Section title="Context">
              <text fg={theme.text.muted}>{usage().context.toLocaleString()} tokens</text>
              <Show when={percent() !== undefined}>
                <text fg={theme.text.muted}>{percent()}% used</text>
              </Show>
            </Section>
          </Show>
          <Show when={running().length}>
            <Section title="Background jobs">
              <For each={running()}>
                {(job) => (
                  <text fg={theme.text.muted} wrapMode="none" truncate>
                    <span style={{ fg: theme.text.feedback.success.base }}>• </span>
                    {job.purpose || job.command.join(" ")}
                  </text>
                )}
              </For>
            </Section>
          </Show>
          <Show when={files().length}>
            <Section title="Modified files">
              <For each={files()}>
                {(file) => <text fg={theme.text.muted} wrapMode="none" truncate>{file}</text>}
              </For>
            </Section>
          </Show>
        </box>
      </scrollbox>
      <box flexShrink={0} gap={1} paddingTop={1}>
        <FilePath value={abbreviateHome(kvist.workspace)} maxWidth={38} fg={theme.text.muted} />
      </box>
    </box>
  )
}

export function SidebarOverlay() {
  const dimensions = useTerminalDimensions()
  return (
    <Show when={sidebarOverlay() && dimensions().width <= SIDEBAR_WIDE_COLUMNS}>
      <box position="absolute" top={0} left={0} right={0} bottom={0} alignItems="flex-end" backgroundColor={RGBA.fromInts(0, 0, 0, 70)}>
        <Sidebar />
      </box>
    </Show>
  )
}
