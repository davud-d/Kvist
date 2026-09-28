import { Box, Text, useInput, usePaste, useWindowSize } from "ink"
import { useReducer, useRef, type ReactNode } from "react"

import { NAME_WIDTH, resolveEnter, suggest, visibleBoxRows, visibleSuggestionCount, windowStart } from "../commands.js"
import { applyKey, emptyEditor, insert, windowLine, type Editor } from "../editor.js"
import { color, currentSkin } from "./theme.js"

interface Props {
  active: boolean
  history: readonly string[]
  placeholder?: string
  mask?: boolean
  // Shown directly under the frame, above the command suggestions.
  caption?: ReactNode
  onSubmit: (text: string) => void
}

// The box prefix and the frame's borders and padding leave columns - 6 cells
// for the line; `maxRows` bounds how much of a long line it may take.
export function Line({ editor, mask, active, maxRows = 1 }: { editor: Editor; mask: boolean; active: boolean; maxRows?: number }) {
  const { columns } = useWindowSize()
  const shown = mask ? { text: "•".repeat(editor.text.length), cursor: editor.cursor } : editor
  const { before, at, after } = windowLine(shown, Math.max(8, columns - 6) * maxRows)
  return (
    <Text>
      {before}
      {active ? <Text inverse>{at || " "}</Text> : at}
      {after}
    </Text>
  )
}

interface Model {
  editor: Editor
  recall: number
  pick: number
}

export function InputBox({ active, history, placeholder, mask = false, caption, onSubmit }: Props) {
  // Several keys can arrive in one stdin chunk and are handled before React
  // re-renders, so the source of truth is a ref, not the render's closure.
  const model = useRef<Model>({ editor: emptyEditor, recall: -1, pick: 0 })
  const [, redraw] = useReducer((n: number) => n + 1, 0)
  const update = (patch: Partial<Model>) => {
    Object.assign(model.current, patch)
    redraw()
  }

  const { rows } = useWindowSize()
  const visible = visibleSuggestionCount(rows)
  const boxRows = visibleBoxRows(rows)

  const { editor, pick } = model.current
  const options = mask ? [] : suggest(editor.text)
  const chosen = Math.min(pick, Math.max(0, options.length - 1))
  const first = windowStart(options.length, chosen, visible)

  const change = (next: Editor) => update({ editor: next, pick: 0 })

  const submit = (text: string) => {
    update({ editor: emptyEditor, recall: -1, pick: 0 })
    onSubmit(text)
  }

  const enter = (text: string, selected: number) => {
    if (mask) return submit(text)
    const action = resolveEnter(text, selected)
    if (action.kind === "fill") return change({ text: action.text, cursor: action.text.length })
    submit(action.text)
  }

  usePaste((text) => change(insert(model.current.editor, text)), { isActive: active })

  useInput(
    (input, key) => {
      const { editor: current, recall, pick: picked } = model.current
      const shown = mask ? [] : suggest(current.text)
      const selected = Math.min(picked, Math.max(0, shown.length - 1))
      if (key.return) return enter(current.text, selected)
      // Keystrokes coalesced by a slow link arrive as "text\r" in one chunk.
      if (input.length > 1 && /[\r\n]$/.test(input)) return enter(insert(current, input.replace(/[\r\n]+$/, "")).text, selected)
      if (key.tab && shown[selected]) {
        const text = `/${shown[selected].name} `
        return change({ text, cursor: text.length })
      }
      if (key.upArrow || key.downArrow) {
        if (shown.length) return update({ pick: (selected + (key.upArrow ? -1 : 1) + shown.length) % shown.length })
        const next = Math.min(history.length - 1, Math.max(-1, recall + (key.upArrow ? 1 : -1)))
        const text = next < 0 ? "" : (history[history.length - 1 - next] ?? "")
        return update({ recall: next, editor: { text, cursor: text.length } })
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

  // Claude Code lists the commands above the input, which stays where it is.
  if (claude) {
    return (
      <Box flexDirection="column">
        {suggestions}
        <Box borderStyle="single" borderColor={color.rule} borderLeft={false} borderRight={false} paddingLeft={1}>
          <Text color={active ? color.text : color.faint}>❯ </Text>
          {line}
        </Box>
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
      {caption}
      {suggestions}
    </Box>
  )
}
