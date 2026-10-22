// The provider stack of OpenCode's TUI (packages/tui/src/app.tsx) with Kvist's core where its
// server and SDK are, the keyboard layers for the commands, and the screens.

import { RGBA, type ScrollBoxRenderable } from "@opentui/core"
import { useKeyboard, useRenderer, useTerminalDimensions } from "@opentui/solid"
import os from "node:os"
import path from "node:path"
import { ErrorBoundary, createEffect, createMemo, createSignal, on, onCleanup, Show } from "solid-js"

import { subjectOf } from "../../ink/approval"
import { englishOnly } from "../../ink/format"
import type { InputRequest } from "../../ink/protocol"
import { createTuiClipboard } from "./oc/tui/clipboard"
import { ConfigProvider, useConfig } from "./oc/tui/config"
import { ClipboardProvider, useClipboard } from "./oc/tui/context/clipboard"
import { Keymap } from "./oc/tui/context/keymap"
import { ThemeProvider, createThemeSource, useTheme, useThemes } from "./oc/tui/context/theme"
import { DialogProvider, useDialog, type DialogContext } from "./oc/tui/ui/dialog"
import { Selection } from "./oc/tui/util/selection"
import { DialogConfirm } from "./oc/tui/ui/dialog-confirm"
import { DialogPrompt } from "./oc/tui/ui/dialog-prompt"
import { DialogSelect } from "./oc/tui/ui/dialog-select"
import { Toast, ToastProvider, useToast } from "./oc/tui/ui/toast"
import { createCommands } from "./commands"
import { KvistProvider, useKvist, type LaunchOptions } from "./kvist"
import { ApprovalPrompt } from "./screens/approval"
import { ClosedBar } from "./screens/closed"
import { Home } from "./screens/home"
import type { PromptRef } from "./screens/prompt"
import { Session } from "./screens/session"

const ESC_CONFIRM_MS = 3000
const EXIT_CONFIRM_MS = 2000

export interface AppProps {
  options: LaunchOptions
  terminalMode: "dark" | "light"
  onQuit(code: number): void
  // Told the session that is on screen, so the launcher can point back to it on exit.
  onSession?(name: string): void
}

// The folders whose `themes/` subfolder holds themes, lowest priority first: the core's own, then OpenCode's.
function themeDirectories(workspace: string): string[] {
  const configDir = process.env.KVIST_CONFIG_DIR || path.join(os.homedir(), ".kvist")
  return [
    path.join(import.meta.dirname, "..", "..", "..", "..", "kernel"),
    path.join(os.homedir(), ".config", "opencode"),
    configDir,
    path.join(workspace, ".kvist"),
  ]
}

export function App(props: AppProps) {
  const renderer = useRenderer()
  const clipboard = createTuiClipboard(renderer)
  onCleanup(() => void clipboard.dispose())
  const source = createThemeSource(themeDirectories(props.options.core.workspace))
  return (
    <ConfigProvider>
      <Keymap.Provider>
        <ClipboardProvider value={clipboard}>
          <ToastProvider>
            <ThemeProvider mode={props.terminalMode} source={source}>
              <ErrorBoundary fallback={(error: unknown) => <Crash error={error} onQuit={() => props.onQuit(1)} />}>
                <KvistProvider options={props.options} onQuit={props.onQuit}>
                  <DialogProvider>
                    <Shell onSession={props.onSession} copy={(text) => clipboard.write(text)} />
                  </DialogProvider>
                </KvistProvider>
              </ErrorBoundary>
            </ThemeProvider>
          </ToastProvider>
        </ClipboardProvider>
      </Keymap.Provider>
    </ConfigProvider>
  )
}

// What a person sees when a component throws: the message, and the way out. The core is stopped
// by the KvistProvider that was torn down.
function Crash(props: { error: unknown; onQuit(): void }) {
  const theme = useTheme()
  useKeyboard((event) => {
    if ((event.ctrl && event.name === "c") || event.name === "q") props.onQuit()
  })
  const message = props.error instanceof Error ? `${props.error.name}: ${props.error.message}` : String(props.error)
  return (
    <box flexDirection="column" width="100%" height="100%" padding={2} gap={1}>
      <text fg={theme.text.feedback.error.base}>Kvist's interface stopped: something in it failed.</text>
      <text fg={theme.text.base} wrapMode="word">{message}</text>
      <text fg={theme.text.muted}>Press q or ctrl+c to leave. Your sessions are kept; start Kvist again to continue.</text>
    </box>
  )
}

// The question a core command asks, drawn as the dialog OpenCode would use for it.
function showRequest(request: InputRequest, dialog: DialogContext, answer: (value: boolean | string | null) => void) {
  let done = false
  const finish = (value: boolean | string | null) => {
    if (done) return
    done = true
    answer(value)
    dialog.clear()
  }
  const text = (value: string) => englishOnly(value).trim()
  const cancelled = request.kind === "confirm" ? false : null
  const render = () => {
    if (request.kind === "choice") {
      return (
        <DialogSelect
          title={text(request.title)}
          current={request.current_value}
          options={(request.options ?? []).map((option) => ({
            title: text(option.label),
            value: option.value,
            description: option.description ? text(option.description) : undefined,
          }))}
          onSelect={(option) => finish(option.value)}
          onCancel={() => finish(cancelled)}
        />
      )
    }
    if (request.kind === "confirm") {
      return (
        <DialogConfirm
          title={text(request.title)}
          message={text(request.body)}
          onConfirm={() => finish(true)}
          onCancel={() => finish(false)}
        />
      )
    }
    return (
      <DialogPrompt
        title={text(request.title)}
        description={request.body || request.help_text ? () => <text>{text(request.body || request.help_text)}</text> : undefined}
        value={request.kind === "secret" ? undefined : request.current_value}
        onConfirm={(value) => finish(value)}
        onCancel={() => finish(null)}
      />
    )
  }
  dialog.replace(render, () => finish(cancelled))
}

function Shell(props: { onSession?(name: string): void; copy(text: string): Promise<void> }) {
  const kvist = useKvist()
  const dialog = useDialog()
  const theme = useTheme()
  const themes = useThemes()
  const config = useConfig()
  const toast = useToast()
  const renderer = useRenderer()
  const clipboard = useClipboard()
  const [promptRef, setPromptRef] = createSignal<PromptRef | undefined>()
  const [stopArmedAt, setStopArmedAt] = createSignal(0)
  const [exitArmedAt, setExitArmedAt] = createSignal(0)
  let scroll: ScrollBoxRenderable | undefined

  const quit = (code = 0) => void kvist.quit(code)
  const state = kvist.state
  const pending = () => state().pending
  const approval = () => {
    const request = pending()
    return request && subjectOf(request) ? request : null
  }

  const dimensions = useTerminalDimensions()
  const commands = createCommands({ kvist, dialog, themes, config, toast, quit, copy: props.copy, columns: () => dimensions().width })
  const interrupt = () => {
    if (state().command) return
    if (state().running) return kvist.cancel()
    if (exitArmedAt() && Date.now() - exitArmedAt() < EXIT_CONFIRM_MS) return quit(0)
    setExitArmedAt(Date.now())
    toast.show({ message: "Press Ctrl+C again to exit", variant: "info", duration: EXIT_CONFIRM_MS })
  }
  const pause = () => {
    if (!state().running) return
    if (stopArmedAt() && Date.now() - stopArmedAt() < ESC_CONFIRM_MS) {
      setStopArmedAt(0)
      return kvist.pause()
    }
    setStopArmedAt(Date.now())
  }
  const scrollPage = (direction: 1 | -1) => scroll?.scrollBy(direction * Math.max(1, Math.floor((scroll.viewport.height || 10) * 0.8)))
  const idle = () => !dialog.stack.length && !approval()

  Keymap.createLayer(() => ({
    mode: "global",
    commands: [
      ...commands,
      { id: "prompt.clear", title: "Clear input field", bind: false, run: () => promptRef()?.set("") },
      { id: "kvist.interrupt", title: "Stop everything", bind: "ctrl+c,ctrl+d", run: interrupt },
      { id: "session.interrupt", title: "Interrupt current session", bind: false, run: pause },
      { id: "session.page.up", title: "Scroll messages up by one page", bind: false, run: () => scrollPage(-1) },
      { id: "session.page.down", title: "Scroll messages down by one page", bind: false, run: () => scrollPage(1) },
    ],
  }))
  Keymap.createLayer(() => ({
    enabled: idle,
    bindings: [
      "command.palette.show",
      "agent.cycle",
      "agent.list",
      "variant.cycle",
      "model.list",
      "session.new",
      "session.list",
      "session.rename",
      "session.undo",
      "session.sidebar.toggle",
      "messages.copy",
      "opencode.status",
      "help.show",
      "session.page.up",
      "session.page.down",
      "app.exit",
    ],
  }))
  Keymap.createLayer(() => ({ enabled: () => idle() && state().running, bindings: ["session.interrupt"] }))
  Keymap.createLayer(() => ({ enabled: () => idle() && (promptRef()?.text ?? "") !== "", bindings: ["prompt.clear"] }))
  Keymap.createLayer(() => ({ enabled: () => idle() && (promptRef()?.text ?? "") === "", bindings: ["kvist.interrupt"] }))

  const route = createMemo(() => (state().items.some((item) => item.kind !== "banner" && item.kind !== "note") ? "session" : "home"))
  let lastNote = 0

  // What a command or the core says is a toast, as in OpenCode; an error also stays on the page once there is one.
  createEffect(on(() => state().items.length, () => {
    const fresh = state().items.filter((item) => item.kind === "note" && item.id > lastNote)
    const last = state().items.at(-1)
    if (last) lastNote = Math.max(lastNote, last.id)
    for (const item of fresh) {
      if (item.kind !== "note" || item.tone === "debug") continue
      if (item.tone === "error" && route() !== "home") continue
      toast.show({ message: item.text, variant: item.tone === "error" ? "error" : item.tone === "warn" ? "warning" : "info", duration: item.tone === "info" ? 4000 : 8000 })
    }
  }))

  createEffect(on(() => pending()?.request_id, () => {
    const request = pending()
    if (!request || subjectOf(request)) return
    showRequest(request, dialog, (value) => kvist.answer(request, value))
  }))

  createEffect(() => {
    if (state().info.session) props.onSession?.(state().info.session)
  })

  const title = createMemo(() => state().info.title)
  createEffect(on(title, (value) => renderer.setTerminalTitle(value ? `Kvist | ${value}` : "Kvist"), { defer: true }))
  const offThemeError = themes.onError(({ name, error }) => toast.show({ message: `Theme '${name}': ${error.message}`, variant: "warning", duration: 8000 }))
  onCleanup(offThemeError)
  createEffect(() => renderer.setBackgroundColor(theme.background.base))
  createEffect(() => {
    renderer.useMouse = config.data.mouse
  })

  createEffect(() => {
    if (!stopArmedAt()) return
    const timer = setTimeout(() => setStopArmedAt(0), ESC_CONFIRM_MS)
    onCleanup(() => clearTimeout(timer))
  })
  createEffect(() => {
    if (!exitArmedAt()) return
    const timer = setTimeout(() => setExitArmedAt(0), EXIT_CONFIRM_MS)
    onCleanup(() => clearTimeout(timer))
  })

  // What replaces the prompt: an approval that waits, or the notice of a session that was closed.
  const approvalView = () => {
    const request = approval()
    if (request) return <ApprovalPrompt request={request} onDecide={(decision) => kvist.answer(request, decision !== "no", decision === "remember")} />
    const notice = state().closed
    if (notice === null) return undefined
    return <ClosedBar notice={notice} active={!state().running && !state().command && !dialog.stack.length} onNewSession={() => void kvist.run("new").catch((error: Error) => kvist.note(error.message, "warn"))} />
  }

  // Releasing a drag copies what it selected; ctrl+c with a selection clears it instead of stopping the turn.
  useKeyboard((event) => Selection.handleSelectionKey(renderer, toast, event, clipboard, true))

  return (
    <box
      flexDirection="column"
      width="100%"
      height="100%"
      backgroundColor={RGBA.fromInts(0, 0, 0, 0)}
      onMouseUp={(event: { isDragging?: boolean }) => Selection.copyOnSelectRelease(event, renderer, toast, clipboard)}
    >
      <Toast />
      <Show
        when={route() === "session"}
        fallback={<Home ref={setPromptRef} commands={commands} disabled={state().pending !== null && !approval()} approval={approvalView()} />}
      >
        <Session
          ref={setPromptRef}
          commands={commands}
          disabled={state().pending !== null && !approval()}
          approval={approvalView()}
          stopArmed={stopArmedAt() > 0}
          scrollRef={(element) => (scroll = element)}
        />
      </Show>
    </box>
  )
}
