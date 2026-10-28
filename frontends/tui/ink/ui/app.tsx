import { Box, Static, Text, useApp, useInput } from "ink"
import { writeFileSync } from "node:fs"
import { useCallback, useEffect, useReducer, useRef, useState } from "react"

import { CLASSIC_COMMAND, COMMANDS, EFFORTS, FRONTENDS, FRONTEND_REQUEST_ID, NAME_WIDTH, frontendOf, parseSlash, routeOf, sessionArgs } from "../commands.js"
import { copyToClipboard } from "../clipboard.js"
import { restoreWindowTitle, setWindowTitle } from "../window-title.js"
import { KvistCore, type CoreCommand } from "../core.js"
import type { Pointer } from "../pointer.js"
import type { CoreEvent } from "../protocol.js"
import { contextLabel, englishOnly } from "../format.js"
import { addInputHistory, inputHistory, sessionHistory } from "../session.js"
import { answerFromEnd, initialState, reduce, requestIsRussian } from "../state.js"
import type { Starters } from "../starters.js"
import { ClaudeEffort, ClaudeFooter, ClaudeLiveBody, ClaudeScreen, ClaudeSpinner } from "./skins/claude-code/claude.js"
import { ViewContext } from "./context.js"
import { ClosedBar, InputBox } from "./input.js"
import { JobStrip, ScreenLine } from "./jobs.js"
import { ItemView } from "./skins/kvist/items.js"
import { Live } from "./skins/kvist/live.js"
import { Prompt } from "./prompts.js"
import { color, type Skin } from "./theme.js"

export interface AppProps {
  command: CoreCommand
  workspace: string
  provider?: string
  models?: { fast: string; smart: string }
  role: string
  approvalMode: string
  resume?: string
  skin: Skin
  // Set for the fullscreen skin, which owns the mouse.
  pointer: Pointer | null
  // Told the session that is on screen, so the launcher can point back to it on exit.
  onSession?: (name: string) => void
  // Requests offered to a person who has not sent one yet (null: none).
  starters?: Starters | null
}

const STOP_CONFIRM_MS = 3000
// How long the "press Ctrl+C again" warning stays, and so how long a second press counts.
const EXIT_WARNING_MS = 2000
const TOAST_MS = 2500
const SWITCH_CODE = 64

// Build is the only mode there is; the label is here so the line already has
// the shape it will keep when a second one exists.
function ModeLine({ model, role, effort }: { model: string; role: string; effort: string }) {
  const parts = ["Build", model, role, effort === "false" ? "no reasoning" : effort].filter(Boolean)
  return (
    <Box paddingX={2}>
      <Text color={color.dim} wrap="truncate-end">{parts.join(" · ")}</Text>
    </Box>
  )
}

function Footer({ running, armed, usage, version }: { running: boolean; armed: boolean; usage: { context: number; limit: number }; version?: string }) {
  const context = contextLabel(usage.context, usage.limit)
  // One row on any width: a wrapped footer is an unbudgeted row in the live frame.
  return (
    <Box justifyContent="space-between" paddingX={2}>
      {armed ? <Text color={color.warn} wrap="truncate-end">esc again to pause · ctrl+c stops all</Text> : running ? <Text color={color.dim} wrap="truncate-end">esc pause · ctrl+c stop all</Text> : <Text> </Text>}
      <Text wrap="truncate-end">
        {version ? <Text color={color.dim}>v{version}   </Text> : null}
        {context ? <Text color={color.dim}>{context}   </Text> : null}
        <Text>/</Text>
        <Text color={color.dim}> commands</Text>
      </Text>
    </Box>
  )
}

function initialEffort(): string {
  const value = process.env.AGENT_REASONING_EFFORT ?? ""
  return (EFFORTS as readonly string[]).includes(value) ? value : ""
}

const CLASSIC_NOTE = `Available in Classic Legacy: ${CLASSIC_COMMAND}`

const HELP = [
  ...COMMANDS.map((c) => `/${c.name.padEnd(NAME_WIDTH)} ${c.hint}`),
  "",
  "enter send · esc esc stop a run · ctrl+d quit",
].join("\n")

export function App(props: AppProps) {
  const { exit } = useApp()
  const [state, dispatch] = useReducer(reduce, undefined, () => initialState(initialEffort(), props.skin === "claude"))
  const [history, setHistory] = useState<string[]>([])
  // Typed lines of earlier runs are read from the core; starters wait for them so they do not
  // flash up for a person who already has a history.
  const [historyRead, setHistoryRead] = useState(false)
  const [stopArmedAt, setStopArmedAt] = useState(0)
  // A first Ctrl+C or Ctrl+D at the idle prompt only warns, naming the key; the ref lets a second press
  // in the same key chunk see it. Only the same key confirms.
  const [exitWarning, setExitWarning] = useState<"C" | "D" | null>(null)
  const exitArmed = useRef<"C" | "D" | null>(null)
  const [toast, setToast] = useState("")
  // Ctrl+O in the fullscreen skin: commands with their output instead of a one-line summary.
  const [expanded, setExpanded] = useState(false)
  const core = useRef<KvistCore | null>(null)
  const stateRef = useRef(state)
  stateRef.current = state

  const note = useCallback((text: string, tone: "info" | "warn" | "error" = "info") => dispatch({ type: "note", tone, text }), [])

  useEffect(() => {
    void inputHistory(props.command, props.workspace).then((entries) => {
      setHistory((typed) => [...entries, ...typed])
      setHistoryRead(true)
    })
  }, [props.command, props.workspace])

  useEffect(() => {
    const instance = new KvistCore(props.command)
    core.current = instance
    // Switching to a session that already has a feed: the reducer has emptied the screen,
    // and the session's own journal fills it back in.
    const redrawSession = async (event: Extract<CoreEvent, { type: "command_result" }>) => {
      const name = event.effects?.session
      if (!event.ok || event.effects?.changed !== true || event.name === "new" || typeof name !== "string") return
      const history = await sessionHistory(props.command, props.workspace, name)
      if (!history.ok || history.events.length === 0 || stateRef.current.running) return
      dispatch({ type: "restore", events: history.events, banner: true })
      dispatch({ type: "note", tone: "info", text: englishOnly(event.message) })
    }
    instance.on("event", (event: CoreEvent) => {
      dispatch({ type: "event", event })
      if (event.type === "hello" && props.resume !== undefined) dispatch({ type: "resume_start" })
      if (event.type === "command_result") void redrawSession(event)
    })
    instance.on("diagnostic", (text: string) => dispatch({ type: "event", event: { type: "debug", message: text.trim() } }))
    instance.on("fault", (error: Error) => note(error.message, "error"))
    instance.on("exit", ({ code, expected }: { code: number | null; expected: boolean }) => dispatch({ type: "core_exit", expected, code }))
    instance.start({
      workspace: props.workspace,
      role: props.role,
      approvalMode: props.approvalMode,
      resume: props.resume,
      provider: props.provider,
      models: props.models,
    })
    return () => void instance.stop()
  }, [props, note])

  const quit = useCallback(async () => {
    await core.current?.stop()
    exit()
  }, [exit])

  const sendCommand = useCallback((name: string, args: string[]) => {
    if (stateRef.current.running || stateRef.current.command) return note("Wait for the current run to finish.", "warn")
    try {
      core.current?.sendCommand(name, args)
      // `resume` is a turn of the agent, not a wizard: the core answers it with
      // the turn's events and `turn_end`, never with `command_result`.
      dispatch(name === "resume" ? { type: "resume_start" } : { type: "command_start", name })
    } catch (error) {
      note(error instanceof Error ? error.message : String(error), "error")
    }
  }, [note])

  const runLocal = useCallback((name: string, args: string[]) => {
    switch (name) {
      case "help":
        return note(HELP)
      case "quit":
        return void quit()
      case "clear":
        // The fullscreen skin redraws the whole screen from state.
        if (props.skin !== "claude") process.stdout.write("\x1b[2J\x1b[3J\x1b[H")
        return dispatch({ type: "clear" })
      case "debug":
        return dispatch({ type: "toggle_debug" })
      case "copy": {
        const fromEnd = Math.max(1, Number.parseInt(args[0] ?? "1", 10) || 1)
        const text = answerFromEnd(stateRef.current.items, fromEnd)
        if (!text) return note("No answer to copy yet.", "warn")
        const { chars } = copyToClipboard(text)
        return note(`Copied ${chars} character${chars === 1 ? "" : "s"} to the clipboard.`)
      }
      case "tui":
        // A local dialog: it is answered here, never sent to the core.
        return dispatch({
          type: "event",
          event: {
            type: "input_request",
            request_id: FRONTEND_REQUEST_ID,
            kind: "choice",
            title: "Terminal interface",
            body: "",
            help_text: "The choice is saved and used from the next launch.",
            options: FRONTENDS.map(({ value, label, description }) => ({ value, label, description })),
            current_value: frontendOf(props.skin),
          },
        })
    }
  }, [note, quit, props.skin])

  const switchFrontend = useCallback((value: string) => {
    if (value === frontendOf(props.skin)) return
    const target = process.env.KVIST_SWITCH_FILE
    if (!target) return note("Switching needs Kvist to be started through its launcher (kvist or python -m kernel).", "warn")
    try {
      writeFileSync(target, value, "utf8")
    } catch (error) {
      return note(`Could not record the choice: ${error instanceof Error ? error.message : String(error)}`, "error")
    }
    // The launcher reads the file, saves the preference and starts that interface.
    process.exitCode = SWITCH_CODE
    void quit()
  }, [note, quit, props.skin])

  const runCommand = useCallback((text: string) => {
    const parsed = parseSlash(text)
    if (!parsed) return
    const { name, args } = parsed
    switch (routeOf(name)) {
      case "local":
        return runLocal(name, args)
      case "core":
        return sendCommand(name, name === "session" ? sessionArgs(args) : name === "new" ? args.slice(0, 1) : args)
      case "classic":
        return note(CLASSIC_NOTE, "warn")
      default:
        return note(`Unknown command /${name}. Try /help.`, "warn")
    }
  }, [runLocal, sendCommand, note])

  const submit = useCallback((raw: string, shown: string = raw) => {
    const text = raw.trim()
    if (!text) return
    setHistory((old) => [...old, shown])
    void addInputHistory(props.command, props.workspace, shown)
    // A "goal" command is an ordinary request the core expands itself.
    if (text.startsWith("/") && routeOf(parseSlash(text)?.name ?? "") !== "goal") {
      // The reply of a command hangs off its echo, as in Claude Code.
      if (props.skin === "claude") dispatch({ type: "echo", text })
      return void runCommand(text)
    }
    try {
      core.current?.send(text)
      dispatch({ type: "submit", text: shown.trim(), request: text })
    } catch (error) {
      note(error instanceof Error ? error.message : String(error), "error")
    }
  }, [runCommand, note, props.skin, props.command, props.workspace])

  const answer = useCallback((value: boolean | string | null | { approved: boolean; remember: boolean }) => {
    const request = stateRef.current.pending
    if (!request) return
    if (request.request_id === FRONTEND_REQUEST_ID) {
      dispatch({ type: "answered" })
      return typeof value === "string" ? switchFrontend(value) : undefined
    }
    if (typeof value === "object" && value !== null && "approved" in value && "remember" in value) {
      core.current?.respond(request.request_id, value.approved, value.remember)
    } else {
      core.current?.respond(request.request_id, value)
    }
    dispatch({ type: "answered" })
  }, [switchFrontend])

  // Ctrl+C stops everything the task runs; Escape (twice) pauses it and keeps its state.
  const stop = () => {
    dispatch({ type: "interrupt" })
    core.current?.cancel()
  }

  const pause = () => {
    core.current?.pause()
  }

  // The fullscreen skin asks for a second press, as Claude Code does, so a stray one costs nothing.
  const askToExit = (letter: "C" | "D") => {
    if (props.skin !== "claude" || exitArmed.current === letter) return void quit()
    exitArmed.current = letter
    setExitWarning(letter)
  }

  useInput((input, key) => {
    const current = stateRef.current
    if (input === "d" && key.ctrl && !current.running && !current.pending) return askToExit("D")
    if (input === "o" && key.ctrl && props.skin === "claude") return setExpanded((on) => !on)
    if (key.ctrl && input === "c") {
      // A command is not a turn: there is nothing to cancel in the core, and
      // quitting would take the whole process down with it.
      if (current.command) return current.pending ? answer(null) : undefined
      if (current.running) return stop()
      return askToExit("C")
    }
    if (key.escape && stateRef.current.running && !stateRef.current.pending) {
      const now = Date.now()
      if (now - stopArmedAt < STOP_CONFIRM_MS) {
        setStopArmedAt(0)
        return pause()
      }
      setStopArmedAt(now)
    }
  })

  useEffect(() => {
    if (!stopArmedAt) return
    const timer = setTimeout(() => setStopArmedAt(0), STOP_CONFIRM_MS)
    return () => clearTimeout(timer)
  }, [stopArmedAt])

  useEffect(() => {
    if (!exitWarning) return
    const timer = setTimeout(() => {
      exitArmed.current = null
      setExitWarning(null)
    }, EXIT_WARNING_MS)
    return () => clearTimeout(timer)
  }, [exitWarning])

  useEffect(() => {
    if (!props.pointer) return
    let timer: ReturnType<typeof setTimeout> | undefined
    const off = props.pointer.onCopied(({ chars }) => {
      setToast(`copied ${chars} character${chars === 1 ? "" : "s"}`)
      clearTimeout(timer)
      timer = setTimeout(() => setToast(""), TOAST_MS)
    })
    return () => {
      off()
      clearTimeout(timer)
    }
  }, [props.pointer])

  useEffect(() => {
    if (state.info.session) props.onSession?.(state.info.session)
  }, [state.info.session, props])

  useEffect(() => {
    setWindowTitle(state.info.title)
  }, [state.info.title])

  useEffect(() => () => restoreWindowTitle(), [])

  const claude = props.skin === "claude"
  // The role the router picked belongs to the turn that is running; between turns the footer
  // shows the configured one again (usually "auto").
  const configuredRole = state.info.role || props.role
  const role = state.running ? (state.info.routedRole ?? configuredRole) : configuredRole
  const armed = stopArmedAt > 0
  const russian = requestIsRussian(state)
  const view = { workspace: props.workspace, info: state.info, effort: state.effort, pointer: props.pointer, expanded, russian }
  const jobs = (
    <>
      <ScreenLine screen={state.screen} russian={russian} />
      <JobStrip jobs={state.jobs} russian={russian} />
    </>
  )

  const caption = claude
    ? <ClaudeFooter running={state.running} armed={armed} usage={state.usage} model={state.info.model} role={role} toast={toast} exitWarning={exitWarning} />
    : <ModeLine model={state.info.model} role={role} effort={state.effort} />
  const input = state.pending ? (
    <Prompt key={state.pending.request_id} request={state.pending} onAnswer={answer} />
  ) : state.closed !== null ? (
    // Enter takes the same way as /new.
    <ClosedBar notice={state.closed} active={!state.running && !state.command} caption={caption} onNewSession={() => sendCommand("new", [])} />
  ) : (
    <InputBox
      active={!state.running && !state.command}
      history={history}
      placeholder={state.command ? `running /${state.command}…` : state.running ? "working…" : "Ask Kvist, or / for commands"}
      starters={historyRead && history.length === 0 && !state.items.some((item) => item.kind === "user") ? props.starters : null}
      caption={caption}
      onSubmit={submit}
    />
  )

  if (claude) {
    return (
      <ViewContext.Provider value={view}>
        <ClaudeScreen
          items={state.items}
          showDebug={state.showDebug}
          followKey={history.length}
          live={<ClaudeLiveBody state={state} />}
          spinner={<><ClaudeSpinner state={state} />{jobs}</>}
          hint={state.pending ? null : <ClaudeEffort effort={state.effort} />}
        >
          {input}
        </ClaudeScreen>
      </ViewContext.Provider>
    )
  }

  return (
    <ViewContext.Provider value={view}>
      <Static key={state.epoch} items={state.items}>
        {(item) => (
          <Box key={item.id} flexDirection="column">
            <ItemView item={item} showDebug={state.showDebug} />
          </Box>
        )}
      </Static>
      <Live state={state} />
      {jobs}
      {input}
      <Footer running={state.running} armed={armed} usage={state.usage} version={state.info.kvistVersion} />
    </ViewContext.Provider>
  )
}
