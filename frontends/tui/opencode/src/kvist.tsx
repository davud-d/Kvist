// The one place that talks to the Kvist core: it owns the process, folds the event stream
// into the shared reducer (frontends/tui/ink/state.ts) and offers the UI what it may do.
// Everything OpenCode keeps behind its server and SDK is this file here.

import { batch, createContext, createSignal, onCleanup, onMount, useContext, type Accessor, type ParentProps } from "solid-js"

import { parseSlash, routeOf, sessionArgs } from "../../ink/commands.js"
import { KvistCore, type CoreCommand, type CoreOptions } from "../../ink/core.js"
import { englishOnly } from "../../ink/format.js"
import type { CoreEvent, InputRequest } from "../../ink/protocol.js"
import { addInputHistory, inputHistory, sessionHistory } from "../../ink/session.js"
import { initialState, reduce, type Action, type State, type Tone } from "../../ink/state.js"

export interface Manifest {
  roles: { value: string; description: string }[]
  effort_levels: { value: string; description: string; default?: string }[]
  providers: { value: string; label: string }[]
  styles: { value: string; description: string }[]
  slash_commands: { name: string; description: string; aliases?: string[] }[]
}

export interface CommandResult {
  name: string
  ok: boolean
  message: string
  effects: Record<string, unknown>
}

export interface LaunchOptions {
  command: CoreCommand
  core: CoreOptions
  // The id of the interface that is running, for `/tui`.
  frontend: string
}

export interface Kvist {
  state: Accessor<State>
  manifest: Accessor<Manifest | null>
  // Typed lines of earlier runs, oldest first.
  history: Accessor<string[]>
  // False until the earlier lines have been read; what depends on there being none waits for it.
  historyRead: Accessor<boolean>
  workspace: string
  frontend: string
  command: CoreCommand
  // True while a turn or a core command holds the core.
  busy: () => boolean
  submit(text: string): void
  // A core command, resolved with its result; rejects when the core is busy.
  run(name: string, args?: string[]): Promise<CommandResult>
  // A typed slash command: routed to the UI, the core or the classic notice.
  slash(text: string): void
  answer(request: InputRequest, value: boolean | string | null, remember?: boolean): void
  cancel(): void
  pause(): void
  note(text: string, tone?: Tone): void
  dispatch(action: Action): void
  // Redraws the feed of the session the core has switched to.
  redraw(name: string): Promise<void>
  quit(code?: number): Promise<void>
  // Command names the UI answers itself; registered by the app.
  local: { handler: (name: string, args: string[]) => boolean }
}

const context = createContext<Kvist>()

export function useKvist(): Kvist {
  const value = useContext(context)
  if (!value) throw new Error("useKvist must be used inside KvistProvider")
  return value
}

function initialEffort(): string {
  const value = process.env.AGENT_REASONING_EFFORT ?? ""
  return ["false", "low", "medium", "high", "xhigh", "max"].includes(value) ? value : ""
}

const CLASSIC_NOTE = "Available in Classic Legacy: python -m kernel --frontend terminal"

export function KvistProvider(props: ParentProps<{ options: LaunchOptions; onQuit: (code: number) => void }>) {
  const { options } = props
  const core = new KvistCore(options.command)
  const [state, setState] = createSignal<State>(initialState(initialEffort(), false))
  const [manifest, setManifest] = createSignal<Manifest | null>(null)
  const [history, setHistory] = createSignal<string[]>([])
  const [historyRead, setHistoryRead] = createSignal(false)
  let waiting: { name: string; resolve: (result: CommandResult) => void } | null = null

  const dispatch = (action: Action) => setState((previous) => reduce(previous, action))
  const note = (text: string, tone: Tone = "info") => dispatch({ type: "note", tone, text })

  // Events that arrive together (one stdout chunk holds many lines) become one update.
  let queued: CoreEvent[] = []
  let flushing = false
  const flush = () => {
    flushing = false
    const events = queued
    queued = []
    batch(() => {
      for (const event of events) apply(event)
    })
  }

  const apply = (event: CoreEvent) => {
    const type = (event as { type: string }).type
    if (type === "manifest") return setManifest(event as unknown as Manifest)
    dispatch({ type: "event", event })
    if (event.type === "hello" && options.core.resume !== undefined) dispatch({ type: "resume_start" })
    if (event.type === "command_result") settle(event)
  }

  const settle = (event: Extract<CoreEvent, { type: "command_result" }>) => {
    if (event.ok && event.effects?.changed === true && event.name !== "new" && typeof event.effects.session === "string") {
      void redraw(event.effects.session, event.message)
    }
    if (waiting?.name !== event.name) return
    const done = waiting
    waiting = null
    done.resolve({ name: event.name, ok: event.ok, message: event.message, effects: (event.effects ?? {}) as Record<string, unknown> })
  }

  async function redraw(name: string, message = ""): Promise<void> {
    const feed = await sessionHistory(options.command, options.core.workspace, name)
    if (!feed.ok || feed.events.length === 0 || state().running) return
    dispatch({ type: "restore", events: feed.events, banner: true })
    if (message) dispatch({ type: "note", tone: "info", text: englishOnly(message) })
  }

  const busy = () => state().running || state().command !== null

  const run = (name: string, args: string[] = []): Promise<CommandResult> => {
    if (busy()) return Promise.reject(new Error("Wait for the current run to finish."))
    return new Promise((resolve, reject) => {
      try {
        core.sendCommand(name, args)
      } catch (error) {
        return reject(error)
      }
      // `resume` is a turn, not a wizard: the core answers it with events and `turn_end`.
      dispatch(name === "resume" ? { type: "resume_start" } : { type: "command_start", name })
      waiting = { name, resolve }
    })
  }

  const runQuietly = (name: string, args: string[]) => {
    run(name, args).catch((error: Error) => note(error.message, "warn"))
  }

  const local: Kvist["local"] = { handler: () => false }

  const slash = (text: string) => {
    const parsed = parseSlash(text)
    if (!parsed) return
    const { name, args } = parsed
    if (local.handler(name, args)) return
    switch (routeOf(name)) {
      case "core":
        return runQuietly(name, name === "session" ? sessionArgs(args) : name === "new" ? args.slice(0, 1) : args)
      case "classic":
        return note(CLASSIC_NOTE, "warn")
      default:
        return note(`Unknown command /${name}.`, "warn")
    }
  }

  const submit = (raw: string) => {
    const text = raw.trim()
    if (!text) return
    setHistory((old) => [...old, text])
    void addInputHistory(options.command, options.core.workspace, text)
    if (text.startsWith("/")) return slash(text)
    try {
      core.send(text)
      dispatch({ type: "submit", text, request: text })
    } catch (error) {
      note(error instanceof Error ? error.message : String(error), "error")
    }
  }

  const answer = (request: InputRequest, value: boolean | string | null, remember = false) => {
    core.respond(request.request_id, value, remember)
    dispatch({ type: "answered" })
  }

  const quit = async (code = 0) => {
    await core.stop()
    props.onQuit(code)
  }

  onMount(() => {
    void inputHistory(options.command, options.core.workspace)
      .then((entries) => setHistory((typed) => [...entries, ...typed]))
      .finally(() => setHistoryRead(true))
    core.on("event", (event: CoreEvent) => {
      queued.push(event)
      if (flushing) return
      flushing = true
      queueMicrotask(flush)
    })
    core.on("diagnostic", (text: string) => dispatch({ type: "event", event: { type: "debug", message: text.trim() } }))
    core.on("fault", (error: Error) => note(error.message, "error"))
    core.on("exit", ({ code, expected }: { code: number | null; expected: boolean }) => dispatch({ type: "core_exit", expected, code }))
    core.start(options.core)
  })

  onCleanup(() => void core.stop())

  const value: Kvist = {
    state,
    manifest,
    history,
    historyRead,
    workspace: options.core.workspace,
    frontend: options.frontend,
    command: options.command,
    busy,
    submit,
    run,
    slash,
    answer,
    cancel: () => {
      dispatch({ type: "interrupt" })
      core.cancel()
    },
    pause: () => core.pause(),
    note,
    dispatch,
    redraw: (name) => redraw(name),
    quit,
    local,
  }
  return <context.Provider value={value}>{props.children}</context.Provider>
}
