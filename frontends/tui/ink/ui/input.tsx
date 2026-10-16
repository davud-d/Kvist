import { clipboardImage, pathImage, packImages, type ImageAttachment } from "../images.js"
import { Box, Text, useInput, usePaste } from "ink"
import { useWindowSize } from "./window-size.js"
import { useReducer, useRef, type ReactNode } from "react"

import { NAME_WIDTH, resolveEnter, suggest, visibleBoxRows, visibleSuggestionCount, windowStart } from "../commands.js"
import { applyKey, cursorRow, emptyEditor, insert, moveRow, wrapRows, type Editor } from "../editor.js"
import type { Starters } from "../starters.js"
import { collapsePaste, emptyPasteStore, expandPastes, findRepeatedLabel, isLargePaste, labelLengthBefore } from "../paste.js"
import { color, currentSkin } from "./theme.js"

interface Props {
  active: boolean
  history: readonly string[]
  placeholder?: string
  mask?: boolean
  // Requests offered while the box is empty, for a person who has not sent one yet.
  starters?: Starters | null
  // Shown directly under the frame, above the command suggestions.
  caption?: ReactNode
  // `shown` is the line as typed, with paste labels; `text` has the pasted text put back.
  onSubmit: (text: string, shown: string) => void
}

// The box prefix and the frame's borders and padding leave columns - 6 cells
// for the line, one of them kept for the cursor at the end of a full row.
export function lineCells(columns: number): number {
  return Math.max(8, columns - 6) - 1
}

// The text wrapped to the box width; `maxRows` bounds how many rows show, scrolled to keep the cursor row in view.
export function Line({ editor, mask, active, maxRows = 1 }: { editor: Editor; mask: boolean; active: boolean; maxRows?: number }) {
  const { columns } = useWindowSize()
  const shown = mask ? { text: "•".repeat(editor.text.length), cursor: editor.cursor } : editor
  const rows = wrapRows(shown.text, lineCells(columns))
  const row = cursorRow(rows, shown.cursor)
  const visible = Math.max(1, maxRows)
  const first = Math.min(Math.max(0, row - Math.floor(visible / 2)), Math.max(0, rows.length - visible))
  return (
    <Box flexDirection="column" flexGrow={1}>
      {rows.slice(first, first + visible).map((span, i) => {
        const text = shown.text.slice(span.start, span.end)
        if (first + i !== row) return <Text key={first + i}>{text || " "}</Text>
        const column = shown.cursor - span.start
        return (
          <Text key={first + i}>
            {text.slice(0, column)}
            {active ? <Text inverse>{text.slice(column, column + 1) || " "}</Text> : text.slice(column, column + 1)}
            {text.slice(column + 1)}
          </Text>
        )
      })}
    </Box>
  )
}

interface Model {
  editor: Editor
  recall: number
  pick: number
  // What was typed when history recall began; Down at the newest entry brings it back.
  draft: string
}

export function InputBox({ active, history, placeholder, mask = false, starters = null, caption, onSubmit }: Props) {
  // Several keys can arrive in one stdin chunk and are handled before React
  // re-renders, so the source of truth is a ref, not the render's closure.
  const model = useRef<Model>({ editor: emptyEditor, recall: -1, pick: 0, draft: "" })
  const pastes = useRef(emptyPasteStore())
  const images = useRef(new Map<number, ImageAttachment>())
  const imageError = useRef("")
  const nextImage = useRef(1)
  const [, redraw] = useReducer((n: number) => n + 1, 0)
  const update = (patch: Partial<Model>) => {
    Object.assign(model.current, patch)
    redraw()
  }

  const { rows, columns } = useWindowSize()
  const visible = visibleSuggestionCount(rows)
  const boxRows = visibleBoxRows(rows)

  const { editor, pick } = model.current
  const options = mask ? [] : suggest(editor.text)
  const chosen = Math.min(pick, Math.max(0, options.length - 1))
  const first = windowStart(options.length, chosen, visible)

  // Editing a recalled entry makes it the new text; the next Up starts from the newest entry again.
  const change = (next: Editor) => update({ editor: next, pick: 0, recall: next.text === model.current.editor.text ? model.current.recall : -1 })

  const attach = (getImage: () => ImageAttachment | null): boolean => {
    try {
      const item = getImage()
      if (!item) return false
      const retained = new Set([...model.current.editor.text.matchAll(/\[Image #(\d+)\]/g)].map(match => Number(match[1])))
      for (const id of images.current.keys()) if (!retained.has(id)) images.current.delete(id)
      if (images.current.size >= 8) throw new Error("Up to 8 images / Не больше 8 изображений")
      const id = nextImage.current++
      images.current.set(id, item)
      imageError.current = ""
      change(insert(model.current.editor, `[Image #${id}]`, true))
    } catch (error) { imageError.current = String(error); redraw() }
    return true
  }

  const submit = (text: string) => {
    update({ editor: emptyEditor, recall: -1, pick: 0, draft: "" })
    const expanded = expandPastes(pastes.current, text)
    pastes.current = emptyPasteStore()
    const payload = packImages(expanded, images.current)
    images.current.clear()
    nextImage.current = 1
    imageError.current = ""
    onSubmit(payload, text)
  }

  const enter = (text: string, selected: number) => {
    if (mask) return submit(text)
    if (text.startsWith("/image ")) {
      const path = text.slice(7)
      change(emptyEditor)
      attach(() => { const result = pathImage(path); if (!result) throw new Error("Expected an image file / Укажите файл изображения"); return result })
      return
    }
    const action = resolveEnter(text, selected)
    if (action.kind === "fill") return change({ text: action.text, cursor: action.text.length })
    submit(action.text)
  }

  usePaste(
    (text) => {
      const current = model.current.editor
      if (!mask && attach(() => pathImage(text))) return
      if (mask || !isLargePaste(text)) return change(insert(current, text, !mask))
      // The same text pasted again replaces its label with the text.
      const repeated = findRepeatedLabel(pastes.current, current.text, text)
      if (!repeated) return change(insert(current, collapsePaste(pastes.current, text)))
      const rest = { text: current.text.slice(0, repeated.start) + current.text.slice(repeated.end), cursor: repeated.start }
      change(insert(rest, text, true))
    },
    { isActive: active },
  )

  useInput(
    (input, key) => {
      // Ink strips ESC from unrecognised sequences, including late replies to
      // its kitty keyboard query. These are terminal reports, not typed text.
      if (/^\[\?\d+u$/.test(input)) return
      if (!mask && key.ctrl && input === "v") { attach(clipboardImage); return }
      const { editor: current, recall, pick: picked } = model.current
      const shown = mask ? [] : suggest(current.text)
      const selected = Math.min(picked, Math.max(0, shown.length - 1))
      // Shift+Enter needs a terminal that reports it (kitty keyboard protocol); Alt+Enter arrives as ESC CR everywhere.
      if (key.return && (key.shift || key.meta) && !mask) return change(insert(current, "\n", true))
      // An empty box with requests on offer: the arrows choose, Tab or Enter puts the choice in the
      // box (and a second Enter sends it, so it can be edited first).
      const offer = !mask && !current.text && starters ? starters.prompts : []
      if (offer.length) {
        if (key.upArrow || key.downArrow) return update({ pick: (Math.min(picked, offer.length - 1) + (key.upArrow ? -1 : 1) + offer.length) % offer.length })
        if (key.tab || key.return) {
          const text = offer[Math.min(picked, offer.length - 1)]!
          return change({ text, cursor: text.length })
        }
      }
      if (key.return) return enter(current.text, selected)
      // Keystrokes coalesced by a slow link arrive as "text\r" in one chunk.
      if (input.length > 1 && /[\r\n]$/.test(input)) return enter(insert(current, input.replace(/[\r\n]+$/, ""), true).text, selected)
      if (key.tab && shown[selected]) {
        const text = `/${shown[selected].name} `
        return change({ text, cursor: text.length })
      }
      if (key.upArrow || key.downArrow) {
        const up = key.upArrow
        // The command menu is for typing a command; a recalled one keeps walking the history.
        if (shown.length && recall < 0) return update({ pick: (selected + (up ? -1 : 1) + shown.length) % shown.length })
        const moved = moveRow(current, lineCells(columns), up ? -1 : 1)
        if (moved) return update({ editor: moved })
        // On the first or last row the arrow goes to the text's start or end; only the next one recalls history.
        if (recall < 0 && current.text) {
          const edge = up ? 0 : current.text.length
          if (current.cursor !== edge) return update({ editor: { text: current.text, cursor: edge } })
          if (!up) return
        }
        const next = Math.min(history.length - 1, Math.max(-1, recall + (up ? 1 : -1)))
        if (next === recall) return
        const draft = recall < 0 ? current.text : model.current.draft
        const text = next < 0 ? draft : (history[history.length - 1 - next] ?? "")
        return update({ recall: next, draft, editor: { text, cursor: text.length } })
      }
      const label = key.backspace ? (/\[Image #\d+\]$/.exec(current.text.slice(0, current.cursor))?.[0].length || labelLengthBefore(current.text, current.cursor)) : 0
      if (label) {
        const start = current.cursor - label
        return change({ text: current.text.slice(0, start) + current.text.slice(current.cursor), cursor: start })
      }
      const next = applyKey(current, input, key)
      if (next) change(next)
    },
    { isActive: active },
  )

  const claude = currentSkin() === "claude"
  const line = editor.text || !placeholder ? (
    <Line editor={editor} mask={mask} active={active} maxRows={boxRows} />
  ) : (
    <Text wrap="truncate-end">
      {active ? <Text inverse> </Text> : null}
      <Text color={color.faint}>{placeholder}</Text>
    </Text>
  )

  const pasteHint = imageError.current ? <Text color={color.faint}>{imageError.current}</Text> : !mask && active && /\[Pasted text #\d+ /.test(editor.text) ? (
    <Text color={color.faint}>  paste again to expand</Text>
  ) : null

  const suggestions = (
    <>
      {options.slice(first, first + visible).map((command, i) => {
        const picked = first + i === chosen
        if (claude) {
          return (
            <Text key={command.name} color={picked ? color.select : color.dim} wrap="truncate-end">
              {"  "}/{command.name.padEnd(NAME_WIDTH)}  {command.hint}
            </Text>
          )
        }
        return (
          <Text key={command.name} color={picked ? color.accent : command.route === "classic" ? color.faint : color.dim} wrap="truncate-end">
            {"  "}
            {picked ? "▸" : " "} /{command.name.padEnd(NAME_WIDTH)}
            <Text color={color.faint}> {command.hint}</Text>
          </Text>
        )
      })}
      {options.length > visible ? (
        <Text color={color.faint} wrap="truncate-end">  {chosen + 1}/{options.length} · ↑↓ select · tab complete</Text>
      ) : null}
    </>
  )

  const offered = !mask && !editor.text && starters ? starters.prompts.slice(0, visible) : []
  const startersPick = Math.min(pick, Math.max(0, offered.length - 1))
  const starterRows =
    offered.length > 0 && starters ? (
      <>
        {visible > offered.length ? (
          <Text color={color.faint} wrap="truncate-end">  {starters.title}</Text>
        ) : null}
        {offered.map((prompt, i) => (
          <Text key={prompt} color={i === startersPick ? (claude ? color.select : color.accent) : color.dim} wrap="truncate-end">
            {"  "}{i === startersPick ? "▸" : " "} {prompt}
          </Text>
        ))}
        {visible > offered.length ? (
          <Text color={color.faint} wrap="truncate-end">  {starters.hint}</Text>
        ) : null}
      </>
    ) : null

  // Claude Code lists the commands above the input, which stays where it is.
  if (claude) {
    return (
      <Box flexDirection="column">
        {suggestions}
        {starterRows}
        <Box borderStyle="single" borderColor={color.rule} borderLeft={false} borderRight={false} paddingLeft={1}>
          <Text color={active ? color.text : color.faint}>❯ </Text>
          {line}
        </Box>
        {pasteHint}
        {caption}
      </Box>
    )
  }

  return (
    <Box flexDirection="column">
      <Box borderStyle="round" borderColor={active ? color.accent : color.faint} paddingX={1}>
        <Text color={active ? color.accent : color.faint}>❯ </Text>
        {line}
      </Box>
      {pasteHint}
      {caption}
      {suggestions}
      {starterRows}
    </Box>
  )
}

interface ClosedBarProps {
  // The core's `closed_notice`; the bar has no words of its own.
  notice: string
  active: boolean
  caption?: ReactNode
  onNewSession: () => void
}

// A session end_session closed: the notice sits in the input's frame, one row tall like it.
// A calm stop, not an error: muted text, no alarm colour. Enter is the only key it takes,
// and it has no editor, so nothing typed or pasted can reach the session.
export function ClosedBar({ notice, active, caption, onNewSession }: ClosedBarProps) {
  useInput(
    (_input, key) => {
      if (key.return) onNewSession()
    },
    { isActive: active },
  )
  const text = <Text color={color.dim} wrap="truncate-end">{notice}</Text>
  if (currentSkin() === "claude") {
    return (
      <Box flexDirection="column">
        <Box borderStyle="single" borderColor={color.rule} borderLeft={false} borderRight={false} paddingLeft={1}>
          {text}
        </Box>
        {caption}
      </Box>
    )
  }
  return (
    <Box flexDirection="column">
      <Box borderStyle="round" borderColor={color.faint} paddingX={1}>
        {text}
      </Box>
      {caption}
    </Box>
  )
}
