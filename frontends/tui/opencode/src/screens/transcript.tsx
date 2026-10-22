// The rows of OpenCode's transcript (packages/tui/src/routes/session/index.tsx: UserMessage,
// InlineTool, BlockTool, Shell, Write, Edit …), taking what the Kvist core reports where they take
// a message part of OpenCode's server.

import { RGBA, TextAttributes } from "@opentui/core"
import { useRenderer } from "@opentui/solid"
import stripAnsi from "strip-ansi"
import { createMemo, createSignal, For, Match, Show, Switch, type Accessor, type JSX } from "solid-js"

import { formatSeconds } from "../../../ink/format"
import { subjectOf } from "../../../ink/approval"
import type { Item } from "../../../ink/state"
import type { SessionMessageAssistant, SessionMessageAssistantReasoning, SessionMessageAssistantText, SessionMessageAssistantTool } from "@opencode/client"
import { PatchDiff } from "../oc/tui/component/patch-diff"
import { Spinner } from "../oc/tui/component/spinner"
import { useConfig } from "../oc/tui/config"
import { useTheme, useThemes } from "../oc/tui/context/theme"
import { InlineToolRow, ReasoningPart, TextPart } from "../oc/tui/routes/session/message-parts"
import { use } from "../oc/tui/routes/session/render-context"
import { SplitBorder } from "../oc/tui/ui/border"
import { collapseShellOutput } from "../oc/tui/util/collapse-tool-output"
import { filetype } from "../oc/tui/util/filetype"
import { Locale } from "../oc/tui/util/locale"
import { stringWidth } from "../oc/tui/util/string-width"
import { useKvist } from "../kvist"
import { commandLine, editPatch, inlineFromLabel, inlineOf, str } from "../tools"
import { roleColor, titlecase } from "./roles"

export const INLINE_TOOL_ICON_WIDTH = 2

type ToolItem = Extract<Item, { kind: "tool" }>

// The shape the copied rows read a tool call in.
export type ToolProps = {
  input: Record<string, unknown>
  metadata: Record<string, unknown>
  output?: string
  tool: string
  part: SessionMessageAssistantTool
}

let counter = 0

// A finished call of the Kvist core as OpenCode's tool part.
export function partOfTool(item: ToolItem): SessionMessageAssistantTool {
  const result = item.result
  const base = { type: "tool" as const, id: `call-${item.id}`, name: item.action, time: { created: 0, completed: 1 } }
  const metadata = { path: result.path, first_line: result.first_line, exit_code: result.exit_code }
  return result.ok
    ? { ...base, state: { status: "completed", input: item.args, metadata, content: [{ type: "text", text: result.output }] } }
    : { ...base, state: { status: "error", input: item.args, metadata, error: { message: result.summary }, content: [{ type: "text", text: result.output }] } }
}

// A call that has no result yet.
export function partOfCall(call: { action: string; args: Record<string, unknown>; step: number }): SessionMessageAssistantTool {
  counter += 1
  return { type: "tool", id: `running-${call.step}-${counter}`, name: call.action, time: { created: 0 }, state: { status: "running", input: call.args, metadata: {} } }
}

function useToolPermission(part: () => SessionMessageAssistantTool | undefined) {
  const kvist = useKvist()
  return createMemo(() => {
    const request = kvist.state().pending
    return Boolean(request && subjectOf(request) && part()?.state.status === "running")
  })
}

export function UserMessage(props: { text: string }) {
  const theme = useTheme()
  const kvist = useKvist()
  const [hover, setHover] = createSignal(false)
  const color = createMemo(() => roleColor(theme, kvist.state().info.routedRole ?? kvist.state().info.role ?? "auto"))
  return (
    <Show when={props.text.trim()}>
      <box border={["left"]} borderColor={color()} customBorderChars={SplitBorder.customBorderChars} backgroundColor={theme.background.raised.base}>
        <box
          onMouseOver={() => setHover(true)}
          onMouseOut={() => setHover(false)}
          paddingTop={1}
          paddingBottom={1}
          paddingLeft={2}
          backgroundColor={hover() ? theme.decrease(theme.background.raised.base) : theme.background.raised.base}
          flexShrink={0}
        >
          <text fg={theme.text.base}>{props.text}</text>
        </box>
      </box>
    </Show>
  )
}

function StatusBadge(props: { children: string; raised?: boolean }) {
  const theme = useTheme()
  const background = () => (props.raised ? theme.background.raised.base : theme.background.base)
  return (
    <text flexShrink={0} bg={theme.decrease(background())} fg={theme.text.muted}>
      {" "}
      {props.children}{" "}
    </text>
  )
}

function InlineTool(props: {
  icon: string
  color?: RGBA
  complete: unknown
  pending: string
  spinner?: boolean
  running?: boolean
  status?: JSX.Element
  children: JSX.Element
  part: SessionMessageAssistantTool
  onClick?: () => void
}) {
  const theme = useTheme()
  const renderer = useRenderer()
  const [hover, setHover] = createSignal(false)
  const [errorExpanded, setErrorExpanded] = createSignal(false)
  const permission = useToolPermission(() => props.part)

  const error = createMemo(() => (!props.running && props.part.state.status === "error" ? props.part.state.error.message : undefined))
  const denied = createMemo(() => error()?.includes("denied") || error()?.includes("rejected") || error()?.includes("user dismissed"))
  const failed = createMemo(() => Boolean(error() && !denied()))
  const clickable = createMemo(() => Boolean(props.onClick || failed()))
  const fg = createMemo(() => {
    if (props.color) return props.color
    if (permission()) return theme.text.feedback.warning.base
    if (failed()) return theme.text.feedback.error.base
    if (hover() && props.onClick) return theme.text.base
    return theme.text.muted
  })

  return (
    <InlineToolRow
      icon={props.icon}
      color={fg()}
      errorColor={theme.text.feedback.error.base}
      failed={failed()}
      denied={Boolean(denied())}
      error={error()}
      errorExpanded={errorExpanded()}
      complete={props.complete}
      pending={props.pending}
      spinner={props.spinner}
      status={props.status}
      onMouseOver={() => clickable() && setHover(true)}
      onMouseOut={() => setHover(false)}
      onMouseUp={() => {
        if (renderer.getSelection()?.getSelectedText()) return
        if (failed()) {
          setErrorExpanded((value) => !value)
          return
        }
        props.onClick?.()
      }}
    >
      {props.children}
    </InlineToolRow>
  )
}

function BlockTool(props: {
  title?: string
  path?: { label: string; value: string }
  headerColor?: RGBA
  children?: JSX.Element
  onClick?: () => void
  part?: SessionMessageAssistantTool
  spinner?: boolean
  error?: string
}) {
  const theme = useTheme()
  const background = () => theme.background.raised.base
  const renderer = useRenderer()
  const [hover, setHover] = createSignal(false)
  const error = createMemo(() => props.error ?? (props.part?.state.status === "error" ? props.part.state.error.message : undefined))
  const permission = useToolPermission(() => props.part)
  const header = () => (permission() ? theme.text.feedback.warning.base : (props.headerColor ?? theme.text.muted))
  return (
    <box
      border={["left"]}
      flexShrink={0}
      paddingTop={1}
      paddingBottom={1}
      paddingLeft={2}
      gap={1}
      backgroundColor={hover() ? theme.decrease(background()) : background()}
      customBorderChars={SplitBorder.customBorderChars}
      borderColor={theme.background.base}
      onMouseOver={() => props.onClick && setHover(true)}
      onMouseOut={() => setHover(false)}
      onMouseUp={() => {
        if (renderer.getSelection()?.getSelectedText()) return
        props.onClick?.()
      }}
    >
      <Show
        when={props.path}
        fallback={
          <Show when={props.title}>
            {(title: Accessor<string>) => (
              <Show when={props.spinner} fallback={<text fg={header()}>{title()}</text>}>
                <Spinner color={header()}>{title().replace(/^# /, "")}</Spinner>
              </Show>
            )}
          </Show>
        }
      >
        {(path: Accessor<{ label: string; value: string }>) => (
          <box flexDirection="row" gap={1} minWidth={0}>
            <Show when={props.spinner} fallback={<text flexShrink={0} fg={header()}>{path().label}</text>}>
              <Spinner color={header()}>{path().label.replace(/^# /, "")}</Spinner>
            </Show>
            <text fg={header()} wrapMode="none" truncate>{path().value}</text>
          </box>
        )}
      </Show>
      {props.children}
      <Show when={error()}>
        <text fg={theme.text.feedback.error.base}>{error()}</text>
      </Show>
    </box>
  )
}

function Shell(props: ToolProps) {
  const theme = useTheme()
  const ctx = use()
  const permission = useToolPermission(() => props.part)
  const color = createMemo(() => (permission() ? theme.text.feedback.warning.base : theme.text.base))
  const isRunning = createMemo(() => props.part.state.status === "running")
  const [expanded, setExpanded] = createSignal(false)
  const output = createMemo(() => stripAnsi(props.output?.trim() ?? ""))
  const maxLines = 10
  const maxChars = createMemo(() => maxLines * Math.max(20, ctx.width - 6 - (isRunning() ? 2 : 0)))
  const command = createMemo(() => commandLine(props.input))
  const input = createMemo(() => (command() ? `${isRunning() ? "" : "$ "}${command()}` : ""))
  const collapsed = createMemo(() => collapseShellOutput(input(), output(), maxLines, maxChars()))
  const limitedInput = createMemo(() => (expanded() ? input() : collapsed().input))
  const limitedOutput = createMemo(() => (expanded() ? output() : collapsed().output))
  const code = () => (typeof props.metadata.exit_code === "number" ? props.metadata.exit_code : null)

  return (
    <BlockTool part={props.part} onClick={collapsed().overflow ? () => setExpanded(!expanded()) : undefined}>
      <box gap={1}>
        <Show
          when={isRunning()}
          fallback={
            <text fg={theme.text.base} wrapMode={expanded() ? "word" : "char"} maxHeight={expanded() ? undefined : 2}>
              {limitedInput()}
            </text>
          }
        >
          <box flexDirection="row" gap={1}>
            <Spinner color={color()} />
            <text fg={color()} wrapMode={expanded() ? "word" : "char"} maxHeight={expanded() ? undefined : 2} flexGrow={1} minWidth={0}>
              {limitedInput()}
            </text>
          </box>
        </Show>
        <Show when={limitedOutput()}>
          <text fg={theme.text.muted}>{limitedOutput()}</text>
        </Show>
        <Show when={props.part.state.status === "completed" && code() !== null && code() !== 0}>
          <text fg={theme.text.feedback.error.base}>{`Command exited with code ${code()}`}</text>
        </Show>
      </box>
    </BlockTool>
  )
}

function Write(props: ToolProps & { label: string }) {
  const theme = useTheme()
  const syntax = useThemes().currentSyntax
  const path = createMemo(() => str(props.metadata.path) || str(props.input.path))
  const code = createMemo(() => str(props.input.content))
  const [expanded, setExpanded] = createSignal(false)
  const lines = createMemo(() => code().replace(/\n$/, "").split("\n"))
  const shown = createMemo(() => (expanded() ? lines() : lines().slice(0, 20)))
  return (
    <Switch>
      <Match when={props.part.state.status === "completed"}>
        <BlockTool
          path={{ label: `# ${props.label}`, value: path() }}
          part={props.part}
          onClick={lines().length > 20 ? () => setExpanded(!expanded()) : undefined}
        >
          <line_number fg={theme.text.muted} minWidth={3} paddingRight={1}>
            <code conceal={false} fg={theme.text.base} filetype={filetype(path())} syntaxStyle={syntax()} content={shown().join("\n")} />
          </line_number>
          <Show when={lines().length > shown().length}>
            <text fg={theme.text.muted}>{`… ${lines().length - shown().length} more lines (click to expand)`}</text>
          </Show>
        </BlockTool>
      </Match>
      <Match when={true}>
        <InlineTool icon="←" pending="Preparing write…" complete={path()} part={props.part}>
          {`${props.label === "Wrote" ? "Write" : "Append"} ${path()}`}
        </InlineTool>
      </Match>
    </Switch>
  )
}

function Edit(props: ToolProps) {
  const ctx = use()
  const theme = useTheme()
  const syntax = useThemes().currentSyntax
  const view = createMemo(() => {
    const diffView = ctx.config.diffs?.view
    if (diffView === "unified") return "unified"
    if (diffView === "split") return "split"
    return ctx.width > 120 ? "split" : "unified"
  })
  const path = createMemo(() => str(props.metadata.path) || str(props.input.path))
  const patch = createMemo(() =>
    props.part.state.status === "completed" ? editPatch(path(), props.input, typeof props.metadata.first_line === "number" ? props.metadata.first_line : null) : "",
  )
  return (
    <Switch>
      <Match when={patch()}>
        <BlockTool path={{ label: "← Edit", value: path() }} part={props.part}>
          <box paddingLeft={1}>
            <PatchDiff
              diff={patch()}
              hunkFg={theme.diff.text.hunkHeader}
              view={view()}
              filetype={filetype(path())}
              syntaxStyle={syntax()}
              showLineNumbers={true}
              width="100%"
              wrapMode={ctx.diffWrapMode()}
              fg={theme.text.base}
              addedBg={theme.diff.background.added}
              removedBg={theme.diff.background.removed}
              contextBg={theme.diff.background.context}
              addedSignColor={theme.diff.highlight.added}
              removedSignColor={theme.diff.highlight.removed}
              lineNumberFg={theme.diff.lineNumber.text}
              lineNumberBg={theme.diff.background.context}
              addedLineNumberBg={theme.diff.lineNumber.background.added}
              removedLineNumberBg={theme.diff.lineNumber.background.removed}
            />
          </box>
        </BlockTool>
      </Match>
      <Match when={true}>
        <BlockTool
          path={path() ? { label: "← Edit", value: path() } : undefined}
          title={path() ? undefined : "# Preparing edit…"}
          part={props.part}
          spinner={props.part.state.status === "running"}
        />
      </Match>
    </Switch>
  )
}

// Everything that is one line: reads, searches, listings, the web, the core's own tools.
function Inline(props: ToolProps) {
  const line = createMemo(() => inlineOf(props.tool, props.input))
  const loading = createMemo(() => props.part.state.status === "running")
  return (
    <InlineTool icon={line().icon} pending={line().pending} complete={props.part.state.status !== "running"} spinner={loading()} part={props.part}>
      {line().text}
    </InlineTool>
  )
}

export function ToolPart(props: { part: SessionMessageAssistantTool }) {
  const toolprops: ToolProps = {
    get metadata() {
      return props.part.state.status === "streaming" ? {} : props.part.state.metadata
    },
    get input() {
      return typeof props.part.state.input === "string" ? {} : props.part.state.input
    },
    get output() {
      const state = props.part.state
      if (state.status === "streaming" || state.status === "running") return undefined
      return (state.content ?? []).flatMap((content) => (content.type === "text" ? [content.text] : [])).join("\n")
    },
    get tool() {
      return props.part.name
    },
    get part() {
      return props.part
    },
  }
  return (
    <Switch>
      <Match when={props.part.name === "run_shell"}>
        <Shell {...toolprops} />
      </Match>
      <Match when={props.part.name === "write_file"}>
        <Write {...toolprops} label="Wrote" />
      </Match>
      <Match when={props.part.name === "append_file"}>
        <Write {...toolprops} label="Appended" />
      </Match>
      <Match when={props.part.name === "replace_in_file"}>
        <Edit {...toolprops} />
      </Match>
      <Match when={true}>
        <Inline {...toolprops} />
      </Match>
    </Switch>
  )
}

// The reads and searches of one stretch, folded into one line that opens to the calls, like
// OpenCode's "Explored" group.
export function ExploredGroup(props: { names: readonly string[]; counts: Record<string, number>; completed: boolean }) {
  const theme = useTheme()
  const ctx = use()
  const [expanded, setExpanded] = createSignal(false)
  const [hover, setHover] = createSignal(false)
  const label = createMemo(() => {
    const words: [string, string, string][] = [["reads", "read", "reads"], ["searches", "search", "searches"], ["lists", "listing", "listings"], ["git", "git check", "git checks"]]
    const parts = words.filter(([key]) => (props.counts[key] ?? 0) > 0).map(([key, one, many]) => `${props.counts[key]} ${props.counts[key] === 1 ? one : many}`)
    return `${props.completed ? "Explored" : "Exploring"}: ${parts.join(", ")}`
  })
  const grouped = () => ctx.groupExploration()
  return (
    <Show
      when={grouped()}
      fallback={
        <For each={props.names}>
          {(name) => {
            const line = inlineFromLabel(name)
            return (
              <InlineToolRow icon={line.icon} color={theme.text.muted} complete={true} pending={line.pending}>
                {line.text}
              </InlineToolRow>
            )
          }}
        </For>
      }
    >
      <InlineToolRow
        icon={props.completed ? "→" : "✱"}
        color={hover() ? theme.text.base : theme.text.muted}
        complete={props.completed}
        pending={label()}
        spinner={!props.completed}
        onMouseOver={() => setHover(true)}
        onMouseOut={() => setHover(false)}
        onMouseUp={() => setExpanded(!expanded())}
      >
        {label()}
      </InlineToolRow>
      <Show when={expanded()}>
        <For each={props.names}>
          {(name) => {
            const line = inlineFromLabel(name)
            return (
              <InlineToolRow icon={line.icon} color={theme.text.muted} complete={true} pending={line.pending}>
                {line.text}
              </InlineToolRow>
            )
          }}
        </For>
      </Show>
    </Show>
  )
}

// Reasoning as OpenCode's part, from the reducer's thought item.
export function Thought(props: { text: string; seconds: number; live?: boolean }) {
  const message = createMemo<SessionMessageAssistant>(() => ({
    id: "thought",
    type: "assistant",
    agent: "",
    model: { providerID: "", id: "" },
    time: { created: 0, completed: props.live ? undefined : 1 },
  }))
  const part = createMemo<SessionMessageAssistantReasoning>(() => ({
    type: "reasoning",
    id: "thought",
    text: props.text,
    time: { created: 0, completed: props.live ? undefined : Math.round(props.seconds * 1000) },
  }))
  return <ReasoningPart last={false} part={part()} message={{ ...message(), time: { created: 0, completed: props.live ? undefined : Math.round(props.seconds * 1000) } }} />
}

export function Answer(props: { text: string; live?: boolean }) {
  const message = createMemo<SessionMessageAssistant>(() => ({
    id: "answer",
    type: "assistant",
    agent: "",
    model: { providerID: "", id: "" },
    time: { created: 0, completed: props.live ? undefined : 1 },
  }))
  const part = createMemo<SessionMessageAssistantText>(() => ({ type: "text", id: "answer", text: props.text }))
  return <TextPart last={false} part={part()} message={message()} />
}

export function AssistantFooter(props: { role: string; seconds: number; interrupted: boolean }) {
  const theme = useTheme()
  const ctx = use()
  const kvist = useKvist()
  return (
    <box paddingLeft={3}>
      <text>
        <span style={{ fg: props.interrupted ? theme.text.muted : roleColor(theme, props.role || "auto") }}>{titlecase(props.role || "auto")}</span>
        <Show when={ctx.terminal.width >= 28}>
          <span style={{ fg: theme.text.muted }}> · {kvist.state().info.model}</span>
        </Show>
        <Show when={props.seconds >= 1 && (ctx.terminal.width < 28 || ctx.terminal.width >= 36)}>
          <span style={{ fg: theme.text.muted }}> · {formatSeconds(props.seconds)}</span>
        </Show>
        <Show when={props.interrupted}>
          <span style={{ fg: theme.text.muted }}> · interrupted</span>
        </Show>
      </text>
    </box>
  )
}

export function Notice(props: { tone: "info" | "warn" | "error" | "debug"; text: string }) {
  const theme = useTheme()
  const color = () => (props.tone === "error" ? theme.text.feedback.error.base : props.tone === "warn" ? theme.text.feedback.warning.base : theme.text.muted)
  return (
    <box paddingLeft={3}>
      <text fg={color()} wrapMode="word">{props.text}</text>
    </box>
  )
}

export function PlanBlock(props: { markdown: string }) {
  const theme = useTheme()
  const syntax = useThemes().currentSyntax
  return (
    <BlockTool title="# Plan" headerColor={theme.text.feedback.info.base}>
      <markdown syntaxStyle={syntax()} content={props.markdown.trim()} conceal={true} fg={theme.markdown.text} bg={theme.background.raised.base} />
    </BlockTool>
  )
}

export function JobBlock(props: { jobId: string; command: string[]; output: string; status: string; exitCode: number | null; seconds: number }) {
  const theme = useTheme()
  const failed = () => props.status !== "exited" || (props.exitCode ?? 0) !== 0
  const outcome = () => (props.status === "timed_out" ? "timed out" : props.status === "stopped" ? "stopped" : `exited with code ${props.exitCode ?? 0}`)
  const [expanded, setExpanded] = createSignal(false)
  const output = createMemo(() => stripAnsi(props.output.trim()))
  const collapsed = createMemo(() => collapseShellOutput(`$ ${props.command.join(" ")}`, output(), 10, 2000))
  return (
    <BlockTool title={`# Background job ${props.jobId}`} onClick={collapsed().overflow ? () => setExpanded(!expanded()) : undefined}>
      <box gap={1}>
        <text fg={theme.text.base} wrapMode="char">{expanded() ? `$ ${props.command.join(" ")}` : collapsed().input}</text>
        <Show when={output()}>
          <text fg={theme.text.muted}>{expanded() ? output() : collapsed().output}</text>
        </Show>
        <text fg={failed() ? theme.text.feedback.error.base : theme.text.muted}>{`${outcome()} after ${formatSeconds(props.seconds)}`}</text>
      </box>
    </BlockTool>
  )
}

