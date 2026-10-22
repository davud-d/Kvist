// The prompt of OpenCode's home and session screens. The layout, colours, fades and the status
// row are OpenCode's (packages/tui/src/component/prompt/index.tsx); what stands behind them is
// Kvist: a request goes to the core, `/` lists the commands registered in the keymap and `@`
// lists the project's files.

import { type BoxRenderable, type KeyEvent, type RGBA, ScrollBoxRenderable, TextareaRenderable } from "@opentui/core"
import { useTerminalDimensions } from "@opentui/solid"
import { createEffect, createMemo, createResource, createSignal, For, on, onCleanup, onMount, Show, type JSX } from "solid-js"

import { PromptMetadataRow } from "../oc/tui/component/prompt/metadata"
import { Spinner } from "../oc/tui/component/spinner"
import { Keymap, type KeymapCommand } from "../oc/tui/context/keymap"
import { useTheme } from "../oc/tui/context/theme"
import { createAnimatable, tween } from "../oc/tui/ui/animation"
import { EmptyBorder, SplitBorder } from "../oc/tui/ui/border"
import { createColors, createFrames } from "../oc/tui/ui/spinner"
import { useToast } from "../oc/tui/ui/toast"
import { useConfig } from "../oc/tui/config"
import { tint } from "../oc/tui/theme/color"
import { Locale } from "../oc/tui/util/locale"
import { createFadeIn } from "../oc/tui/util/signal"
import { starterSet } from "../../../ink/starters"
import { listWorkspaceFiles, mentionAt } from "../files"
import { useKvist } from "../kvist"
import { rank } from "../ui/fuzzy"
import { roleColor, titlecase } from "./roles"

export type PromptRef = {
  focused: boolean
  readonly text: string
  set(text: string): void
  blur(): void
  focus(): void
  // A list of candidates is open: Tab and Enter belong to it.
  readonly suggesting: boolean
}

export type PromptProps = {
  ref?: (ref: PromptRef | undefined) => void
  visible?: boolean
  disabled?: boolean
  muted?: boolean
  placeholders?: string[]
  // The commands `/` lists. They come from the app, not from the keymap: while the list is open the keymap
  // is in "autocomplete" mode and reports only the commands that work there.
  commands: readonly KeymapCommand[]
  // The row under the box: status on the left, hints on the right (OpenCode's `prompt.footer`).
  footer?: JSX.Element
  // Offer three first requests under an empty box (until there is a request or a typed line to come back to).
  starters?: boolean
}

// One row of the list above the box, whatever it completes. Enter runs it, Tab only writes it.
interface Candidate {
  display: string
  description?: string
  accept(): void
  complete(): void
}

const MAX_FILE_CANDIDATES = 50
const AUTOCOMPLETE_ROWS = 10

// "/mo" matches the commands that start with it first, then the ones that contain it.
export function matchSlash<T extends { name: string }>(text: string, items: readonly T[]): T[] {
  const match = /^\/(\S*)$/.exec(text)
  if (!match) return []
  const query = (match[1] ?? "").toLowerCase()
  const starts = items.filter((item) => item.name.startsWith(query))
  const inside = items.filter((item) => !item.name.startsWith(query) && item.name.includes(query))
  return [...starts, ...inside]
}

function PromptInterruptStatus(props: {
  armed: boolean
  animations?: boolean
  text: RGBA
  subdued: RGBA
  warning: RGBA
  flash?: RGBA
}) {
  const ignition = createAnimatable(
    { level: 0 },
    { enabled: () => props.animations ?? false, transition: tween({ duration: 0.22 }) },
  )
  createEffect(
    on(
      () => props.armed,
      (armed) => {
        if (!armed || !props.animations) return ignition.jump({ level: 0 })
        ignition.jump({ level: 0.75 })
        ignition.animate({ level: 0 })
      },
      { defer: true },
    ),
  )
  const armedColor = createMemo(() => {
    const level = ignition.value().level
    if (level === 0 || !props.flash) return props.warning
    return tint(props.warning, props.flash, level)
  })

  return (
    <text fg={props.armed ? armedColor() : props.text} wrapMode="none" truncate flexShrink={1}>
      esc{" "}
      <span style={{ fg: props.armed ? armedColor() : props.subdued }}>{props.armed ? "again to pause" : "pause"}</span>
    </text>
  )
}

export { PromptInterruptStatus }

export function Prompt(props: PromptProps) {
  const kvist = useKvist()
  const theme = useTheme()
  const toast = useToast()
  const config = useConfig().data
  const dimensions = useTerminalDimensions()
    const [text, setText] = createSignal("")
  const [caret, setCaret] = createSignal(0)
  const [picked, setPicked] = createSignal(0)
  const [dismissed, setDismissed] = createSignal("")
  const [wantFiles, setWantFiles] = createSignal(false)
  const leader = Keymap.useLeaderActive()
  const muted = () => leader() || props.muted
  const [placeholderIndex] = createSignal(Math.floor(Math.random() * Math.max(1, props.placeholders?.length ?? 1)))
  let input: TextareaRenderable | undefined
  let anchor: BoxRenderable | undefined
  let scroll: ScrollBoxRenderable | undefined
  // Walking back through earlier requests: -1 is the draft being typed.
  let history = -1
  let draft = ""

  const state = kvist.state
  const animationsEnabled = createMemo(() => config.animations ?? true)
  const disabled = () => props.disabled === true
  const files = createResource(wantFiles, (wanted) => (wanted ? listWorkspaceFiles(kvist.workspace) : []))[0]

  const put = (value: string, at = value.length) => {
    if (!input || input.isDestroyed) return
    input.setText(value)
    input.cursorOffset = at
    setText(value)
    setCaret(at)
  }

  // One row per command, alphabetical, descriptions aligned. An alias is not a row of its own: it only
  // brings the command up once the person types it (from habit or memory).
  const named = createMemo(() =>
    props.commands
      .flatMap((command) => (command.slash ? [{ name: command.slash.name, command }] : []))
      .sort((a, b) => a.name.localeCompare(b.name)),
  )
  const aliased = createMemo(() =>
    props.commands.flatMap((command) => (command.slash?.aliases ?? []).map((name) => ({ name, command: command, main: command.slash!.name }))),
  )
  const slashWidth = createMemo(() => Math.max(0, ...named().map((entry) => entry.name.length)) + 1)

  const slashCandidates = (): Candidate[] => {
    const direct = matchSlash(text(), named())
    const shown = new Set(direct.map((entry) => entry.command))
    const byAlias = matchSlash(text(), aliased()).filter((entry) => !shown.has(entry.command))
    return [...direct, ...byAlias.map((entry) => ({ name: entry.main, command: entry.command }))].map(({ name, command }) => ({
      display: `/${name}`.padEnd(slashWidth() + 2),
      description: command.description ?? command.title,
      accept: () => {
        if (command.slash?.arguments) return put(`/${name} `)
        put("")
        void command.run()
      },
      complete: () => put(`/${name} `),
    }))
  }

  const fileCandidates = (): Candidate[] => {
    const mention = mentionAt(text(), caret())
    if (!mention) return []
    if (!wantFiles()) queueMicrotask(() => setWantFiles(true))
    return rank(files() ?? [], mention.query, (file) => file)
      .slice(0, MAX_FILE_CANDIDATES)
      .map((file) => {
        const insert = () => {
          const replacement = `@${file} `
          put(text().slice(0, mention.start) + replacement + text().slice(mention.end), mention.start + replacement.length)
        }
        return { display: file, accept: insert, complete: insert }
      })
  }

  const candidates = createMemo<Candidate[]>(() => {
    if (dismissed() === text() + caret()) return []
    const slash = slashCandidates()
    return slash.length ? slash : fileCandidates()
  })

  const role = () => {
    const info = state().info
    return (state().running ? (info.routedRole ?? info.role) : info.role) || "auto"
  }
  const providerLabel = () => {
    const id = state().info.provider
    return kvist.manifest()?.providers.find((entry) => entry.value === id)?.label ?? id
  }
  const variant = () => (state().effort && state().effort !== "false" ? state().effort : undefined)

  const agentColor = () => roleColor(theme, role())
  const highlight = createMemo(() => (muted() ? theme.border.base : agentColor()))
  const animateMetadata = true
  const metadataAnimationsEnabled = () => animationsEnabled() && animateMetadata
  const agentMetaAlpha = createFadeIn(() => true, metadataAnimationsEnabled)
  const modelMetaAlpha = createFadeIn(() => true, metadataAnimationsEnabled)
  const variantMetaAlpha = createFadeIn(() => !!variant(), metadataAnimationsEnabled)
  const borderHighlight = createMemo(() => tint(theme.border.base, highlight(), agentMetaAlpha()))

  const placeholderText = createMemo(() => {
    const list = props.placeholders ?? []
    if (!list.length) return undefined
    const value = `Ask anything… "${list[placeholderIndex() % list.length]}"`
    const width = dimensions().width < 44 ? dimensions().width - 5 : Math.min(75, dimensions().width - 4) - 5
    return Locale.takeWidth(value, Math.max(1, width)).trimEnd()
  })

  // The Ink interface's first-request suggestions: the arrows choose, Tab or Enter puts the choice in the box.
  const starterPrompts = createMemo(() => starterSet(kvist.workspace)?.prompts ?? [])
  const [starterPick, setStarterPick] = createSignal(0)
  const offered = () =>
    props.starters && !disabled() && kvist.historyRead() && !kvist.history().length && !state().items.some((item) => item.kind === "user") && !text()
      ? starterPrompts()
      : []
  const takeStarter = () => {
    const chosen = offered()[Math.min(starterPick(), offered().length - 1)]
    if (chosen) put(chosen)
  }

  const submit = () => {
    if (offered().length) return takeStarter()
    const value = text().trim()
    if (!value || disabled()) return
    if (kvist.busy() && !value.startsWith("/")) {
      toast.show({ message: "Kvist is working. Esc twice pauses, ctrl+c stops it.", variant: "warning", duration: 3000 })
      return
    }
    // `/name rest` runs the command with what follows it; anything else is a request for the core.
    const typed = /^\/(\S+)\s*(.*)$/s.exec(value)
    const command = typed && props.commands.find((entry) => entry.slash && [entry.slash.name, ...(entry.slash.aliases ?? [])].includes(typed[1]!))
    if (command && typed) void command.run(typed[2]!.trim() || undefined)
    else kvist.submit(value)
    history = -1
    put("")
  }

  const walkHistory = (delta: 1 | -1): boolean => {
    const entries = kvist.history()
    if (!entries.length) return false
    if (delta === 1 && history === -1) return false
    if (history === -1) draft = text()
    const next = Math.min(entries.length - 1, Math.max(-1, history + (delta === -1 ? 1 : -1)))
    if (next === history) return false
    history = next
    const shown = history === -1 ? draft : entries[entries.length - 1 - history]!
    put(shown, delta === -1 ? 0 : shown.length)
    return true
  }

  const move = (delta: number, count: number) => {
    const next = (picked() + delta + count) % count
    setPicked(next)
    scroll?.scrollChildIntoView(`prompt-candidate-${next}`)
  }

  const onKeyDown = (event: KeyEvent) => {
    if (disabled()) return event.preventDefault()
    if (event.name === "tab" && !event.shift && offered().length) {
      takeStarter()
      return event.preventDefault()
    }
  }

  const keymap = Keymap.use()
  // While a list is open it owns Up, Down, Tab, Enter and Escape: OpenCode's own "autocomplete" mode.
  createEffect(() => {
    if (!candidates().length) return
    const pop = keymap.mode.push("autocomplete")
    onCleanup(pop)
  })
  Keymap.createLayer(() => ({
    mode: "autocomplete",
    target: () => input,
    enabled: () => candidates().length > 0,
    commands: [
      { id: "prompt.autocomplete.prev", title: "Previous autocomplete item", group: "Autocomplete", run: () => move(-1, candidates().length) },
      { id: "prompt.autocomplete.next", title: "Next autocomplete item", group: "Autocomplete", run: () => move(1, candidates().length) },
      { id: "prompt.autocomplete.hide", title: "Hide autocomplete", group: "Autocomplete", run: () => setDismissed(text() + caret()) },
      { id: "prompt.autocomplete.select", title: "Select autocomplete item", group: "Autocomplete", run: () => candidates()[picked()]?.accept() },
      { id: "prompt.autocomplete.complete", title: "Complete autocomplete item", group: "Autocomplete", run: () => candidates()[picked()]?.complete() },
    ],
  }))

  // Up and Down as in OpenCode (and the Ink interface): inside the text they move between lines; where they
  // cannot, the first press goes to the start or end of the text and the next one walks the earlier requests.
  // Under an empty box that offers first requests they choose among those.
  const chooseStarter = (delta: 1 | -1) => {
    const count = offered().length
    setStarterPick((Math.min(starterPick(), count - 1) + delta + count) % count)
  }
  Keymap.createLayer(() => ({
    priority: 1,
    target: () => input,
    enabled: () => input !== undefined && !disabled() && candidates().length === 0,
    commands: [
      {
        id: "prompt.history.previous",
        title: "Previous history item",
        group: "Prompt",
        bind: "up",
        run: () => {
          if (!input) return false
          if (offered().length) return chooseStarter(-1)
          if (input.cursorOffset !== 0) {
            if (input.scrollY + input.visualCursor.visualRow === 0) input.cursorOffset = 0
            else input.moveCursorUp()
            return
          }
          return walkHistory(-1) ? undefined : false
        },
      },
      {
        id: "prompt.history.next",
        title: "Next history item",
        group: "Prompt",
        bind: "down",
        run: () => {
          if (!input) return false
          if (offered().length) return chooseStarter(1)
          const end = input.plainText.length
          if (input.cursorOffset !== end) {
            if (input.scrollY + input.visualCursor.visualRow === Math.max(0, input.editorView.getTotalVirtualLineCount() - 1)) input.cursorOffset = end
            else input.moveCursorDown()
            return
          }
          return walkHistory(1) ? undefined : false
        },
      },
    ],
  }))

  const ref: PromptRef = {
    get focused() {
      return input?.focused ?? false
    },
    get text() {
      return text()
    },
    get suggesting() {
      return candidates().length > 0
    },
    set: put,
    blur: () => input?.blur(),
    focus: () => input?.focus(),
  }

  onMount(() => {
    props.ref?.(ref)
    setTimeout(() => input && !input.isDestroyed && input.focus(), 1)
  })

  createEffect(() => {
    if (!input || input.isDestroyed) return
    input.cursorColor = disabled() ? theme.background.raised.base : theme.text.base
    if (config.cursor) input.cursorStyle = config.cursor
  })

  const maxHeight = createMemo(() => Math.max(6, Math.floor(dimensions().height / 3)))
  const promptBg = createMemo(() => theme.decrease(theme.background.raised.base))
  const running = () => state().running

  const spinnerDef = createMemo(() => {
    const color = agentColor()
    return {
      frames: createFrames({ color, style: "blocks", inactiveFactor: 0.6, minAlpha: 0.3 }),
      color: createColors({ color, style: "blocks", inactiveFactor: 0.6, minAlpha: 0.3 }),
    }
  })

  const autocompleteHeight = () => Math.min(AUTOCOMPLETE_ROWS, Math.max(1, candidates().length))
  const [anchorHeight, setAnchorHeight] = createSignal(0)

  return (
    <>
      <box
        ref={(r: BoxRenderable) => (anchor = r)}
        visible={props.visible !== false}
        width="100%"
        onSizeChange={function (this: BoxRenderable) {
          setAnchorHeight(this.height)
        }}
      >
        <Show when={candidates().length}>
          <box position="absolute" bottom={anchorHeight()} left={0} right={0} zIndex={100} {...SplitBorder} borderColor={theme.border.base}>
            <scrollbox
              ref={(r: ScrollBoxRenderable) => (scroll = r)}
              backgroundColor={theme.background.raised.high}
              height={autocompleteHeight()}
              scrollbarOptions={{ visible: false }}
            >
              <For each={candidates()}>
                {(candidate, index) => (
                  <box
                    id={`prompt-candidate-${index()}`}
                    paddingLeft={1}
                    paddingRight={1}
                    backgroundColor={index() === picked() ? theme.background.action.primary.focused : undefined}
                    flexDirection="row"
                    onMouseMove={() => setPicked(index())}
                    onMouseUp={() => candidate.accept()}
                  >
                    <text fg={index() === picked() ? theme.text.action.primary.focused : theme.text.base} flexShrink={0} wrapMode="none">
                      {candidate.display}
                    </text>
                    <Show when={candidate.description}>
                      <text fg={index() === picked() ? theme.text.action.primary.focused : theme.text.muted} wrapMode="none" flexShrink={1} minWidth={0}>
                        {" " + candidate.description?.replace(/\s+/g, " ").trim()}
                      </text>
                    </Show>
                  </box>
                )}
              </For>
            </scrollbox>
          </box>
        </Show>
        <box
          width="100%"
          border={["left"]}
          borderColor={borderHighlight()}
          customBorderChars={{ ...SplitBorder.customBorderChars, bottomLeft: "╹" }}
        >
          <box
            paddingLeft={dimensions().width < 44 ? 1 : 2}
            paddingRight={dimensions().width < 44 ? 1 : 2}
            paddingTop={1}
            flexShrink={0}
            backgroundColor={promptBg()}
            flexGrow={1}
            width="100%"
          >
            <textarea
              width="100%"
              placeholder={placeholderText()}
              placeholderColor={theme.text.muted}
              textColor={muted() ? theme.text.muted : theme.text.base}
              focusedTextColor={muted() ? theme.text.muted : theme.text.base}
              minHeight={1}
              maxHeight={maxHeight()}
              cursorStyle={config.cursor}
              onContentChange={() => {
                setText(input?.plainText ?? "")
                setCaret(input?.cursorOffset ?? 0)
                setPicked(0)
              }}
              onCursorChange={() => setCaret(input?.cursorOffset ?? 0)}
              onKeyDown={onKeyDown}
              onSubmit={() => {
                if (disabled()) return
                setTimeout(() => setTimeout(() => submit(), 0), 0)
              }}
              ref={(r: TextareaRenderable) => (input = r)}
              focusedBackgroundColor="transparent"
              cursorColor={disabled() ? theme.background.raised.base : theme.text.base}
              syntaxStyle={undefined}
            />
            <box flexDirection="row" flexShrink={0} paddingTop={1} gap={1} justifyContent="space-between">
              <PromptMetadataRow
                mode="normal"
                agent={titlecase(role())}
                auto={false}
                model={state().info.model}
                provider={providerLabel()}
                variant={variant()}
                muted={!!muted()}
                highlight={highlight()}
                agentAlpha={agentMetaAlpha()}
                modelAlpha={modelMetaAlpha()}
                variantAlpha={variantMetaAlpha()}
              />
            </box>
          </box>
        </box>
        <box
          height={1}
          border={["left"]}
          borderColor={borderHighlight()}
          customBorderChars={{ ...EmptyBorder, vertical: promptBg().a !== 0 ? "╹" : " " }}
        >
          <box
            height={1}
            border={["bottom"]}
            borderColor={promptBg()}
            customBorderChars={promptBg().a !== 0 ? { ...EmptyBorder, horizontal: "▀" } : { ...EmptyBorder, horizontal: " " }}
          />
        </box>
        <box width="100%" flexDirection="row" justifyContent="space-between" gap={2}>
          <Show when={props.footer} fallback={<box flexGrow={1} />}>
            {props.footer}
          </Show>
        </box>
      </box>
      <Show when={offered().length}>
        <box flexDirection="column" paddingTop={1} paddingLeft={2} flexShrink={0}>
          <text fg={theme.text.muted}>{starterSet(kvist.workspace)?.title}</text>
          <For each={offered()}>
            {(prompt, index) => (
              <text fg={index() === starterPick() ? theme.text.base : theme.text.muted} wrapMode="none" truncate>
                {index() === starterPick() ? "▸ " : "  "}
                {prompt}
              </text>
            )}
          </For>
          <text fg={theme.text.muted}>{starterSet(kvist.workspace)?.hint}</text>
        </box>
      </Show>
      <Show when={running()}>
        <box position="absolute" visible={false}>
          <Spinner />
        </box>
      </Show>
    </>
  )
}

// The left of the footer row while a turn runs: the spinner in the role's colour and what Esc does.
export function PromptRunning(props: { armed: boolean; color: RGBA; frames: ReturnType<typeof createFrames>; colors: ReturnType<typeof createColors> }) {
  const theme = useTheme()
  const config = useConfig().data
  return (
    <box flexDirection="row" gap={1} flexGrow={1} justifyContent="flex-start">
      <box marginLeft={1}>
        <Show when={config.animations ?? true} fallback={<text fg={theme.text.muted}>[⋯]</text>}>
          <spinner color={props.colors} frames={props.frames} interval={40} />
        </Show>
      </box>
      <PromptInterruptStatus
        armed={props.armed}
        animations={config.animations ?? true}
        text={theme.text.base}
        subdued={theme.text.muted}
        warning={theme.text.feedback.warning.base}
        flash={theme.decrease(theme.text.feedback.warning.base, 2)}
      />
    </box>
  )
}
