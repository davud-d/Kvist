// Draws one frame of the setup screen: the key art over the whole window, dimmed, and a panel in
// the middle. Returns the ANSI rows and the places that react to a click.
import { sampleCells } from "./backdrop.js"
import { BOLD, Canvas, DIM, INVERSE, type Style } from "./canvas.js"
import { mix, pack, scale, unpack, type ColorLevel, type RGB } from "./color.js"
import { DRIVES, breadthOf, crumbsOf, isRoot, rootOf, rowsOf, tildePath, visibleDirs, type FolderTarget } from "./folders.js"
import {
  INTERFACES,
  KEY_PAGES,
  MORE_PROVIDERS,
  RECOMMENDED_PROVIDER,
  REGISTERABLE_PROVIDER,
  filteredModels,
  offeredProviders,
  type SetupState,
  type Target,
} from "./flow.js"
import type { Strings } from "./strings.js"
import { videoFit, type CellSize } from "./video.js"

export const MIN_COLS = 50
export const MIN_ROWS = 20
const MAX_PANEL_WIDTH = 68
const MAX_WIDE_PANEL_WIDTH = 112
const SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

const PALETTE = {
  text: [233, 238, 247] as RGB,
  dim: [160, 172, 192] as RGB,
  accent: [92, 200, 255] as RGB,
  warm: [255, 170, 80] as RGB,
  ok: [91, 227, 154] as RGB,
  err: [255, 107, 122] as RGB,
  panel: [9, 13, 27] as RGB,
  field: [24, 32, 56] as RGB,
  border: [78, 118, 180] as RGB,
  onAccent: [7, 14, 26] as RGB,
}

type Tone = "text" | "dim" | "accent" | "warm" | "ok" | "err" | "title" | "primary" | "secondary"

interface Segment {
  text: string
  tone: Tone
  hit?: Target
}

interface PanelLine {
  segments: Segment[]
  // The whole row is highlighted and answers a click.
  selected?: boolean
  hit?: Target
}

export interface Hit {
  col: number
  row: number
  width: number
  height: number
  target: Target
}

export interface SceneInput {
  state: SetupState
  strings: Strings
  cols: number
  rows: number
  level: ColorLevel
  // 0 is black, 1 is the picture as drawn; the resting value is DIMMED.
  brightness: number
  // How much of the panel shows: 0 is none, 1 is all of it; between, it is blended into the picture.
  panel: number
  // Animation counter for the spinner, in 100 ms steps from the start of the wait.
  tick: number
  // The folder Kvist will work in, as it is shown (home abbreviated).
  workspace: string
  // What the pointer is over, and what a pressed button is held on, for the buttons' look.
  hover: Target | null
  pressed: Target | null
  // Glyph set for folders and places: Nerd Font symbols where the terminal brings them, plain
  // Unicode elsewhere.
  icons: "nerd" | "plain"
  // While the panel changes size, only an empty pane of this rectangle is drawn.
  ghost: Rect | null
  // "cells" paints the picture as half blocks; "image" leaves the cells under the panel's
  // surroundings empty because a terminal image is showing through them (picture.ts).
  picture: "cells" | "image"
  // The terminal's cell size in pixels when the demo video can play (an image terminal, a decoder and
  // the clip are there); it sets the proportions of the video's slot on the interface step.
  video?: CellSize | null
}

export interface Rect {
  col: number
  row: number
  width: number
  height: number
}

export interface Scene {
  rows: string[]
  hits: Hit[]
  // The panel's rectangle in cells, or null while it is not shown. `glass` panels let more of
  // the picture through (the folder chooser is large, and the picture is part of why it is there).
  panel: (Rect & { glass: boolean }) | null
  // The cells the demo video fills, when it is to play on this screen.
  video?: Rect | null
}

// Brightness the picture rests at behind the panel: dim enough for text, bright enough to keep the detail.
export const PANEL_COLOR = PALETTE.panel

export function sameTarget(a: Target | null, b: Target | null): boolean {
  if (!a || !b || a.kind !== b.kind) return false
  if (a.kind === "folder") return JSON.stringify(a.target) === JSON.stringify((b as typeof a).target)
  return a.kind === "option" ? a.index === (b as typeof a).index : a.id === (b as typeof a).id
}

export const DIMMED = 0.55

const smooth = (t: number) => t * t * (3 - 2 * t)

function drawBackdrop(canvas: Canvas, brightness: number): void {
  const art = sampleCells(canvas.cols, canvas.rows)
  const cx = (canvas.cols - 1) / 2
  const cy = (canvas.rows - 1) / 2
  for (let row = 0; row < canvas.rows; row++) {
    for (let col = 0; col < canvas.cols; col++) {
      // Darker toward the middle, where the panel is, lighter at the edges.
      const radius = Math.min(1, Math.hypot((col - cx) / cx, (row - cy) / cy))
      const factor = brightness * (0.72 + 0.28 * smooth(radius))
      const at = row * canvas.cols + col
      const fg: RGB = scale([art.fg[at * 3]!, art.fg[at * 3 + 1]!, art.fg[at * 3 + 2]!], factor)
      const bg: RGB = scale([art.bg[at * 3]!, art.bg[at * 3 + 1]!, art.bg[at * 3 + 2]!], factor)
      canvas.set(col, row, art.chars[at]!, { fg, bg })
    }
  }
}

function wrapText(text: string, width: number): string[] {
  const lines: string[] = []
  let line = ""
  for (const word of text.split(/\s+/)) {
    if (!word) continue
    let rest = word
    while (rest.length > width) {
      if (line) lines.push(line)
      lines.push(rest.slice(0, width))
      rest = rest.slice(width)
      line = ""
    }
    if (!line) line = rest
    else if (line.length + 1 + rest.length <= width) line += ` ${rest}`
    else {
      lines.push(line)
      line = rest
    }
  }
  if (line) lines.push(line)
  return lines
}

function truncate(text: string, width: number): string {
  return text.length <= width ? text : `${text.slice(0, Math.max(0, width - 1))}…`
}

function maskKey(key: string): string {
  if (key.length <= 8) return "•".repeat(key.length)
  return "•".repeat(key.length - 4) + key.slice(-4)
}

// Keys that name their provider by prefix; a different start is worth a quiet word.
const KEY_PREFIX: Readonly<Record<string, string>> = { anthropic: "sk-ant-", openrouter: "sk-or-" }

// The end of a path is what identifies it, so a long one loses its start.
function tail(value: string, width: number): string {
  return value.length <= width ? value : `…${value.slice(value.length - width + 1)}`
}

// A failed connection: what happened, the core's own words, and what to do next.
function failureLines(state: SetupState, strings: Strings, width: number): PanelLine[] {
  if (!state.error) return []
  if (state.errorKind === "input") return paragraph(state.error, width, "err")
  const next = state.provider?.auth_type === "oauth" ? strings.connectFailedNextOauth : strings.connectFailedNext
  return [text(strings.connectFailed, "err"), ...paragraph(state.error, width, "dim"), ...paragraph(next, width, "dim")]
}

// What the highlighted interface looks like at the prompt, as far as it can be said in three rows.
function previewLines(index: number, strings: Strings, width: number): PanelLine[] {
  const span = Math.min(38, width - 2)
  const ask = "❯ Ask Kvist, or / for commands"
  if (index === 0) return [text(` ${"─".repeat(span)}`, "dim"), text(` ${ask}`, "text"), text(` ${"─".repeat(span)}`, "dim")]
  if (index === 1) return [text(` ╭${"─".repeat(span - 2)}╮`, "dim"), text(` │ ${ask.padEnd(span - 3)}│`, "text"), text(` ╰${"─".repeat(span - 2)}╯`, "dim")]
  return [blank(), text(` ${strings.previewClassic}`, "dim"), blank()]
}

const text = (value: string, tone: Tone = "text"): PanelLine => ({ segments: [{ text: value, tone }] })
const blank = (): PanelLine => text("")
const paragraph = (value: string, width: number, tone: Tone = "text"): PanelLine[] => wrapText(value, width).map((line) => text(line, tone))

function button(label: string, id: "primary" | "back" | "quit" | "register" | "folder" | "anyway", tone: Tone): Segment {
  return { text: ` ${label} `, tone, hit: { kind: "button", id } }
}

function buttons(...segments: Segment[]): PanelLine {
  const spaced: Segment[] = []
  segments.forEach((segment, index) => {
    if (index) spaced.push({ text: "  ", tone: "text" })
    spaced.push(segment)
  })
  return { segments: spaced }
}

// One selectable row: a number, a label column and a description that gives way when the row is short.
function optionRow(index: number, selected: boolean, label: string, blurb: string, tag: string, width: number, labelWidth: number, numbered = true): PanelLine {
  const head = `${numbered ? ` ${index + 1}  ` : "  "}${label.padEnd(labelWidth)}`
  const room = Math.max(0, width - head.length)
  const body = truncate(`${blurb}${tag ? `  ${tag}` : ""}`, room)
  return {
    segments: [
      { text: head, tone: selected ? "primary" : "text" },
      { text: body, tone: selected ? "primary" : "dim" },
    ],
    selected,
    hit: { kind: "option", index },
  }
}

function providerLines(state: SetupState, strings: Strings, width: number): PanelLine[] {
  const lines: PanelLine[] = [text(strings.providerTitle, "title"), ...paragraph(strings.providerBody, width, "dim"), blank()]
  if (state.catalog === "loading") return [...lines, text(strings.loadingCatalog, "dim")]
  const offered = offeredProviders(state)
  const labelWidth = Math.min(18, Math.max(...offered.map((provider) => provider.label.length), strings.moreProviders.length) + 2)
  offered.forEach((provider, index) => {
    const tag = provider.id === RECOMMENDED_PROVIDER ? `(${strings.recommended})` : ""
    lines.push(optionRow(index, state.providerIndex === index, provider.label, strings.providerBlurbs[provider.id] ?? "", tag, width, labelWidth))
  })
  lines.push(optionRow(offered.length, state.providerIndex === offered.length, strings.moreProviders, strings.moreProvidersBlurb, "", width, labelWidth))
  if (state.catalog === "failed") lines.push(blank(), text(`${strings.catalogFailed} ${state.error}`.trim(), "err"))
  return lines
}

function fieldLine(value: string, cursor: number, placeholder: string, width: number, secret: boolean, right = ""): { line: PanelLine; caret: number } {
  const shown = secret ? maskKey(value) : value
  const room = Math.max(4, width - 4 - right.length)
  const offset = Math.max(0, cursor - room + 1)
  const visible = shown.slice(offset, offset + room)
  const empty = value.length === 0
  const body = empty ? placeholder : visible
  const pad = " ".repeat(Math.max(0, width - 3 - body.length - right.length))
  return {
    line: { segments: [{ text: " › ", tone: "accent" }, { text: body, tone: empty ? "dim" : "text" }, { text: pad + right, tone: "dim" }] },
    caret: empty ? 0 : cursor - offset,
  }
}

interface Built {
  lines: PanelLine[]
  // Where a scrollbar goes: the first list line, how many are shown, how many there are, and
  // the index of the first one shown.
  scroll?: { line: number; count: number; total: number; first: number }
  // The line holding a text cursor and its column inside the panel's content, if any.
  caret?: { line: number; column: number }
  // The first line of the demo video's slot and its size in cells; the lines are blank.
  video?: { line: number; columns: number; rows: number }
  hint: string
  step: number
  steps: number
}

const ICONS = {
  // Nerd Font code points: folder, open folder, home, root disk, plus, check, level up, git branch, eye.
  nerd: { folder: "\uf07b", open: "\uf07c", home: "\uf015", root: "\uf0a0", drive: "\uf0a0", plus: "\uf067", check: "\uf00c", up: "\uf148", git: "\ue702", shown: "\uf06e", hidden: "\uf070" },
  plain: { folder: "▸", open: "▾", home: "⌂", root: "▤", drive: "▣", plus: "+", check: "✓", up: "↑", git: "·", shown: "◉", hidden: "○" },
} as const

const folderHit = (target: FolderTarget): Target => ({ kind: "folder", target })

function folderButton(label: string, target: FolderTarget, tone: Tone): Segment {
  return { text: ` ${label} `, tone, hit: folderHit(target) }
}

// A row of the chooser: icon and name on the left, a mark or note at the right edge.
function folderRow(index: number, selected: boolean, icon: string, label: string, note: string, noteTone: Tone, width: number): PanelLine {
  const head = ` ${icon} `
  const room = Math.max(1, width - head.length - (note ? note.length + 2 : 0))
  const name = truncate(label, room)
  const pad = " ".repeat(Math.max(1, width - head.length - name.length - note.length))
  return {
    segments: [
      { text: head, tone: selected ? "primary" : "accent" },
      { text: name, tone: selected ? "primary" : "text" },
      { text: pad, tone: selected ? "primary" : "text" },
      { text: note, tone: selected ? "primary" : noteTone },
    ],
    selected,
    hit: folderHit({ kind: "row", index }),
  }
}

function buildFolder(input: SceneInput, width: number, listRows: number): Built {
  const { state, strings } = input
  const folder = state.folder
  const empty: Built = { lines: [], hint: "", step: 0, steps: 0 }
  if (!folder) return empty
  const icons = ICONS[input.icons]
  const lines: PanelLine[] = [text(strings.folderTitle, "title"), blank()]

  // The trail; a path too long for the line loses its first parts.
  const crumbs = crumbsOf(folder.path, folder.home)
  let first = 0
  const length = (from: number) => crumbs.slice(from).reduce((sum, crumb) => sum + (crumb.path === DRIVES ? strings.folderDrives : crumb.label).length, 0) + 3 * (crumbs.length - from - 1) + (from > 0 ? 4 : 0)
  while (first < crumbs.length - 1 && length(first) > width) first++
  const trail: Segment[] = first > 0 ? [{ text: "…", tone: "dim" }, { text: " › ", tone: "dim" }] : []
  crumbs.slice(first).forEach((crumb, offset) => {
    if (offset) trail.push({ text: " › ", tone: "dim" })
    const last = first + offset === crumbs.length - 1
    const label = crumb.path === DRIVES ? strings.folderDrives : crumb.label
    trail.push({ text: label, tone: last ? "title" : "accent", hit: last ? undefined : folderHit({ kind: "crumb", index: first + offset }) })
  })
  lines.push({ segments: trail })

  let caret: Built["caret"]
  if (folder.creating !== null) {
    const typed = folder.creating
    lines.push({
      segments: [
        { text: ` ${icons.plus} `, tone: "ok" },
        { text: `${strings.folderNameLabel}: `, tone: "dim" },
        { text: typed || "…", tone: typed ? "text" : "dim" },
      ],
    })
    caret = { line: lines.length - 1, column: 3 + strings.folderNameLabel.length + 2 + typed.length }
  } else {
    const places: Segment[] = [folderButton(`${icons.home} ${strings.folderHome}`, { kind: "place", id: "home" }, "secondary")]
    if (folder.start !== folder.home) places.push(folderButton(`${icons.open} ${strings.folderStart}`, { kind: "place", id: "start" }, "secondary"))
    if (folder.drives) places.push(folderButton(`${icons.drive} ${strings.folderDrives}`, { kind: "place", id: "drives" }, "secondary"))
    places.push(
      ...(folder.path === DRIVES ? [] : [folderButton(`${rootOf(folder.path) === "/" ? icons.root : icons.drive} ${rootOf(folder.path)}`, { kind: "place", id: "root" }, "secondary" as Tone)]),
      folderButton(`${icons.plus} ${strings.folderNew}`, { kind: "toggle", id: "new" }, "secondary"),
      folderButton(`${folder.showHidden ? icons.shown : icons.hidden} ${strings.folderHidden}`, { kind: "toggle", id: "hidden" }, folder.showHidden ? "primary" : "secondary"),
    )
    const spaced: Segment[] = []
    places.forEach((segment, index) => {
      if (index) spaced.push({ text: " ", tone: "text" })
      spaced.push(segment)
    })
    lines.push({ segments: spaced })
  }

  const dirs = visibleDirs(folder)
  const count = folder.status === "ready" ? String(dirs.length) : ""
  const filterField = fieldLine(folder.filter, folder.filter.length, strings.folderFilter, width, false, count)
  if (folder.creating === null) caret = { line: lines.length, column: 3 + filterField.caret }
  lines.push(filterField.line)

  // The list, with the selected row kept near the middle.
  const rows = rowsOf(folder)
  const room = width - 2
  const start = Math.max(0, Math.min(folder.index - Math.floor(listRows / 2), rows.length - listRows))
  const listTop = lines.length
  rows.slice(start, start + listRows).forEach((row, offset) => {
    const index = start + offset
    const selected = index === folder.index && folder.focus === "list"
    if (folder.path === DRIVES && row.kind === "dir") {
      const here = row.entry.name === rootOf(folder.start)
      lines.push(folderRow(index, selected, icons.drive, row.entry.name, here ? strings.folderCurrentDrive : "", "dim", room))
    } else if (row.kind === "use") {
      lines.push(folderRow(index, selected, icons.check, strings.folderUse, tailPath(folder.path, folder.home, 36), "dim", room))
    } else if (row.kind === "parent") {
      lines.push(folderRow(index, selected, icons.up, "..", isRoot(folder.path) ? strings.folderDrives : strings.folderParent, "dim", room))
    } else {
      lines.push(folderRow(index, selected, selected ? icons.open : icons.folder, row.entry.name, row.entry.git ? `${icons.git} git` : "", "warm", room))
    }
  })
  if (folder.status === "ready" && dirs.length === 0) {
    const at = Math.min(lines.length, listTop + rows.length)
    lines.splice(at, 0, text(`   ${folder.filter ? strings.folderNoMatch : strings.folderEmpty}`, "dim"))
  }
  // Rows beyond the folders are kept blank so the buttons stay at the bottom edge.
  while (lines.length < listTop + listRows) lines.push(blank())
  while (lines.length > listTop + listRows) lines.pop()

  let status: PanelLine
  if (folder.status === "loading") status = text(`${SPINNER[input.tick % SPINNER.length]} ${strings.folderReading}`, "dim")
  else if (folder.error) status = text(truncate(folder.error, width), "err")
  else if (folder.path === DRIVES) status = text(strings.folderDrivesHint, "dim")
  else status = text(strings.folderCounts(dirs.length, folder.files), "dim")
  lines.push(status)
  const focused = folder.focus
  const cancel = folderButton(`${focused === "cancel" ? "▸ " : ""}${strings.cancel}`, { kind: "action", id: "cancel" }, focused === "cancel" || folder.path === DRIVES ? "primary" : "secondary")
  // Among the drives there is nothing to use yet, so only Cancel is offered.
  lines.push(
    folder.path === DRIVES
      ? buttons(cancel)
      : buttons(folderButton(`${focused === "use" ? "▸ " : ""}${strings.folderUseButton}`, { kind: "action", id: "use" }, focused === "cancel" ? "secondary" : "primary"), cancel),
  )
  const hint = folder.creating !== null ? `${strings.hintEnter} · ${strings.hintBack}` : strings.folderHint
  return { lines, caret, hint, step: 0, steps: 0, scroll: { line: listTop, count: listRows, total: rows.length, first: start } }
}

// A long path keeps its end, with the home folder written as ~.
function tailPath(path: string, home: string, width: number): string {
  return tail(tildePath(path, home), width)
}

function build(input: SceneInput, width: number, listRows: number): Built {
  const { state, strings } = input
  const needsModel = state.provider?.requires_manual_models === true
  // Provider, key, (model), interface; a provider-only setup has no interface.
  const steps = (needsModel ? 4 : 3) - (state.scope === "provider" ? 1 : 0)
  const hintMouse = strings.hintMouse
  switch (state.step) {
    case "welcome": {
      const full = state.scope === "full"
      const wide = full && breadthOf(state.workspace, state.home) !== null
      const where = `${strings.workingFolder}: ${tail(input.workspace, Math.max(8, width - strings.workingFolder.length - 2))}`
      const title = text(strings.welcomeTitle, "title")
      const body = paragraph(strings.welcomeBody, width)
      const safe = paragraph(`✓ ${strings.welcomeSafe}`, width, "dim")
      const time = paragraph(strings.welcomeTime, width, "dim")
      const folder = full ? [text(where, wide ? "warm" : "dim")] : []
      const actions = buttons(
        button(strings.start, "primary", "primary"),
        ...(full ? [button(`${ICONS[input.icons].folder} ${strings.workingFolder}`, "folder", "secondary" as Tone)] : []),
        button(strings.quit, "quit", "secondary"),
      )
      // The panel is the window less its frame; what does not fit goes in this order: the time, the
      // gaps, the description. The buttons and the guarantees stay.
      const room = input.rows - 8
      const candidates: PanelLine[][] = [
        [title, blank(), ...body, blank(), ...safe, blank(), ...time, blank(), ...folder, ...(folder.length ? [blank()] : []), actions],
        [title, blank(), ...body, blank(), ...safe, blank(), ...folder, ...(folder.length ? [blank()] : []), actions],
        [title, ...body, ...safe, ...folder, blank(), actions],
        [title, ...safe, ...folder, blank(), actions],
      ]
      return {
        lines: candidates.find((lines) => lines.length <= room) ?? candidates[candidates.length - 1]!,
        hint: [strings.hintEnter, ...(full ? [strings.hintFolder] : []), strings.hintQuit, hintMouse].join(" · "),
        step: 0,
        steps,
      }
    }
    case "broad": {
      const breadth = breadthOf(state.workspace, state.home)
      const why = breadth === "home" ? strings.broadHome : breadth === "root" ? strings.broadRoot : strings.broadCrowded
      return {
        lines: [
          text(strings.broadTitle, "warm"),
          blank(),
          text(`${strings.workingFolder}: ${tail(input.workspace, Math.max(8, width - strings.workingFolder.length - 2))}`, "text"),
          blank(),
          ...paragraph(why, width),
          blank(),
          ...paragraph(strings.broadAdvice, width, "dim"),
          blank(),
          buttons(
            button(`${ICONS[input.icons].folder} ${strings.broadChoose}`, "folder", "primary"),
            button(strings.broadAnyway, "anyway", "secondary"),
            button(strings.back, "back", "secondary"),
          ),
        ],
        hint: [strings.hintChoose, strings.hintAnyway, strings.hintBack, hintMouse].join(" · "),
        step: 0,
        steps,
      }
    }
    case "folder":
      return buildFolder(input, width, listRows)
    case "provider":
      return { lines: providerLines(state, strings, width), hint: `${strings.hintMove} · ${strings.hintEnter} · ${strings.hintBack} · ${hintMouse}`, step: 1, steps }
    case "key": {
      const provider = state.provider
      const label = provider?.label ?? ""
      const field = fieldLine(state.key, state.cursor, strings.keyPlaceholder, width, !state.reveal)
      const lines: PanelLine[] = [text(strings.keyTitle(label), "title"), ...paragraph(strings.keyBody, width, "dim"), blank()]
      const caret = { line: lines.length, column: 3 + field.caret }
      lines.push(field.line)
      lines.push(blank())
      const prefix = provider ? KEY_PREFIX[provider.id] : undefined
      if (prefix && state.key.trim() && !state.key.trim().startsWith(prefix)) lines.push(text(strings.wrongPrefix(label, prefix), "warm"))
      const failure = failureLines(state, strings, width)
      lines.push(...failure, ...(failure.length ? [blank()] : []))
      if (provider?.id === REGISTERABLE_PROVIDER) {
        if (state.registration?.opened) lines.push(text(strings.registrationOpened, "ok"))
        else if (state.registration?.url) lines.push(text(strings.registrationNoBrowser, "dim"), text(state.registration.url, "accent"))
        else lines.push(buttons(button(strings.openRegistration, "register", "secondary")))
      } else if (provider && KEY_PAGES[provider.id]) {
        lines.push({ segments: [{ text: `${strings.getKey}: `, tone: "dim" }, { text: KEY_PAGES[provider.id]!, tone: "accent" }] })
      }
      lines.push(blank(), buttons(button(strings.continue, "primary", "primary"), button(strings.back, "back", "secondary")))
      return { lines, caret, hint: `${strings.hintEnter} · ${strings.hintReveal} · ${strings.hintBack}`, step: 2, steps }
    }
    case "models": {
      const lines: PanelLine[] = [text(strings.modelsTitle, "title"), ...paragraph(strings.modelsBody, width, "dim"), blank()]
      if (state.models.status === "loading") return { lines: [...lines, text(`${SPINNER[input.tick % SPINNER.length]} ${strings.modelsLoading}`, "dim")], hint: `${strings.hintBack}`, step: 3, steps }
      const matches = filteredModels(state)
      const first = Math.max(0, Math.min(state.modelIndex - listRows + 1, matches.length - listRows))
      const shownTo = Math.min(first + listRows, matches.length)
      const count = state.models.status !== "ready" ? "" : matches.length > listRows ? strings.modelsRange(first + 1, shownTo, matches.length) : strings.modelsCount(matches.length)
      const field = fieldLine(state.filter, state.filterCursor, strings.modelsFilter, width, false, count)
      const caretLine = lines.length
      lines.push(field.line)
      const note = state.models.status === "failed" ? strings.modelsCatalogFailed : matches.length === 0 && state.filter ? strings.modelsNone : ""
      if (note) lines.push(text(truncate(note, width), "dim"))
      matches.slice(first, first + listRows).forEach((id, offset) => {
        const index = first + offset
        lines.push(optionRow(index, index === state.modelIndex, id, "", "", width, width - 2, false))
      })
      lines.push(...failureLines(state, strings, width))
      lines.push(blank(), buttons(button(strings.continue, "primary", "primary"), button(strings.back, "back", "secondary")))
      return { lines, caret: { line: caretLine, column: 3 + field.caret }, hint: `${strings.hintMove} · ${strings.hintEnter} · ${strings.hintBack}`, step: 3, steps }
    }
    case "working": {
      const spinner = SPINNER[input.tick % SPINNER.length]
      const oauth = state.provider?.auth_type === "oauth"
      const seconds = Math.floor(input.tick / 10)
      return {
        lines: [
          text(`${spinner} ${strings.connecting(state.provider?.label ?? "")}`, "title"),
          blank(),
          ...(oauth ? paragraph(strings.signIn, width, "dim") : []),
          ...(seconds >= 6 ? [text(strings.stillWaiting(seconds), "dim")] : []),
        ],
        hint: strings.hintCancel,
        step: needsModel ? 3 : 2,
        steps,
      }
    }
    case "interface": {
      const labels = [strings.interfaceClaude, strings.interfaceInk, strings.interfaceClassic]
      const lines: PanelLine[] = [
        ...(state.connected ? [text(`✓ ${strings.connected(state.connected)}`, "ok"), blank()] : []),
        text(strings.interfaceTitle, "title"),
        ...paragraph(strings.interfaceBody, width, "dim"),
        blank(),
      ]
      INTERFACES.forEach((_, index) => {
        const [label = "", blurb = ""] = labels[index] ?? []
        lines.push(optionRow(index, state.interfaceIndex === index, label, blurb, "", width, 14))
      })
      // The preview is a luxury of tall windows; the list and the button come first.
      let video: Built["video"]
      if (input.rows >= 28) {
        // Where the demo video can play, its slot is reserved for every interface so the panel does
        // not change height when the highlight moves; the video itself shows only on the first one.
        const fit = input.video ? videoFit(width, input.rows - 12 - lines.length, input.video) : null
        const preview = previewLines(state.interfaceIndex, strings, width)
        lines.push(blank())
        if (fit && state.interfaceIndex === 0) {
          video = { line: lines.length, ...fit }
          for (let row = 0; row < fit.rows; row++) lines.push(blank())
        } else {
          lines.push(...preview)
          for (let row = preview.length; row < (fit?.rows ?? 0); row++) lines.push(blank())
        }
      }
      lines.push(blank(), buttons(button(strings.ready, "primary", "primary")))
      return { lines, video, hint: `${strings.hintMove} · ${strings.hintEnter} · ${hintMouse}`, step: steps, steps }
    }
    case "starting":
      return { lines: [text(`${SPINNER[input.tick % SPINNER.length]} ${strings.starting}`, "title")], hint: "", step: steps, steps }
  }
}

type Interaction = "none" | "hover" | "pressed"

const WHITE: RGB = [255, 255, 255]

// A button answers the pointer in three steps: lit when it is over it, sunk (darker, bold text)
// while it is held down.
function style(tone: Tone, background: RGB | null, interaction: Interaction = "none"): Style {
  switch (tone) {
    case "text":
      return { fg: PALETTE.text }
    case "dim":
      return { fg: PALETTE.dim }
    case "accent":
      return { fg: PALETTE.accent }
    case "warm":
      return { fg: PALETTE.warm }
    case "ok":
      return { fg: PALETTE.ok }
    case "err":
      return { fg: PALETTE.err }
    case "title":
      return { fg: PALETTE.text, attrs: BOLD }
    case "primary": {
      const bg = interaction === "hover" ? mix(PALETTE.accent, WHITE, 0.28) : interaction === "pressed" ? scale(PALETTE.accent, 0.62) : PALETTE.accent
      return { fg: interaction === "pressed" ? PALETTE.text : PALETTE.onAccent, bg, attrs: BOLD }
    }
    case "secondary": {
      const base = background ?? PALETTE.panel
      const amount = interaction === "hover" ? 0.75 : interaction === "pressed" ? 0.2 : 0.45
      return { fg: PALETTE.text, bg: mix(base, PALETTE.border, amount), attrs: interaction === "pressed" ? BOLD : 0 }
    }
  }
}

// The colour each cell of a rectangle shows: the average of the cell's two colours.
function snapshotBackground(canvas: Canvas, left: number, top: number, width: number, height: number): RGB[] {
  const colors: RGB[] = []
  for (let row = top; row < top + height; row++) {
    for (let col = left; col < left + width; col++) {
      const at = row * canvas.cols + col
      const upper = canvas.fg[at]!
      const lower = canvas.bg[at]!
      colors.push(upper >= 0 && lower >= 0 ? mix(unpack(upper), unpack(lower), 0.5) : lower >= 0 ? unpack(lower) : PALETTE.panel)
    }
  }
  return colors
}

// Blends the drawn panel toward what was behind it, text included.
function fadePanel(canvas: Canvas, behind: RGB[], left: number, top: number, width: number, height: number, opacity: number): void {
  let index = 0
  for (let row = top; row < top + height; row++) {
    for (let col = left; col < left + width; col++) {
      const at = row * canvas.cols + col
      const under = behind[index++]!
      const bg = canvas.bg[at]! >= 0 ? unpack(canvas.bg[at]!) : under
      const shown = mix(under, bg, opacity)
      canvas.bg[at] = pack(shown)
      if (canvas.fg[at]! >= 0) canvas.fg[at] = pack(mix(shown, unpack(canvas.fg[at]!), opacity))
    }
  }
}

// Text over an image: it fades toward the panel's colour, which the image has been darkened to.
function fadeText(canvas: Canvas, left: number, top: number, width: number, height: number, opacity: number): void {
  for (let row = top; row < top + height; row++) {
    for (let col = left; col < left + width; col++) {
      const at = row * canvas.cols + col
      if (canvas.fg[at]! >= 0) canvas.fg[at] = pack(mix(PALETTE.panel, unpack(canvas.fg[at]!), opacity))
    }
  }
}

export function renderScene(input: SceneInput): Scene {
  const { cols, rows, level } = input
  const canvas = new Canvas(cols, rows)
  const hits: Hit[] = []
  const picture = level !== "none"
  const cells = input.picture === "cells"
  if (picture && cells) drawBackdrop(canvas, input.brightness)

  if (cols < MIN_COLS || rows < MIN_ROWS) {
    const message = input.strings.tooSmall(MIN_COLS, MIN_ROWS)
    const lines = wrapText(message, Math.max(10, cols - 2))
    lines.forEach((line, index) => canvas.text(1, Math.max(0, Math.floor(rows / 2) - 1) + index, line, { fg: PALETTE.text, bg: PALETTE.panel }))
    return { rows: canvas.toRows(level), hits, panel: null }
  }
  if (input.panel <= 0) return { rows: canvas.toRows(level), hits, panel: null }

  // The folder chooser takes most of the window; every other step is a small dialog.
  const wide = input.state.step === "folder"
  const ghost = input.ghost
  const panelWidth = ghost?.width ?? Math.min(wide ? MAX_WIDE_PANEL_WIDTH : MAX_PANEL_WIDTH, cols - 4)
  const inner = panelWidth - 4
  const wideHeight = Math.max(MIN_ROWS - 4, rows - 4)
  // Panel chrome (7) and the chooser's own rows: title, a gap, trail, places, filter, status, buttons.
  const listRows = wide ? Math.max(3, wideHeight - 7 - 7) : Math.max(3, Math.min(8, rows - 18))
  const built: Built = ghost ? { lines: [], hint: "", step: 0, steps: 0 } : build(input, inner, listRows)
  const height = ghost?.height ?? (wide ? wideHeight : built.lines.length + 7)
  const left = ghost?.col ?? Math.floor((cols - panelWidth) / 2)
  const top = ghost?.row ?? (wide ? 2 : Math.max(0, Math.floor((rows - height) / 2)))
  const glass = wide

  // What is behind the panel, for fading it in.
  const behind = cells ? snapshotBackground(canvas, left, top, panelWidth, height) : []
  // The panel is the picture pressed dark, so it reads as a pane of the same scene. With an
  // image behind it the darkening is in the image's pixels (picture.ts), not in these cells.
  if (cells) {
    for (let row = top; row < top + height; row++) {
      for (let col = left; col < left + panelWidth; col++) {
        const under = canvas.backgroundAt(col, row)
        canvas.set(col, row, " ", { bg: under ? mix(scale(under, glass ? 0.6 : 0.5), PALETTE.panel, glass ? 0.52 : 0.86) : PALETTE.panel })
      }
    }
  }
  const frame: Style = { fg: PALETTE.border }
  const edge = (col: number, row: number, char: string) => canvas.text(col, row, char, frame)
  for (let col = left + 1; col < left + panelWidth - 1; col++) {
    edge(col, top, "─")
    edge(col, top + height - 1, "─")
  }
  for (let row = top + 1; row < top + height - 1; row++) {
    edge(left, row, "│")
    edge(left + panelWidth - 1, row, "│")
  }
  edge(left, top, "╭")
  edge(left + panelWidth - 1, top, "╮")
  edge(left, top + height - 1, "╰")
  edge(left + panelWidth - 1, top + height - 1, "╯")

  const x0 = left + 2
  const panelRect = { col: left, row: top, width: panelWidth, height, glass }
  if (ghost) return { rows: canvas.toRows(level), hits, panel: panelRect }
  // Header: brand on the left, step counter on the right.
  canvas.text(x0, top + 1, "Kvist", { fg: PALETTE.accent, attrs: BOLD })
  canvas.text(x0 + 6, top + 1, "· setup", { fg: PALETTE.dim })
  if (built.step > 0) {
    const counter = input.strings.stepOf(built.step, built.steps)
    canvas.text(x0 + inner - counter.length, top + 1, counter, { fg: PALETTE.dim })
  }
  const dotsRow = top + 2
  if (built.step > 0) {
    for (let step = 1; step <= built.steps; step++) canvas.text(x0 + (step - 1) * 2, dotsRow, step <= built.step ? "●" : "○", { fg: step <= built.step ? PALETTE.accent : PALETTE.dim })
  }

  const contentTop = top + 4
  const interactionOf = (target: Target | undefined): Interaction =>
    !target ? "none" : sameTarget(input.pressed, target) ? "pressed" : sameTarget(input.hover, target) ? "hover" : "none"
  built.lines.forEach((line, index) => {
    const y = contentTop + index
    let x = x0
    const row = interactionOf(line.hit)
    if (line.selected) {
      const fill = row === "hover" ? mix(PALETTE.accent, WHITE, 0.28) : row === "pressed" ? scale(PALETTE.accent, 0.62) : PALETTE.accent
      canvas.fill(x0, y, inner, 1, { bg: fill })
    } else if (row !== "none") {
      // A row that is only pointed at gets a tint of the accent; pressed, a deeper one.
      canvas.fill(x0, y, inner, 1, { bg: mix(PALETTE.panel, PALETTE.accent, row === "pressed" ? 0.38 : 0.2) })
    }
    if (line.hit) hits.push({ col: x0, row: y, width: inner, height: 1, target: line.hit })
    for (const segment of line.segments) {
      const background = canvas.backgroundAt(x, y)
      // On a selected row the segments follow the row's own state, so text and fill agree.
      const interaction = segment.hit ? interactionOf(segment.hit) : line.selected ? row : "none"
      const painted = style(segment.tone, background, interaction)
      const used = canvas.text(x, y, segment.text, painted)
      if (segment.hit) hits.push({ col: x, row: y, width: used, height: 1, target: segment.hit })
      x += used
    }
  })
  if (built.scroll && built.scroll.total > built.scroll.count) {
    const { line, count, total, first } = built.scroll
    const thumb = Math.max(1, Math.round((count * count) / total))
    const thumbTop = Math.round((first / (total - count)) * (count - thumb))
    for (let step = 0; step < count; step++) {
      const on = step >= thumbTop && step < thumbTop + thumb
      canvas.text(x0 + inner - 1, contentTop + line + step, on ? "┃" : "│", { fg: on ? PALETTE.accent : PALETTE.border })
    }
  }
  if (built.caret) {
    const at = x0 + built.caret.column
    const y = contentTop + built.caret.line
    const current = canvas.chars[y * cols + at] ?? " "
    canvas.set(at, y, current, { fg: PALETTE.text, bg: canvas.backgroundAt(at, y) ?? PALETTE.panel, attrs: INVERSE })
  }
  canvas.text(x0, top + height - 2, truncate(built.hint, inner), { fg: PALETTE.dim, attrs: DIM })
  if (input.panel < 1) {
    if (cells) fadePanel(canvas, behind, left, top, panelWidth, height, input.panel)
    else fadeText(canvas, left, top, panelWidth, height, input.panel)
  }
  const slot = built.video
  const video = slot ? { col: x0 + Math.floor((inner - slot.columns) / 2), row: contentTop + slot.line, width: slot.columns, height: slot.rows } : null
  return { rows: canvas.toRows(level), hits, panel: panelRect, video }
}
