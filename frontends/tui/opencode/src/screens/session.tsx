// OpenCode's session screen (routes/session/index.tsx): the transcript in a scrollbox that sticks to
// its bottom, the prompt (or a question) under it, and the sidebar beside it.

import { type ScrollBoxRenderable } from "@opentui/core"
import { useTerminalDimensions } from "@opentui/solid"
import { createMemo, For, Match, Show, Switch, type JSX } from "solid-js"

import type { Item } from "../../../ink/state"
import { useConfig } from "../oc/tui/config"
import type { KeymapCommand } from "../oc/tui/context/keymap"
import { Spinner } from "../oc/tui/component/spinner"
import { useTheme } from "../oc/tui/context/theme"
import { context as renderContext } from "../oc/tui/routes/session/render-context"
import { SESSION_SIDEBAR_WIDTH } from "../oc/tui/ui/layout"
import { getScrollAcceleration } from "../oc/tui/util/scroll"
import { useKvist } from "../kvist"
import { isAnswerAction } from "../tools"
import { PromptFooter } from "./footers"
import { Prompt, type PromptRef } from "./prompt"
import { Sidebar, SidebarOverlay, sidebarDocked } from "./sidebar"
import {
  Answer,
  AssistantFooter,
  ExploredGroup,
  JobBlock,
  Notice,
  PlanBlock,
  Thought,
  ToolPart,
  UserMessage,
  partOfCall,
  partOfTool,
} from "./transcript"

export function isEmptyItem(item: Item): boolean {
  switch (item.kind) {
    case "thought":
      return !item.text.trim() && item.seconds < 1
    case "say":
    case "answer":
    case "note":
      return !item.text.trim()
    default:
      return false
  }
}

// What the transcript shows: the welcome banner, the call lines the answer makes redundant, debug
// notes (unless asked for), the empty rows and the notices (a command's result, a warning) are left
// out: those are toasts. Only an error stays on the page.
export function visibleItems(items: readonly Item[], showDebug = false): Item[] {
  return items.filter((item) => {
    if (item.kind === "banner" || isEmptyItem(item)) return false
    if (item.kind === "note" && item.tone === "debug") return showDebug
    if (item.kind === "note" && item.tone !== "error") return false
    return !((item.kind === "step" || item.kind === "tool") && isAnswerAction(item.action))
  })
}

function ItemRow(props: { item: Item }) {
  const item = () => props.item
  return (
    <Switch>
      <Match when={item().kind === "user"}>
        <UserMessage text={(item() as Extract<Item, { kind: "user" }>).text} />
      </Match>
      <Match when={item().kind === "say" || item().kind === "answer"}>
        <Answer text={(item() as Extract<Item, { kind: "say" }>).text} />
      </Match>
      <Match when={item().kind === "thought"}>
        <Thought text={(item() as Extract<Item, { kind: "thought" }>).text} seconds={(item() as Extract<Item, { kind: "thought" }>).seconds} />
      </Match>
      <Match when={item().kind === "tool"}>
        <ToolPart part={partOfTool(item() as Extract<Item, { kind: "tool" }>)} />
      </Match>
      <Match when={item().kind === "step"}>
        <ToolPart part={partOfCall({ action: (item() as Extract<Item, { kind: "step" }>).action, args: {}, step: item().id })} />
      </Match>
      <Match when={item().kind === "explored"}>
        <ExploredGroup names={(item() as Extract<Item, { kind: "explored" }>).names} counts={(item() as Extract<Item, { kind: "explored" }>).counts as unknown as Record<string, number>} completed={true} />
      </Match>
      <Match when={item().kind === "note"}>
        <Notice tone={(item() as Extract<Item, { kind: "note" }>).tone} text={(item() as Extract<Item, { kind: "note" }>).text} />
      </Match>
      <Match when={item().kind === "plan"}>
        <PlanBlock markdown={(item() as Extract<Item, { kind: "plan" }>).markdown} />
      </Match>
      <Match when={item().kind === "done"}>
        <AssistantFooter
          role={(item() as Extract<Item, { kind: "done" }>).role}
          seconds={(item() as Extract<Item, { kind: "done" }>).seconds}
          interrupted={(item() as Extract<Item, { kind: "done" }>).interrupted}
        />
      </Match>
      <Match when={item().kind === "job"}>
        {(() => {
          const job = item() as Extract<Item, { kind: "job" }>
          return <JobBlock jobId={job.jobId} command={job.command} output={job.output} status={job.status} exitCode={job.exitCode} seconds={job.seconds} />
        })()}
      </Match>
      <Match when={item().kind === "job_notice"}>
        <Notice tone="info" text={(item() as Extract<Item, { kind: "job_notice" }>).text} />
      </Match>
    </Switch>
  )
}

// What is still arriving: reads in flight, calls without a result, the thought and the answer
// being written.
function LiveRows() {
  const kvist = useKvist()
  const state = kvist.state
  const counts = createMemo(() => (state().exploring ?? {}) as unknown as Record<string, number>)
  const calls = createMemo(() => state().inflight.filter((call) => !isAnswerAction(call.action)))
  // Between the model's words, for any role: what the core says it is doing (an action role's long write
  // shows as "Writing write_file → path · N characters"), until a thought or an answer streams in.
  const waiting = () => state().running && !state().pending && !state().live?.thinking && !state().live?.answer.trim()
  const statusLine = () => {
    const status = state().status
    return [status?.text || "Working", status?.planStep, status?.step ? `step ${status.step}` : ""].filter(Boolean).join(" · ")
  }
  return (
    <>
      <Show when={state().exploringNames.length}>
        <box marginTop={1} flexShrink={0}>
          <ExploredGroup names={state().exploringNames} counts={counts()} completed={false} />
        </box>
      </Show>
      <For each={calls()}>
        {(call) => (
          <box marginTop={1} flexShrink={0}>
            <ToolPart part={partOfCall(call)} />
          </box>
        )}
      </For>
      <Show when={state().live?.thinking}>
        <box marginTop={1} flexShrink={0}>
          <Thought text={state().live?.reasoning ?? ""} seconds={0} live />
        </box>
      </Show>
      <Show when={state().live?.answer.trim()}>
        <box marginTop={1} flexShrink={0}>
          <Answer text={state().live?.answer ?? ""} live />
        </box>
      </Show>
      <Show when={waiting()}>
        <box marginTop={1} flexShrink={0}>
          <Spinner>{statusLine()}</Spinner>
        </box>
      </Show>
    </>
  )
}

export function Session(props: {
  ref?: (ref: PromptRef | undefined) => void
  disabled?: boolean
  commands: readonly KeymapCommand[]
  // Replaces the prompt while an approval waits.
  approval?: JSX.Element
  stopArmed: boolean
  scrollRef?: (scroll: ScrollBoxRenderable) => void
}) {
  const theme = useTheme()
  const kvist = useKvist()
  const config = useConfig()
  const dimensions = useTerminalDimensions()
  const state = kvist.state
  // One list per update: every row looks at the one before it.
  const items = createMemo(() => visibleItems(state().items, state().showDebug))
  const docked = () => sidebarDocked(config, dimensions().width)
  const scrollAcceleration = createMemo(() => getScrollAcceleration(config.data))
  const contentWidth = () => dimensions().width - (docked() ? SESSION_SIDEBAR_WIDTH : 0) - (dimensions().width < 44 ? 2 : 4)

  return (
    <renderContext.Provider
      value={{
        get width() {
          return contentWidth()
        },
        terminal: {
          get width() {
            return dimensions().width
          },
          get height() {
            return dimensions().height
          },
        },
        sessionID: state().info.session,
        thinkingMode: () => config.data.session?.thinking ?? "show",
        markdownMode: () => "rendered",
        groupExploration: () => true,
        diffWrapMode: () => config.data.diffs?.wrap ?? "word",
        models: () => [],
        config: config.data,
      }}
    >
      <box flexDirection="row" flexGrow={1} minHeight={0}>
        <box
          flexGrow={1}
          minHeight={0}
          paddingBottom={1}
          paddingLeft={dimensions().width < 44 ? 1 : 2}
          paddingRight={dimensions().width < 44 ? 1 : 2}
        >
          <box flexGrow={1} minHeight={0} position="relative">
            <scrollbox
              ref={(scroll: ScrollBoxRenderable) => props.scrollRef?.(scroll)}
              stickyScroll={true}
              stickyStart="bottom"
              flexGrow={1}
              scrollAcceleration={scrollAcceleration()}
              verticalScrollbarOptions={{
                paddingLeft: 1,
                visible: config.data.session?.scrollbar === true,
                trackOptions: { backgroundColor: theme.decrease(theme.background.raised.base), foregroundColor: theme.border.base },
              }}
            >
              <For each={items()}>
                {(item) => (
                  <box marginTop={1} flexShrink={0}>
                    <ItemRow item={item} />
                  </box>
                )}
              </For>
              <LiveRows />
            </scrollbox>
          </box>
          <box height={1} flexShrink={0} flexDirection="row" justifyContent="flex-end" />
          <box flexShrink={0}>
            <Show
              when={props.approval}
              fallback={<Prompt ref={props.ref} commands={props.commands} disabled={props.disabled} footer={<PromptFooter armed={props.stopArmed} />} />}
            >
              {props.approval}
            </Show>
          </box>
        </box>
        <Show when={docked()}>
          <Sidebar />
        </Show>
      </box>
      <SidebarOverlay />
    </renderContext.Provider>
  )
}

