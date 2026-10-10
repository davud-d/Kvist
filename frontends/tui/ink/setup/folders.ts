// The folder chooser as a pure state machine, like flow.ts: input and directory listings go in,
// the next state and at most one thing to do come out. Reading directories is run.ts's job.
import { posix, win32 } from "node:path"

import type { InputEvent } from "./keys.js"

// Not a path: the view of the drives of a Windows machine, which has no folder of its own.
export const DRIVES = "::drives::"

export interface DirEntry {
  name: string
  // The folder has a .git inside: the mark of a project.
  git: boolean
}

export interface Listing {
  path: string
  dirs: DirEntry[]
  // Files are only counted: the chooser is for folders.
  files: number
  error?: string
}

export type FolderFocus = "list" | "use" | "cancel"

export interface FolderState {
  path: string
  home: string
  // The folder the chooser was opened on, offered as a place of its own.
  start: string
  // The machine has drive letters, so there is a level above each drive's root.
  drives: boolean
  status: "loading" | "ready" | "failed"
  dirs: DirEntry[]
  files: number
  error: string
  filter: string
  // Row index: 0 is "use this folder", then the parent (unless at the root), then the folders.
  index: number
  showHidden: boolean
  // A name being typed for a new folder, or null.
  creating: string | null
  focus: FolderFocus
}

export type Row = { kind: "use" } | { kind: "parent" } | { kind: "dir"; entry: DirEntry }

export type FolderTarget =
  | { kind: "row"; index: number }
  | { kind: "crumb"; index: number }
  | { kind: "place"; id: "home" | "start" | "root" | "drives" }
  | { kind: "toggle"; id: "hidden" | "new" }
  | { kind: "action"; id: "use" | "cancel" }

export type FolderAction =
  | { type: "input"; event: InputEvent }
  | { type: "click"; target: FolderTarget }
  | { type: "wheel"; delta: number }
  | { type: "listing"; listing: Listing }
  | { type: "made"; path: string; error?: string }

export type FolderEffect =
  | { type: "list"; path: string }
  | { type: "make"; parent: string; name: string }
  | { type: "choose"; path: string }
  | { type: "leave" }

export interface FolderUpdate {
  state: FolderState
  effect?: FolderEffect
}

const PAGE = 8

export function initialFolder(start: string, home: string, drives = false): FolderState {
  return {
    path: start,
    home,
    start,
    drives,
    status: "loading",
    dirs: [],
    files: 0,
    error: "",
    filter: "",
    index: 0,
    showHidden: false,
    creating: null,
    focus: "list",
  }
}

// Windows paths start with a drive letter or a UNC prefix; everything else is read the Unix way.
// Deciding by the path itself keeps both readings testable on any machine.
const flavour = (path: string) => (/^[A-Za-z]:|^\\\\/.test(path) ? win32 : posix)

export function parentOf(path: string): string {
  return path === DRIVES ? DRIVES : flavour(path).dirname(path)
}

export function joinPath(path: string, name: string): string {
  return flavour(path).join(path, name)
}

// "/" on Unix, "C:\\" on Windows.
export function rootOf(path: string): string {
  return path === DRIVES ? DRIVES : flavour(path).parse(path).root || "/"
}

export function isRoot(path: string): boolean {
  return parentOf(path) === path
}

// Whether `path` is `home` or inside it (Windows paths compare without regard to case).
function within(path: string, home: string): boolean {
  if (!home || isRoot(home)) return false
  const f = flavour(path)
  const [a, b] = f === win32 ? [path.toLowerCase(), home.toLowerCase()] : [path, home]
  return a === b || a.startsWith(b.endsWith(f.sep) ? b : b + f.sep)
}

// The path with the home folder written as ~, as a shell prompt would.
export function tildePath(path: string, home: string): string {
  return within(path, home) ? `~${path.slice(home.length)}` : path
}

// What a folder is too wide a workspace for: the person's home, the top of a disk, or a folder
// that gathers many things that are not one project.
export type Breadth = "home" | "root" | "crowded"

// Folders under home where files of every kind end up.
const CROWDED_UNDER_HOME = new Set(["desktop", "documents", "downloads"])

// Same folder, whatever the separator at the end and (on Windows paths) the letter case.
function samePlace(a: string, b: string): boolean {
  if (!a || !b) return false
  const f = flavour(a)
  const plain = (value: string) => {
    const normal = f.normalize(value)
    const trimmed = !isRoot(normal) && normal.endsWith(f.sep) ? normal.slice(0, -1) : normal
    return f === win32 ? trimmed.toLowerCase() : trimmed
  }
  return plain(a) === plain(b)
}

export function breadthOf(path: string, home: string): Breadth | null {
  if (!path) return null
  if (isRoot(path)) return "root"
  if (samePlace(path, home)) return "home"
  if (!home) return null
  const f = flavour(home)
  if (samePlace(path, parentOf(home)) && !isRoot(parentOf(home))) return "crowded"
  const parent = parentOf(path)
  if (samePlace(parent, home) && CROWDED_UNDER_HOME.has(f.basename(path).toLowerCase())) return "crowded"
  return null
}

// The folders shown: hidden ones only when asked for, and only those matching what was typed.
export function visibleDirs(state: FolderState): DirEntry[] {
  const needle = state.filter.trim().toLowerCase()
  return state.dirs.filter((entry) => (state.showHidden || !entry.name.startsWith(".")) && (!needle || entry.name.toLowerCase().includes(needle)))
}

export function rowsOf(state: FolderState): Row[] {
  // Among the drives there is nothing to use as a workspace: each row is a drive to enter.
  if (state.path === DRIVES) return visibleDirs(state).map((entry) => ({ kind: "dir", entry }))
  const rows: Row[] = [{ kind: "use" }]
  // At a drive's root the parent is the list of drives, where the machine has them.
  if (!isRoot(state.path) || state.drives) rows.push({ kind: "parent" })
  for (const entry of visibleDirs(state)) rows.push({ kind: "dir", entry })
  return rows
}

// The path as a trail: each part with the folder it leads to (the home folder is "~").
export function crumbsOf(path: string, home: string): { label: string; path: string }[] {
  if (path === DRIVES) return [{ label: "Drives", path: DRIVES }]
  const f = flavour(path)
  const under = within(path, home)
  const root = rootOf(path)
  const first = under ? { label: "~", path: home } : { label: root, path: root }
  const rest = path.slice(under ? home.length : root.length).split(/[\\/]+/).filter(Boolean)
  const crumbs = [first]
  let at = first.path
  for (const part of rest) {
    at = f.join(at, part)
    crumbs.push({ label: part, path: at })
  }
  return crumbs
}

function clamp(index: number, count: number): number {
  return Math.max(0, Math.min(count - 1, index))
}

function go(state: FolderState, path: string): FolderUpdate {
  return {
    state: { ...state, path, status: "loading", dirs: [], files: 0, error: "", filter: "", index: 0, creating: null, focus: "list" },
    effect: { type: "list", path },
  }
}

function activate(state: FolderState, index: number): FolderUpdate {
  const row = rowsOf(state)[index]
  if (!row) return { state }
  if (row.kind === "use") return choose(state)
  if (row.kind === "parent") return up(state)
  // A drive's name is its root; a folder's name is joined onto the path.
  return go(state, state.path === DRIVES ? row.entry.name : joinPath(state.path, row.entry.name))
}

// There is no workspace among the drives.
function choose(state: FolderState): FolderUpdate {
  return state.path === DRIVES ? { state } : { state, effect: { type: "choose", path: state.path } }
}

// One level up: the parent folder, from a drive's root the list of drives, from there nowhere.
function up(state: FolderState): FolderUpdate {
  if (state.path === DRIVES) return { state }
  if (isRoot(state.path)) return state.drives ? go(state, DRIVES) : { state }
  return go(state, parentOf(state.path))
}

function move(state: FolderState, delta: number): FolderState {
  return { ...state, index: clamp(state.index + delta, rowsOf(state).length) }
}

const cycle: FolderFocus[] = ["list", "use", "cancel"]

function onKey(state: FolderState, event: InputEvent): FolderUpdate {
  if (event.type === "paste") {
    const text = event.text.replace(/[\r\n\t]+/g, "")
    return state.creating !== null
      ? { state: { ...state, creating: state.creating + text } }
      : { state: { ...state, filter: state.filter + text, index: 0 } }
  }
  if (event.type !== "key") return { state }

  if (state.creating !== null) {
    if (event.key === "escape") return { state: { ...state, creating: null } }
    if (event.key === "enter") {
      const name = state.creating.trim()
      return name ? { state, effect: { type: "make", parent: state.path, name } } : { state }
    }
    if (event.key === "backspace") return { state: { ...state, creating: state.creating.slice(0, -1) } }
    if (event.key === "char" && !event.ctrl) return { state: { ...state, creating: state.creating + event.text } }
    return { state }
  }

  if (event.ctrl) {
    if (event.text === "d") return { state: { ...state, showHidden: !state.showHidden, index: 0 } }
    if (event.text === "n") return { state: { ...state, creating: "" } }
    return { state }
  }

  switch (event.key) {
    case "up":
      return { state: move(state, -1) }
    case "down":
      return { state: move(state, 1) }
    case "pageup":
      return { state: move(state, -PAGE) }
    case "pagedown":
      return { state: move(state, PAGE) }
    case "home":
      return { state: { ...state, index: 0 } }
    case "end":
      return { state: { ...state, index: rowsOf(state).length - 1 } }
    case "tab":
    case "shift-tab": {
      const step = event.key === "tab" ? 1 : cycle.length - 1
      return { state: { ...state, focus: cycle[(cycle.indexOf(state.focus) + step) % cycle.length]! } }
    }
    case "enter":
      if (state.focus === "use") return choose(state)
      if (state.focus === "cancel") return { state, effect: { type: "leave" } }
      return activate(state, state.index)
    case "right": {
      const row = rowsOf(state)[state.index]
      return row?.kind === "dir" ? activate(state, state.index) : { state }
    }
    case "left":
      return up(state)
    case "backspace":
      if (state.filter) return { state: { ...state, filter: state.filter.slice(0, -1), index: 0 } }
      return up(state)
    case "escape":
      if (state.filter) return { state: { ...state, filter: "", index: 0 } }
      return { state, effect: { type: "leave" } }
    case "char":
      return { state: { ...state, filter: state.filter + event.text, index: 0, focus: "list" } }
    default:
      return { state }
  }
}

function onClick(state: FolderState, target: FolderTarget): FolderUpdate {
  if (state.creating !== null) return { state }
  switch (target.kind) {
    case "row":
      return activate({ ...state, index: target.index }, target.index)
    case "crumb": {
      const crumb = crumbsOf(state.path, state.home)[target.index]
      return crumb && crumb.path !== state.path ? go(state, crumb.path) : { state }
    }
    case "place":
      if (target.id === "drives") return state.drives ? go(state, DRIVES) : { state }
      return go(state, target.id === "home" ? state.home : target.id === "start" ? state.start : rootOf(state.path === DRIVES ? state.start : state.path))
    case "toggle":
      return target.id === "hidden" ? { state: { ...state, showHidden: !state.showHidden, index: 0 } } : { state: { ...state, creating: "" } }
    case "action":
      return target.id === "use" ? choose(state) : { state, effect: { type: "leave" } }
  }
}

export function updateFolder(state: FolderState, action: FolderAction): FolderUpdate {
  switch (action.type) {
    case "input":
      return onKey(state, action.event)
    case "click":
      return onClick(state, action.target)
    case "wheel":
      return { state: move(state, action.delta) }
    case "listing":
      // An answer for a folder the person has already left changes nothing.
      if (action.listing.path !== state.path) return { state }
      if (action.listing.error) return { state: { ...state, status: "failed", dirs: [], files: 0, error: action.listing.error } }
      return { state: { ...state, status: "ready", dirs: action.listing.dirs, files: action.listing.files, error: "", index: clamp(state.index, 1 + action.listing.dirs.length + 2) } }
    case "made":
      if (action.error) return { state: { ...state, error: action.error } }
      // The new folder is entered at once: that is what making one is for.
      return go({ ...state, creating: null }, action.path)
  }
}
