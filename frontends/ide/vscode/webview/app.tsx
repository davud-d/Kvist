import { readImages, imageGoal, MAX_IMAGES, type ImageAttachment } from "../../../gui/electron/renderer/image-attachments.js"
import { useEffect, useMemo, useReducer, useRef, useState } from "preact/hooks"

import { COMMANDS, parseSlash, routeOf, sessionArgs, suggest } from "../../../tui/ink/commands"
import { contextLabel, formatSeconds, formatTokens } from "../../../tui/ink/format"
import { actionTarget, exploreSummary, initialState, lastInflight, reduce, requestIsRussian } from "../../../tui/ink/state"
import type { ConfigInfo, ProviderInfo, SessionInfo, ToWebview } from "../src/messages"
import { Dialog, type Answer } from "./dialog"
import { FirstRun, type ModelListState, type RegistrationState } from "./firstRun"
import { CursorGlow } from "./glow"
import { Icon, Ring } from "./icons"
import { JobStrip } from "./jobs"
import { opensNewSession } from "./closed"
import { onMessage, post } from "./host"
import { Markdown } from "./markdown"
import { NO_METRICS, shortSeconds, trackMetrics, type TurnMetrics } from "./metrics"
import { RecentSessions } from "./sessions"
import { Settings } from "./settings"
import { ShellCard, Thinking, TranscriptItem, Working } from "./transcript"
import { verbOf } from "./verbs"

// The terminal-only commands make no sense inside an editor panel.
const PANEL_COMMANDS = COMMANDS.filter((command) => command.name !== "tui" && command.name !== "quit")
const logoUrl = document.getElementById("root")?.dataset.logo ?? ""
const STARTERS = ["Explain this project", "Review my uncommitted changes", "Find and fix failing tests"]
const HELP = PANEL_COMMANDS.map((command) => `/${command.name} — ${command.hint}`).join("\n")

export function App() {
  const [state, dispatch] = useReducer(reduce, undefined, () => initialState())
  const [view, setView] = useState<"chat" | "settings">("chat")
  const [config, setConfig] = useState<ConfigInfo | null>(null)
  const [sessions, setSessions] = useState<SessionInfo | null>(null)
  const [helloEffort, setHelloEffort] = useState("")
  const [text, setText] = useState("")
  const [selected, setSelected] = useState(0)
  const [, setTick] = useState(0)
  const [metrics, setMetrics] = useState<TurnMetrics>(NO_METRICS)
  const [attachments, setAttachments] = useState<string[]>([])
  const [images, setImages] = useState<ImageAttachment[]>([])
  const [loadingImages, setLoadingImages] = useState(false)
  const imagePicker = useRef<HTMLInputElement>(null)
  const [liveOutput, setLiveOutput] = useState<{ step: number; text: string } | null>(null)
  const [environment, setEnvironment] = useState<Record<string, string> | null>(null)
  // null: unknown yet or already set up. An array: the panel has never run a turn because
  // no provider has usable credentials, so it shows the setup screen instead of the chat.
  const [needsSetup, setNeedsSetup] = useState<ProviderInfo[] | null>(null)
  const [setupBusy, setSetupBusy] = useState(false)
  const [setupError, setSetupError] = useState("")
  const [modelList, setModelList] = useState<ModelListState>({ provider: "", busy: false, options: [], error: "" })
  const [registration, setRegistration] = useState<RegistrationState | null>(null)
  // When each shell command started, by step: the light around it begins after a couple of seconds.
  const shellStarts = useRef(new Map<number, number>())
  const bottom = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const callStart = useRef(Date.now())

  const busy = state.running || state.command !== null
  const runCommandRef = useRef<(name: string, args?: string[]) => void>(() => {})

  function runCommand(name: string, args: string[] = []): void {
    if (name === "resume") beginTurn()
    dispatch(name === "resume" ? { type: "resume_start" } : { type: "command_start", name })
    post({ type: "command", name, args: name === "session" ? sessionArgs(args) : args })
    setView("chat")
  }
  runCommandRef.current = runCommand

  function applyEnvironment(next: Record<string, string>): void {
    post({ type: "setEnvironment", env: next })
    dispatch({ type: "note", tone: "info", text: "Environment saved. Restarting the Kvist core…" })
  }

  function beginTurn(): void {
    setMetrics(NO_METRICS)
    callStart.current = Date.now()
  }

  function submitProvider(provider: string, apiKey: string, model: string): void {
    setSetupBusy(true)
    setSetupError("")
    post({ type: "setProvider", provider, apiKey, model })
  }

  function listModels(provider: string, apiKey: string): void {
    setModelList({ provider, busy: true, options: [], error: "" })
    post({ type: "listModels", provider, apiKey })
  }

  function openRegistration(provider: string): void {
    post({ type: "openRegistration", provider })
  }

  useEffect(() => {
    function handle(message: ToWebview): void {
      switch (message.type) {
        case "event": {
          const core = message.event
          const now = Date.now()
          setMetrics((current) => trackMetrics(current, core, (now - callStart.current) / 1000))
          if (core.type === "usage" || core.type === "action_result") callStart.current = now
          if (core.type === "step" && core.action_type === "run_shell") shellStarts.current.set(core.step, now)
          if (core.type === "action_output") setLiveOutput({ step: core.step, text: core.output })
          if (core.type === "action_result") setLiveOutput(null)
          if (core.type === "hello") setHelloEffort(core.effort ?? "")
          let freshSession = false
          if (core.type === "command_result") {
            const effects = core.effects ?? {}
            freshSession = core.ok && (core.name === "new" || core.name === "session") && effects.changed !== false && !effects.cancelled
            if (freshSession) post({ type: "getSessions" })
            // Switching to an existing session redraws it; a new one has nothing to show.
            if (freshSession && core.name === "session" && typeof effects.session === "string") post({ type: "getHistory", name: effects.session })
            if (core.ok && core.name === "provider") post({ type: "getConfig" })
          }
          if (core.type === "turn_end") post({ type: "getSessions" })
          // Background generation lands after that turn's own refresh above, so the
          // sidebar needs its own trigger or a late title never reaches it.
          if (core.type === "session_title") post({ type: "getSessions" })
          dispatch({ type: "event", event: core })
          // Cleared after the event: the reducer turns the core's "session started" message into a
          // note, and a note left behind would keep the welcome screen from showing.
          if (freshSession) dispatch({ type: "clear" })
          return
        }
        case "exit":
          return dispatch({ type: "core_exit", expected: message.expected, code: message.code })
        case "diagnostic":
          return dispatch({ type: "note", tone: "debug", text: message.text.trim() })
        case "fault":
          return dispatch({ type: "note", tone: "error", text: message.message })
        case "attached":
          return setAttachments((current) => [...new Set([...current, ...message.paths])])
        case "environment":
          return setEnvironment(message.env)
        case "history":
          setImages([])
          setAttachments([])
          // A page that starts mid-way says so at the top of the feed.
          return dispatch({ type: "restore", events: message.hasMore ? [{ type: "journal_notice" }, ...message.events] : message.events })
        case "config":
          return setConfig(message.info)
        case "sessions":
          return setSessions(message.info)
        case "invoke":
          return runCommandRef.current(message.name, message.args)
        case "showSettings":
          return setView("settings")
        case "needsSetup":
          setSetupBusy(false)
          return setNeedsSetup(message.providers)
        case "setupResult":
          setSetupBusy(false)
          if (!message.ok) return setSetupError(message.error || "Could not save the provider.")
          setSetupError("")
          return setNeedsSetup(null)
        case "modelList":
          return setModelList({ provider: message.provider, busy: false, options: message.models, error: message.ok ? message.error ?? "" : message.error || "Could not load models." })
        case "registrationOpened":
          return setRegistration({ provider: message.provider, opened: message.opened, url: message.url })
      }
    }
    const stop = onMessage(handle)
    post({ type: "ready" })
    post({ type: "getConfig" })
    post({ type: "getSessions" })
    return stop
  }, [])

  useEffect(() => {
    if (!state.running) return
    const timer = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [state.running])

  useEffect(() => {
    const field = input.current
    if (!field) return
    field.style.height = "auto"
    field.style.height = `${Math.min(field.scrollHeight, 200)}px`
  }, [text])

  useEffect(() => bottom.current?.scrollIntoView({ block: "end" }), [state.items.length, state.live?.answer, state.live?.reasoning, state.pending])

  // A closed session has no composer to type into; Enter anywhere takes the New session path.
  useEffect(() => {
    if (state.closed === null || busy || state.pending) return
    function onKey(event: KeyboardEvent): void {
      const targetTag = event.target instanceof Element ? event.target.tagName : ""
      if (!opensNewSession({ key: event.key, shiftKey: event.shiftKey, ctrlKey: event.ctrlKey, altKey: event.altKey, metaKey: event.metaKey, targetTag })) return
      event.preventDefault()
      runCommandRef.current("new")
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [state.closed, busy, state.pending])

  async function attachImages(files: FileList | File[]): Promise<void> {
    if (busy || loadingImages) return
    setLoadingImages(true)
    try {
      const added = await readImages(files)
      if (images.length + added.length > MAX_IMAGES) throw new Error("Up to 8 images / Не больше 8 изображений")
      if ([...images, ...added].reduce((size, item) => size + item.data_url.length, 0) > 28 * 1024 * 1024) throw new Error("Images exceed 20 MiB / Изображения больше 20 МиБ")
      setImages(previous => [...previous, ...added])
    } catch (error) {
      dispatch({ type: "note", tone: "error", text: String(error) })
    } finally {
      setLoadingImages(false)
    }
  }

  function submit(): void {
    const value = text.trim()
    if ((!value && !images.length) || busy || loadingImages) return
    setText("")
    const slash = parseSlash(value)
    if (!slash) {
      // Names only: the agent reads a file itself when it needs the content.
      const files = attachments
      const line = files.length ? `[Attached files, contents not included, read them if needed: ${files.join(", ")}] ${value}` : value
      beginTurn()
      dispatch({ type: "submit", text: (files.length ? `${value}\n[${files.join(", ")}]` : value) + images.map((_, index) => ` [Image #${index + 1}]`).join("") })
      post({ type: "submit", text: imageGoal(line, images) })
      setImages([])
      setAttachments([])
      return
    }
    const route = routeOf(slash.name)
    if (route === "core") return runCommand(slash.name, slash.args)
    if (slash.name === "help") return dispatch({ type: "note", tone: "info", text: HELP })
    if (slash.name === "clear") return dispatch({ type: "clear" })
    if (slash.name === "debug") return dispatch({ type: "toggle_debug" })
    dispatch({ type: "note", tone: "error", text: route ? `/${slash.name} is not available in this panel.` : `Unknown command: /${slash.name}` })
  }

  const suggestions = suggest(text, PANEL_COMMANDS)

  function onKeyDown(event: KeyboardEvent): void {
    if (suggestions.length > 0 && (event.key === "Tab" || event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault()
      if (event.key === "Tab") return setText(`/${suggestions[selected]?.name ?? suggestions[0].name} `)
      return setSelected((selected + (event.key === "ArrowDown" ? 1 : suggestions.length - 1)) % suggestions.length)
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      const pick = suggestions[selected]
      if (pick?.needsArgument) return setText(`/${pick.name} `)
      if (pick && text !== `/${pick.name}`) setText(`/${pick.name}`)
      submit()
    }
  }

  const answer: Answer = (value, remember = false) => {
    if (!state.pending) return
    post({ type: "respond", requestId: state.pending.request_id, answer: value, remember })
    dispatch({ type: "answered" })
  }

  const effort = state.effort || helloEffort || "medium"
  const active = sessions?.active_session ?? state.info.session
  const items = sessions?.session_items ?? []
  const recent = items.filter((item) => item.name !== active)
  // The core's hello leaves an invisible banner item, so the chat counts as empty with only that.
  const isEmpty = !state.live && state.items.every((item) => item.kind === "banner")
  const usage = contextLabel(state.usage.context, state.usage.limit)
  const contextShare = state.usage.limit > 0 ? state.usage.context / state.usage.limit : 0
  const elapsed = state.startedAt ? formatSeconds((Date.now() - state.startedAt) / 1000) : ""
  const thinking = state.live?.thinking === true
  const lightState = state.running ? (thinking ? "thinking" : "working") : ""

  // The running command and its finished card share one key, so the element (and the
  // light circling it) survives the moment the result arrives.
  const shellKey = (step: number) => `shell-${state.epoch}-${step}`
  const startOf = (step: number) => shellStarts.current.get(step) ?? 0
  const running = lastInflight(state)
  // Typing and the elapsed timer do not change the transcript or its DOM selection.
  const feed = useMemo(() => {
    const cards = state.items
      .filter((item) => item.kind !== "note" || item.tone !== "debug" || state.showDebug)
      .map((item) =>
        item.kind === "tool" && item.action === "run_shell"
          ? <ShellCard key={shellKey(item.step)} args={item.args} result={item.result} startedAt={startOf(item.step)} live="" />
          : <TranscriptItem key={`${state.epoch}-${item.id}`} uid={`${state.epoch}-${item.id}`} settled={item.id <= state.settledThrough} item={item} items={state.items} />)
    if (running?.action === "run_shell") {
      const { step, args } = running
      cards.push(<ShellCard key={shellKey(step)} args={args} result={null} startedAt={startOf(step)} live={liveOutput?.step === step ? liveOutput.text : ""} />)
    }
    return cards
  }, [state.items, state.epoch, state.settledThrough, state.showDebug, running, liveOutput])

  if (needsSetup) {
    return (
      <>
        <CursorGlow />
        <FirstRun
          logoUrl={logoUrl}
          providers={needsSetup}
          busy={setupBusy}
          error={setupError}
          submit={submitProvider}
          modelList={modelList}
          onListModels={listModels}
          registration={registration}
          onOpenRegistration={openRegistration}
        />
      </>
    )
  }

  if (view === "settings") {
    return (
      <>
        <CursorGlow />
        <Settings
          config={config}
          provider={state.info.provider}
          role={state.info.role || "auto"}
          effort={effort}
          environment={environment}
          busy={busy}
          run={runCommand}
          applyEnvironment={applyEnvironment}
          close={() => setView("chat")}
        />
      </>
    )
  }

  return (
    <div class="app">
      <CursorGlow />
      <header class="header">
        <label class="session-picker" title="Switch session">
          <span class="session-current">{isEmpty ? "New session" : (items.find((item) => item.name === active)?.title ?? active)}</span>
          <Icon name="chevron" size={14} />
          <select
            value={active}
            disabled={busy}
            onFocus={() => post({ type: "getSessions" })}
            onChange={(event) => runCommand("session", [(event.target as HTMLSelectElement).value])}
          >
            {(items.length ? items : [{ name: active, title: active, updated_at: 0 }]).map((item) => (
              <option key={item.name} value={item.name}>{item.title}</option>
            ))}
          </select>
        </label>
        <button class="icon" title="New session" disabled={busy} onClick={() => runCommand("new")}><Icon name="add" /></button>
        <button class="icon" title="Settings" onClick={() => setView("settings")}><Icon name="settings" /></button>
      </header>

      <main class="transcript">
        {isEmpty ? (
          <>
            <RecentSessions items={recent} disabled={busy} open={(name) => runCommand("session", [name])} />
            <div class="welcome">
              <div class="logo-wrap" style={{ "--logo": `url(${logoUrl})` }}>
                <img class="logo" src={logoUrl} alt="Kvist" />
                <div class="logo-sheen" />
              </div>
              <div class="welcome-title">What should Kvist work on?</div>
              <div class="starters">
                {STARTERS.map((starter) => (
                  <button key={starter} class="starter" disabled={busy} onClick={() => { setText(starter); input.current?.focus() }}>{starter}</button>
                ))}
              </div>
            </div>
          </>
        ) : null}
        {feed}
        {state.exploring ? <Working label="Exploring" target={exploreSummary(state.exploring)} /> : null}
        {running && running.action !== "run_shell" ? <Working label={verbOf(running.action)} target={actionTarget(running.args)} /> : null}
        {thinking ? <Thinking reasoning={state.live?.reasoning ?? ""} since={state.live?.thinkingSince ?? null} /> : null}
        {state.live?.answer ? <Markdown text={state.live.answer} /> : null}
        {state.pending ? <Dialog key={state.pending.request_id} request={state.pending} answer={answer} /> : null}
        <div ref={bottom} />
      </main>

      <footer class="footer">
        {suggestions.length > 0 ? (
          <div class="suggestions">
            {suggestions.map((command, i) => (
              <div key={command.name} class={`suggestion ${i === selected ? "selected" : ""}`} onClick={() => setText(`/${command.name} `)}>
                <span class="suggestion-name">/{command.name}</span>
                <span class="hint">{command.hint}</span>
              </div>
            ))}
          </div>
        ) : null}
        <JobStrip jobs={state.jobs} russian={requestIsRussian(state)} now={Date.now()} />
        {state.running || metrics.thinking > 0 || metrics.tokensPerSecond ? (
          <div class="strip">
            {state.running ? <span class="shimmer strip-status">{state.status?.text ?? "Working"}</span> : null}
            {state.running ? <span>{elapsed}</span> : null}
            {state.running && state.status?.step ? <span>Step {state.status.step}</span> : null}
            {metrics.thinking > 0 ? <span>Thought for {shortSeconds(metrics.thinking)}</span> : null}
            {metrics.tokensPerSecond ? <span title="Estimated from output tokens and call time">≈{Math.round(metrics.tokensPerSecond)} tokens/s</span> : null}
          </div>
        ) : null}
        {state.closed !== null ? (
          // The core's notice where the composer was, as tall as it: a calm stop, not an error.
          <div class="composer closed-bar" role="status">
            <div class="closed-notice">{state.closed}</div>
            <div class="toolbar" aria-hidden="true" />
          </div>
        ) : (
        <div class={`composer lit ${lightState}`}
          onDragOver={event => event.preventDefault()}
          onDrop={event => { event.preventDefault(); void attachImages(Array.from(event.dataTransfer?.files || [])) }}>
          <input ref={imagePicker} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp,image/tiff" multiple hidden
            onChange={event => { void attachImages(Array.from(event.currentTarget.files || [])); event.currentTarget.value = "" }} />
          {images.length ? <div class="image-attachments" aria-label="Attached images">
            {images.map((image, index) => <div class="image-attachment" key={index} title={image.name}>
              <img src={image.data_url} alt={`Image #${index + 1}: ${image.name}`} />
              <button class="image-remove" title="Remove image / Удалить изображение" onClick={() => setImages(previous => previous.filter(item => item !== image))}>×</button>
            </div>)}
          </div> : null}
          {attachments.length > 0 ? (
            <div class="chips">
              {attachments.map((file) => (
                <span key={file} class="chip" title={file}>
                  {file.split("/").at(-1)}
                  <button class="chip-remove" title="Remove" onClick={() => setAttachments(attachments.filter((item) => item !== file))}><Icon name="cross" size={12} /></button>
                </span>
              ))}
            </div>
          ) : null}
          <textarea
            ref={input}
            rows={1}
            disabled={busy || state.pending !== null}
            placeholder={busy ? "Working…" : "Ask Kvist, or type / for commands"}
            value={text}
            onInput={(event) => {
              setText((event.target as HTMLTextAreaElement).value)
              setSelected(0)
            }}
            onPaste={event => {
              const files = Array.from(event.clipboardData?.files || []).filter(file => file.type.startsWith("image/"))
              if (files.length) { event.preventDefault(); void attachImages(files) }
            }}
            onKeyDown={onKeyDown}
          />
          <div class="toolbar">
            <button class="icon" title="Attach images / Прикрепить изображения" disabled={busy || loadingImages} onClick={() => imagePicker.current?.click()}>▧</button>
            <button class="icon" title="Attach files by name" disabled={busy} onClick={() => post({ type: "pickFiles" })}><Icon name="add" /></button>
            <button class="pill" title="Change models" disabled={busy} onClick={() => runCommand("models")}>{state.info.model || "Model"}</button>
            <button class="pill" title="Reasoning effort" disabled={busy} onClick={() => runCommand("effort")}>{effort}</button>
            <span class="spacer" />
            {usage ? (
              <span class="ring" title={`Context ${usage}, sent ${formatTokens(state.usage.input)}, received ${formatTokens(state.usage.output)}`}>
                <Ring value={contextShare} />
              </span>
            ) : null}
            {state.running ? (
              <button class="send" title="Stop" onClick={() => post({ type: "cancel" })}><Icon name="stop" /></button>
            ) : (
              <button class="send" title="Send" disabled={busy || loadingImages || (!text.trim() && !images.length)} onClick={submit}><Icon name="arrowUp" /></button>
            )}
          </div>
        </div>
        )}
      </footer>
    </div>
  )
}
