import { Box, Static, Text, useApp, useInput } from "ink"
import { writeFileSync } from "node:fs"
import { useCallback, useEffect, useReducer, useRef, useState } from "react"

import { CLASSIC_COMMAND, COMMANDS, CURRENT_FRONTEND, EFFORTS, FRONTENDS, FRONTEND_REQUEST_ID, NAME_WIDTH, parseSlash, routeOf, sessionArgs } from "../commands.js"
import { KvistCore, type CoreCommand } from "../core.js"
import type { CoreEvent } from "../protocol.js"
import { contextLabel } from "../format.js"
import { initialState, reduce } from "../state.js"
import { InputBox } from "./input.js"
import { ItemView } from "./items.js"
import { Live } from "./live.js"
import { Prompt } from "./prompts.js"
import { color } from "./theme.js"

export interface AppProps {
  command: CoreCommand
  workspace: string
  provider?: string
  models?: { fast: string; smart: string; planner: string }
  role: string
  approvalMode: string
}

const STOP_CONFIRM_MS = 3000
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

function Footer({ running, armed, usage }: { running: boolean; armed: boolean; usage: { context: number; limit: number } }) {
  const context = contextLabel(usage.context, usage.limit)
  return (
    <Box justifyContent="space-between" paddingX={2}>
      {armed ? <Text color={color.warn}>esc again to interrupt</Text> : running ? <Text color={color.dim}>esc interrupt</Text> : <Text> </Text>}
      <Text>
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

const CLASSIC_NOTE = `Available in the classic terminal: ${CLASSIC_COMMAND}`

const HELP = [
  ...COMMANDS.map((c) => `/${c.name.padEnd(NAME_WIDTH)} ${c.hint}`),
  "",
  "enter send · esc esc stop a run · ctrl+d quit",
].join("\n")

export function App(props: AppProps) {
  const { exit } = useApp()
  const [state, dispatch] = useReducer(reduce, undefined, () => initialState(initialEffort()))
  const [history, setHistory] = useState<string[]>([])
  const [stopArmedAt, setStopArmedAt] = useState(0)
  const core = useRef<KvistCore | null>(null)
  const stateRef = useRef(state)
  stateRef.current = state

  const note = useCallback((text: string, tone: "info" | "warn" | "error" = "info") => dispatch({ type: "note", tone, text }), [])

  useEffect(() => {
    const instance = new KvistCore(props.command)
    core.current = instance
    instance.on("event", (event: CoreEvent) => dispatch({ type: "event", event }))
    instance.on("diagnostic", (text: string) => dispatch({ type: "event", event: { type: "debug", message: text.trim() } }))
    instance.on("fault", (error: Error) => note(error.message, "error"))
    instance.on("exit", ({ code, expected }: { code: number | null; expected: boolean }) => dispatch({ type: "core_exit", expected, code }))
    instance.start({
      workspace: props.workspace,
      role: props.role,
      approvalMode: props.approvalMode,
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

  const runLocal = useCallback((name: string) => {
    switch (name) {
      case "help":
        return note(HELP)
      case "quit":
        return void quit()
      case "clear":
        process.stdout.write("\x1b[2J\x1b[3J\x1b[H")
        return dispatch({ type: "clear" })
      case "debug":
        return dispatch({ type: "toggle_debug" })
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
            current_value: CURRENT_FRONTEND,
          },
        })
    }
  }, [note, quit])

  const switchFrontend = useCallback((value: string) => {
    if (value === CURRENT_FRONTEND) return
    const target = process.env.KVIST_SWITCH_FILE
    if (!target) return note("Switching needs Kvist to be started through main.py.", "warn")
    try {
      writeFileSync(target, value, "utf8")
    } catch (error) {
      return note(`Could not record the choice: ${error instanceof Error ? error.message : String(error)}`, "error")
    }
    // The launcher reads the file, saves the preference and starts that interface.
    process.exitCode = SWITCH_CODE
    void quit()
  }, [note, quit])

  const runCommand = useCallback((text: string) => {
    const parsed = parseSlash(text)
    if (!parsed) return
    const { name, args } = parsed
    switch (routeOf(name)) {
      case "local":
        return runLocal(name)
      case "core":
        return sendCommand(name, name === "session" ? sessionArgs(args) : name === "new" ? args.slice(0, 1) : args)
      case "classic":
        return note(CLASSIC_NOTE, "warn")
      default:
        return note(`Unknown command /${name}. Try /help.`, "warn")
    }
  }, [runLocal, sendCommand, note])

  const submit = useCallback((raw: string) => {
    const text = raw.trim()
    if (!text) return
    setHistory((old) => [...old, text])
    if (text.startsWith("/")) return void runCommand(text)
    try {
      core.current?.send(text)
      dispatch({ type: "submit", text })
    } catch (error) {
      note(error instanceof Error ? error.message : String(error), "error")
    }
  }, [runCommand, note])

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

  useInput((input, key) => {
    const current = stateRef.current
    if (input === "d" && key.ctrl && !current.running && !current.pending) return void quit()
    if (key.ctrl && input === "c") {
      // A command is not a turn: there is nothing to cancel in the core, and
      // quitting would take the whole process down with it.
      if (current.command) return current.pending ? answer(null) : undefined
      if (current.running) return core.current?.cancel()
      return void quit()
    }
    if (key.escape && stateRef.current.running && !stateRef.current.pending) {
      const now = Date.now()
      if (now - stopArmedAt < STOP_CONFIRM_MS) {
        setStopArmedAt(0)
        return core.current?.cancel()
      }
      setStopArmedAt(now)
    }
  })

  useEffect(() => {
    if (!stopArmedAt) return
    const timer = setTimeout(() => setStopArmedAt(0), STOP_CONFIRM_MS)
    return () => clearTimeout(timer)
  }, [stopArmedAt])

  return (
    <>
      <Static key={state.epoch} items={state.items}>
        {(item) => (
          <Box key={item.id} flexDirection="column">
            <ItemView item={item} showDebug={state.showDebug} />
          </Box>
        )}
      </Static>
      <Live state={state} />
      {state.pending ? (
        <Prompt key={state.pending.request_id} request={state.pending} onAnswer={answer} />
      ) : (
        <InputBox
          active={!state.running && !state.command}
          history={history}
          placeholder={state.command ? `running /${state.command}…` : state.running ? "working…" : "Ask Kvist, or / for commands"}
          caption={<ModeLine model={state.info.model} role={state.info.routedRole ?? (state.info.role || props.role)} effort={state.effort} />}
          onSubmit={submit}
        />
      )}
      <Footer running={state.running} armed={stopArmedAt > 0} usage={state.usage} />
    </>
  )
}
