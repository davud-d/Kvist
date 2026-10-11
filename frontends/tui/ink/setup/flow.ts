// The setup flow as a pure state machine: input and operation results go in, the next state and
// at most one thing to do come out. Drawing is scene.ts, running the operations is run.ts.
import { breadthOf, initialFolder, updateFolder, type Breadth, type FolderState, type FolderTarget, type Listing } from "./folders.js"
import type { InputEvent } from "./keys.js"
import type { ConfigInfo, InstallEvent, InstallReply, ListModelsReply, OpReply, PlanReply, ProviderInfo, RegistrationReply, SetProviderReply } from "./ops.js"

// "components" and "installing" come before "welcome" when the installer started this screen.
export type Step = "components" | "installing" | "welcome" | "broad" | "folder" | "provider" | "key" | "models" | "working" | "interface" | "starting"

// What the release installer can put on a machine, in the order it is offered. The first one is
// the program itself and cannot be unticked.
export const COMPONENT_IDS = ["cli", "node", "ffmpeg", "jetbrains", "vscode", "path", "python", "libpython", "binutils"] as const
export const REQUIRED_COMPONENT = "cli"
// System packages for building Kvist from source: they sit under one folding row and are the only
// items that need administrator rights.
export const BUILD_TOOLS: readonly string[] = ["python", "libpython", "binutils"]

export type ComponentStatus = "waiting" | "running" | "done" | "failed"

// How system packages can be installed: as root, with a sudo that asks nothing, with a sudo that
// asks for a password (the screen hands the terminal over for that), or not at all.
export type Privilege = "root" | "cached" | "password" | "none"

export interface Component {
  id: string
  available: boolean
  // Why an unavailable item is not offered: "present" (already there) or "unsupported".
  reason: string
  targets: string[]
  checked: boolean
}

export interface InstallState {
  plan: "loading" | "ready" | "failed"
  components: Component[]
  privilege: Privilege
  // The build tools are folded away until this is true.
  buildOpen: boolean
  // The highlighted row of the list (choiceRows), the folding row included.
  cursor: number
  // Progress of the run, by component id.
  status: Record<string, ComponentStatus>
  // The last step the installer reported, and what it warned about.
  message: string
  warnings: string[]
  error: string
}

// Providers a newcomer is offered, in order; the other 40+ are reached through "More providers".
export const CURATED_PROVIDERS = ["anymodel", "chatgpt", "openai", "anthropic", "deepseek", "openrouter", "freeflow"] as const
export const MORE_PROVIDERS = "__more__"
export const RECOMMENDED_PROVIDER = "anymodel"
// Only AnyModel has a signup page the core knows how to open.
export const REGISTERABLE_PROVIDER = "anymodel"

// Where a key comes from, for the providers that have a stable console address.
export const KEY_PAGES: Readonly<Record<string, string>> = {
  openai: "platform.openai.com/api-keys",
  anthropic: "console.anthropic.com/settings/keys",
  deepseek: "platform.deepseek.com/api_keys",
  openrouter: "openrouter.ai/keys",
}

// The interfaces the launcher can save, newcomers' favourite first (Claude Code's look is the
// one most people already know).
export const INTERFACES = ["claude", "tui", "terminal"] as const
export type InterfaceId = (typeof INTERFACES)[number]

// "done" ends a provider-only setup: the provider is saved and there is no interface to choose.
export type Outcome = { kind: "frontend"; value: InterfaceId } | { kind: "classic" } | { kind: "quit" } | { kind: "done" }

export type Effect =
  | { type: "plan" }
  | { type: "elevate" }
  | { type: "install"; only: string[] }
  | { type: "listModels"; provider: string; key: string }
  | { type: "connect"; provider: string; key: string; model: string }
  | { type: "register"; provider: string }
  | { type: "cancel" }
  | { type: "list"; path: string }
  | { type: "make"; parent: string; name: string }
  | { type: "finish"; outcome: Outcome }

export type Target =
  | { kind: "option"; index: number }
  | { kind: "button"; id: "primary" | "back" | "quit" | "register" | "folder" | "anyway" }
  | { kind: "folder"; target: FolderTarget }

export type Action =
  | { type: "input"; event: InputEvent }
  | { type: "click"; target: Target }
  | { type: "wheel"; delta: number }
  | { type: "catalog"; reply: OpReply<ConfigInfo> }
  | { type: "models"; reply: OpReply<ListModelsReply> }
  | { type: "connected"; reply: OpReply<SetProviderReply> }
  | { type: "registered"; reply: OpReply<RegistrationReply> }
  | { type: "listing"; listing: Listing }
  | { type: "made"; path: string; error?: string }
  | { type: "plan"; reply: OpReply<PlanReply> }
  | { type: "installEvent"; event: InstallEvent }
  | { type: "elevated"; error: string }
  | { type: "installed"; reply: InstallReply }

export interface SetupState {
  step: Step
  // The installation that precedes the setup, or null when the screen starts at "welcome".
  install: InstallState | null
  // "provider" is for a caller that only wants a provider configured (the ACP login): no working
  // folder, no interface choice.
  scope: "full" | "provider"
  // The folder the agent will work in, and the person's home folder (for the chooser's "~").
  workspace: string
  home: string
  // The machine has drive letters (Windows): the chooser can go above a drive's root.
  drives: boolean
  folder: FolderState | null
  // The wide folder the person has been told about and kept: asked about once.
  broadAccepted: string
  catalog: "loading" | "ready" | "failed"
  providers: ProviderInfo[]
  providerIndex: number
  provider: ProviderInfo | null
  key: string
  cursor: number
  registration: RegistrationReply | null
  models: { status: "loading" | "ready" | "failed"; all: string[] }
  filter: string
  filterCursor: number
  modelIndex: number
  // Where a failed connection returns to.
  returnStep: Step
  // The core's words about the last failure, shown under the field it concerns, and whether it
  // came from the person's input (no key yet) or from the connection (the screen adds the next step).
  error: string
  errorKind: "input" | "connect"
  // The key is shown in clear text, so a paste can be checked.
  reveal: boolean
  // The provider that has just connected, for the confirmation on the last screen.
  connected: string
  interfaceIndex: number
}

export function initialState(workspace = "", home = "", drives = false, scope: "full" | "provider" = "full", withInstall = false): SetupState {
  return {
    step: withInstall ? "components" : "welcome",
    install: withInstall ? { plan: "loading", components: [], privilege: "none", buildOpen: false, cursor: 0, status: {}, message: "", warnings: [], error: "" } : null,
    scope,
    workspace,
    home,
    drives,
    folder: null,
    broadAccepted: "",
    catalog: "loading",
    providers: [],
    providerIndex: 0,
    provider: null,
    key: "",
    cursor: 0,
    registration: null,
    models: { status: "loading", all: [] },
    filter: "",
    filterCursor: 0,
    modelIndex: 0,
    returnStep: "provider",
    error: "",
    errorKind: "input",
    reveal: false,
    connected: "",
    interfaceIndex: 0,
  }
}

export interface Update {
  state: SetupState
  effect?: Effect
}

// What the provider list shows: the curated providers the core knows, then "More providers".
export function offeredProviders(state: SetupState): ProviderInfo[] {
  return CURATED_PROVIDERS.flatMap((id) => state.providers.find((provider) => provider.id === id) ?? [])
}

export function providerOptionCount(state: SetupState): number {
  return offeredProviders(state).length + 1
}

export function filteredModels(state: SetupState): string[] {
  const needle = state.filter.trim().toLowerCase()
  return needle ? state.models.all.filter((id) => id.toLowerCase().includes(needle)) : state.models.all
}

function wrap(index: number, delta: number, count: number): number {
  return count === 0 ? 0 : (index + delta + count) % count
}

interface Edited {
  text: string
  cursor: number
}

// Line editing shared by the key field and the model filter. Returns null for a key it does not own.
function edit(text: string, cursor: number, event: InputEvent): Edited | null {
  if (event.type === "paste") {
    const unquote = (value: string) => value.replace(/^["'`]+|["'`]+$/g, "").trim()
    const pasted = unquote(unquote(event.text.replace(/[\r\n\t]+/g, "")).replace(/^bearer\s+/i, ""))
    return { text: text.slice(0, cursor) + pasted + text.slice(cursor), cursor: cursor + pasted.length }
  }
  if (event.type !== "key") return null
  if (event.ctrl) {
    if (event.text === "u") return { text: "", cursor: 0 }
    if (event.text === "a") return { text, cursor: 0 }
    if (event.text === "e") return { text, cursor: text.length }
    return null
  }
  switch (event.key) {
    case "char":
      return { text: text.slice(0, cursor) + event.text + text.slice(cursor), cursor: cursor + event.text.length }
    case "backspace":
      return cursor === 0 ? { text, cursor } : { text: text.slice(0, cursor - 1) + text.slice(cursor), cursor: cursor - 1 }
    case "delete":
      return { text: text.slice(0, cursor) + text.slice(cursor + 1), cursor }
    case "left":
      return { text, cursor: Math.max(0, cursor - 1) }
    case "right":
      return { text, cursor: Math.min(text.length, cursor + 1) }
    case "home":
      return { text, cursor: 0 }
    case "end":
      return { text, cursor: text.length }
    default:
      return null
  }
}

function toWorking(state: SetupState, effect: Effect, returnStep: Step): Update {
  return { state: { ...state, step: "working", returnStep, error: "" }, effect }
}

function chooseProvider(state: SetupState, index: number): Update {
  const offered = offeredProviders(state)
  const provider = offered[index]
  if (!provider) return { state, effect: { type: "finish", outcome: { kind: "classic" } } }
  const next = { ...state, providerIndex: index, provider, key: "", cursor: 0, registration: null, error: "" }
  // An OAuth provider has no key to paste, and one whose key is already saved needs none.
  if (provider.auth_type === "oauth" || provider.has_credentials) {
    return toWorking(next, { type: "connect", provider: provider.id, key: "", model: "" }, "provider")
  }
  return { state: { ...next, step: "key" } }
}

function submitKey(state: SetupState, strings: { keyEmpty: string }): Update {
  const provider = state.provider
  if (!provider) return { state }
  if (!state.key.trim()) {
    if (provider.id === REGISTERABLE_PROVIDER && !state.registration) return { state, effect: { type: "register", provider: provider.id } }
    return { state: { ...state, error: strings.keyEmpty, errorKind: "input" } }
  }
  if (provider.requires_manual_models) {
    return {
      state: { ...state, step: "models", error: "", models: { status: "loading", all: [] }, filter: "", filterCursor: 0, modelIndex: 0 },
      effect: { type: "listModels", provider: provider.id, key: state.key.trim() },
    }
  }
  return toWorking(state, { type: "connect", provider: provider.id, key: state.key.trim(), model: "" }, "key")
}

function submitModel(state: SetupState): Update {
  const provider = state.provider
  if (!provider) return { state }
  const model = filteredModels(state)[state.modelIndex] ?? state.filter.trim()
  if (!model) return { state }
  return toWorking(state, { type: "connect", provider: provider.id, key: state.key.trim(), model }, "models")
}

// The components the run would install: the ones that exist on this machine and are ticked.
export function chosenComponents(install: InstallState): string[] {
  return install.components.filter((component) => component.available && component.checked).map((component) => component.id)
}

function withInstall(state: SetupState, change: Partial<InstallState>): SetupState {
  return state.install ? { ...state, install: { ...state.install, ...change } } : state
}

// One line of the choice: an item, or the row that folds the build tools away.
export type Row = { kind: "component"; component: Component; index: number } | { kind: "group" }

// The rows in the order they are drawn: the items, then the folding row, then (when it is open)
// the build tools. A plan without build tools has no folding row.
export function choiceRows(install: InstallState): Row[] {
  const rows: Row[] = []
  install.components.forEach((component, index) => {
    if (!BUILD_TOOLS.includes(component.id)) rows.push({ kind: "component", component, index })
  })
  const tools = install.components.flatMap((component, index) => (BUILD_TOOLS.includes(component.id) ? [{ kind: "component" as const, component, index }] : []))
  if (tools.length === 0) return rows
  rows.push({ kind: "group" })
  if (install.buildOpen) rows.push(...tools)
  return rows
}

// Folds or unfolds the build tools; the highlight returns to the folding row if it was inside.
function setBuildOpen(state: SetupState, open: boolean): SetupState {
  const install = state.install
  if (!install || install.buildOpen === open) return state
  const rows = choiceRows({ ...install, buildOpen: open })
  const group = rows.findIndex((row) => row.kind === "group")
  const cursor = open || install.cursor <= group ? install.cursor : group
  return withInstall(state, { buildOpen: open, cursor })
}

// → unfolds the build tools from their folding row; ← folds them from the row or from inside them.
function foldWithArrow(state: SetupState, open: boolean): SetupState {
  const install = state.install
  const row = install ? choiceRows(install)[install.cursor] : undefined
  if (!install || !row) return state
  const inBuildTools = row.kind === "group" || BUILD_TOOLS.includes(row.component.id)
  return inBuildTools ? setBuildOpen(state, open) : state
}

// A tick is flipped by key or click alike; the program itself and what is not here stay as they are.
// The folding row has no tick: it folds or unfolds.
function toggleComponent(state: SetupState, rowIndex: number): Update {
  const install = state.install
  const row = install ? choiceRows(install)[rowIndex] : undefined
  if (!install || !row) return { state }
  const moved = withInstall(state, { cursor: rowIndex })
  if (row.kind === "group") return { state: setBuildOpen(moved, !install.buildOpen) }
  if (!row.component.available || row.component.id === REQUIRED_COMPONENT) return { state: moved }
  const components = install.components.map((item, at) => (at === row.index ? { ...item, checked: !item.checked } : item))
  return { state: withInstall(state, { cursor: rowIndex, components }) }
}

function startInstall(state: SetupState): Update {
  const install = state.install
  if (!install || install.plan !== "ready") return { state }
  const only = chosenComponents(install)
  if (only.length === 0) return { state }
  const status: Record<string, ComponentStatus> = {}
  only.forEach((id) => (status[id] = "waiting"))
  const running = { ...withInstall(state, { status, message: "", warnings: [], error: "" }), step: "installing" as const }
  // A sudo that wants a password cannot ask inside this screen: the terminal is handed over first.
  const needsPassword = install.privilege === "password" && only.some((id) => BUILD_TOOLS.includes(id))
  return { state: running, effect: needsPassword ? { type: "elevate" } : { type: "install", only } }
}

function onInstallEvent(state: SetupState, event: InstallEvent): SetupState {
  const install = state.install
  if (!install) return state
  switch (event.event) {
    case "component": {
      const status: ComponentStatus = event.status === "start" ? "running" : event.status === "done" ? "done" : "failed"
      return withInstall(state, { status: { ...install.status, [event.id]: status } })
    }
    case "step":
      return withInstall(state, { message: event.message })
    case "warning":
      return withInstall(state, { warnings: [...install.warnings, event.message] })
    case "error":
    case "done":
      return state
  }
}

function back(state: SetupState): Update {
  switch (state.step) {
    case "components":
      return { state, effect: { type: "finish", outcome: { kind: "quit" } } }
    case "provider":
      return { state: { ...state, step: "welcome" } }
    case "broad":
      return { state: { ...state, step: "welcome" } }
    case "key":
      return { state: { ...state, step: "provider", error: "" } }
    case "models":
      return { state: { ...state, step: "key", error: "" } }
    case "welcome":
      return { state, effect: { type: "finish", outcome: { kind: "quit" } } }
    default:
      return { state }
  }
}

function openFolder(state: SetupState): Update {
  if (state.scope !== "full") return { state }
  const folder = initialFolder(state.workspace || state.home || "/", state.home, state.drives)
  return { state: { ...state, step: "folder", folder }, effect: { type: "list", path: folder.path } }
}

// How wide the working folder is, unless the person has already said it is meant. A provider-only
// setup has no folder.
export function widthWarning(state: SetupState): Breadth | null {
  if (state.scope !== "full" || state.broadAccepted === state.workspace) return null
  return breadthOf(state.workspace, state.home)
}

// "Use it anyway": the wide folder stays and is not asked about again.
function keepWide(state: SetupState): Update {
  return { state: { ...state, step: "provider", broadAccepted: state.workspace } }
}

function press(state: SetupState, strings: { keyEmpty: string }): Update {
  switch (state.step) {
    case "components":
      return startInstall(state)
    case "welcome":
      return { state: { ...state, step: widthWarning(state) ? "broad" : "provider" } }
    // The safe answer to "this folder is wide" is the primary one: pick another.
    case "broad":
      return openFolder(state)
    case "provider":
      return chooseProvider(state, state.providerIndex)
    case "key":
      return submitKey(state, strings)
    case "models":
      return state.models.status === "loading" ? { state } : submitModel(state)
    case "interface":
      return { state, effect: { type: "finish", outcome: { kind: "frontend", value: INTERFACES[state.interfaceIndex] ?? "claude" } } }
    case "installing":
    case "working":
    case "starting":
    case "folder":
      return { state }
  }
}

function move(state: SetupState, delta: number): SetupState {
  switch (state.step) {
    case "components":
      return state.install ? withInstall(state, { cursor: wrap(state.install.cursor, delta, choiceRows(state.install).length) }) : state
    case "provider":
      return { ...state, providerIndex: wrap(state.providerIndex, delta, providerOptionCount(state)) }
    case "models":
      return { ...state, modelIndex: wrap(state.modelIndex, delta, filteredModels(state).length) }
    case "interface":
      return { ...state, interfaceIndex: wrap(state.interfaceIndex, delta, INTERFACES.length) }
    default:
      return state
  }
}

function onKey(state: SetupState, event: InputEvent, strings: { keyEmpty: string }): Update {
  if (event.type === "key" && event.ctrl && event.text === "c") return { state, effect: { type: "finish", outcome: { kind: "quit" } } }
  if (state.step === "starting" || state.step === "installing") return { state }
  if (state.step === "working") {
    // The connection is abandoned, not waited for: its answer is ignored when it arrives.
    if (event.type === "key" && event.key === "escape") return { state: { ...state, step: state.returnStep, error: "" }, effect: { type: "cancel" } }
    return { state }
  }
  if (state.step === "key" && event.type === "key" && event.key === "tab") return { state: { ...state, reveal: !state.reveal } }
  if (event.type === "key") {
    if (event.key === "enter") return press(state, strings)
    if (event.key === "escape") return back(state)
    if (state.step === "components" && (event.key === "left" || event.key === "right")) return { state: foldWithArrow(state, event.key === "right") }
    if (event.key === "up") return { state: move(state, -1) }
    if (event.key === "down" || event.key === "tab") return { state: move(state, 1) }
    if (event.key === "shift-tab") return { state: move(state, -1) }
  }
  if (state.step === "key") {
    const edited = edit(state.key, state.cursor, event)
    return edited ? { state: { ...state, key: edited.text, cursor: edited.cursor, error: "" } } : { state }
  }
  if (state.step === "models" && state.models.status !== "loading") {
    const edited = edit(state.filter, state.filterCursor, event)
    return edited ? { state: { ...state, filter: edited.text, filterCursor: edited.cursor, modelIndex: 0 } } : { state }
  }
  if (event.type === "key" && event.key === "char" && !event.ctrl) {
    if (state.step === "components") {
      if (event.text === " " || event.text === "x" || event.text === "X") return toggleComponent(state, state.install?.cursor ?? 0)
      const number = Number(event.text)
      if (number >= 1 && number <= (state.install ? choiceRows(state.install).length : 0)) return toggleComponent(state, number - 1)
      if (event.text === "q" || event.text === "Q") return { state, effect: { type: "finish", outcome: { kind: "quit" } } }
    }
    if (state.step === "welcome" && (event.text === "q" || event.text === "Q")) return { state, effect: { type: "finish", outcome: { kind: "quit" } } }
    if ((state.step === "welcome" || state.step === "broad") && (event.text === "f" || event.text === "F")) return openFolder(state)
    if (state.step === "broad" && (event.text === "u" || event.text === "U")) return keepWide(state)
    const digit = Number(event.text)
    if (state.step === "provider" && digit >= 1 && digit <= providerOptionCount(state)) return chooseProvider(state, digit - 1)
    if (state.step === "interface" && digit >= 1 && digit <= INTERFACES.length) {
      return { state: { ...state, interfaceIndex: digit - 1 }, effect: { type: "finish", outcome: { kind: "frontend", value: INTERFACES[digit - 1]! } } }
    }
  }
  return { state }
}

function onClick(state: SetupState, target: Target, strings: { keyEmpty: string }): Update {
  if (state.step === "working" || state.step === "starting" || state.step === "installing" || target.kind === "folder") return { state }
  if (state.step === "components" && target.kind === "option") return toggleComponent(state, target.index)
  if (target.kind === "button") {
    switch (target.id) {
      case "primary":
        return press(state, strings)
      case "back":
        return back(state)
      case "quit":
        return { state, effect: { type: "finish", outcome: { kind: "quit" } } }
      case "register":
        return state.provider ? { state, effect: { type: "register", provider: state.provider.id } } : { state }
      case "folder":
        return state.step === "welcome" || state.step === "broad" ? openFolder(state) : { state }
      case "anyway":
        return state.step === "broad" ? keepWide(state) : { state }
    }
  }
  if (state.step === "provider") return chooseProvider(state, target.index)
  if (state.step === "models") return submitModel({ ...state, modelIndex: target.index })
  if (state.step === "interface") {
    const value = INTERFACES[target.index]
    return value
      ? { state: { ...state, interfaceIndex: target.index }, effect: { type: "finish", outcome: { kind: "frontend", value } } }
      : { state }
  }
  return { state }
}

function failure(state: SetupState, error: string): SetupState {
  return { ...state, step: state.returnStep, error, errorKind: "connect" }
}

// The folder chooser runs by its own rules; leaving it returns to the welcome screen, with the
// folder it chose, if it chose one.
function inFolder(state: SetupState, action: Action): Update {
  const folder = state.folder
  if (!folder) return { state: { ...state, step: "welcome" } }
  let result
  if (action.type === "input") result = updateFolder(folder, { type: "input", event: action.event })
  else if (action.type === "click" && action.target.kind === "folder") result = updateFolder(folder, { type: "click", target: action.target.target })
  else if (action.type === "click" && action.target.kind === "option") result = updateFolder(folder, { type: "click", target: { kind: "row", index: action.target.index } })
  else if (action.type === "wheel") result = updateFolder(folder, { type: "wheel", delta: action.delta })
  else if (action.type === "listing") result = updateFolder(folder, { type: "listing", listing: action.listing })
  else if (action.type === "made") result = updateFolder(folder, { type: "made", path: action.path, error: action.error })
  else return { state }
  const effect = result.effect
  if (effect?.type === "choose") return { state: { ...state, step: "welcome", folder: null, workspace: effect.path } }
  if (effect?.type === "leave") return { state: { ...state, step: "welcome", folder: null } }
  return { state: { ...state, folder: result.state }, effect }
}

export function update(state: SetupState, action: Action, strings: { keyEmpty: string }): Update {
  if (action.type === "input" && action.event.type === "key" && action.event.ctrl && action.event.text === "c") {
    return { state, effect: { type: "finish", outcome: { kind: "quit" } } }
  }
  if (state.step === "folder") return inFolder(state, action)
  switch (action.type) {
    case "input":
      return onKey(state, action.event, strings)
    case "click":
      return onClick(state, action.target, strings)
    case "wheel":
      return { state: move(state, action.delta) }
    case "catalog":
      return action.reply.ok
        ? { state: { ...state, catalog: "ready", providers: action.reply.providers } }
        : { state: { ...state, catalog: "failed", error: action.reply.error } }
    case "models": {
      if (state.step !== "models") return { state }
      if (!action.reply.ok) return { state: { ...state, models: { status: "failed", all: [] }, error: action.reply.error, errorKind: "connect" } }
      const all = action.reply.models
      return { state: { ...state, models: { status: all.length ? "ready" : "failed", all }, error: "" } }
    }
    case "connected":
      // An answer that arrives after the person pressed Esc is not theirs any more.
      if (state.step !== "working") return { state }
      if (!action.reply.ok) return { state: failure(state, action.reply.error) }
      if (state.scope === "provider") return { state: { ...state, error: "", connected: state.provider?.label ?? "" }, effect: { type: "finish", outcome: { kind: "done" } } }
      return { state: { ...state, step: "interface", error: "", connected: state.provider?.label ?? "" } }
    case "registered":
      return action.reply.ok ? { state: { ...state, registration: { opened: action.reply.opened, url: action.reply.url } } } : { state: { ...state, error: action.reply.error } }
    case "plan": {
      if (!state.install) return { state }
      if (!action.reply.ok) return { state: withInstall(state, { plan: "failed", error: action.reply.error }) }
      const reply = action.reply
      const components = COMPONENT_IDS.flatMap((id) => {
        const found = reply.components.find((item) => item.id === id)
        // Everything that can be installed here starts ticked, the build tools included; the
        // folding row shows how many of them are.
        return found ? [{ id, available: found.available, reason: found.reason ?? "", targets: found.targets, checked: found.available }] : []
      })
      return { state: withInstall(state, { plan: "ready", components, privilege: reply.privilege, cursor: 0, error: "" }) }
    }
    case "installEvent":
      return { state: onInstallEvent(state, action.event) }
    case "elevated": {
      const install = state.install
      if (state.step !== "installing" || !install) return { state }
      // The password was not given: back to the choice, nothing was installed.
      if (action.error) return { state: { ...withInstall(state, { error: action.error, status: {} }), step: "components" } }
      return { state, effect: { type: "install", only: chosenComponents(install) } }
    }
    case "installed":
      // A run that was abandoned with Ctrl+C has no screen left to update.
      if (state.step !== "installing") return { state }
      if (!action.reply.ok) return { state: { ...withInstall(state, { error: action.reply.error, status: {} }), step: "components" } }
      return { state: { ...state, step: "welcome" } }
    case "listing":
    case "made":
      // Folder answers belong to the chooser, which is not open.
      return { state }
  }
}
