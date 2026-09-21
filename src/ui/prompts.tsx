import { Box, Text, useInput, usePaste, useWindowSize } from "ink"
import { useEffect, useReducer, useRef, type ReactNode } from "react"

import {
  DIALOG_ROWS,
  choiceWindow,
  counterLabel,
  filterOptions,
  initialChoice,
  pasteChoice,
  pasteLine,
  stepChoice,
  stepConfirm,
  stepLine,
} from "../dialog.js"
import { diffLines } from "../diff.js"
import { emptyEditor } from "../editor.js"
import type { InputRequest } from "../protocol.js"
import { Line } from "./input.js"
import { color } from "./theme.js"
import {
  ARM_MS,
  ROWS_AFTER_LIST,
  approvalOptions,
  initialApproval,
  isTerminalReport,
  optionAt,
  parseCursorReport,
  parseMouse,
  stepApproval,
  stepClick,
  subjectOf,
  type ClickState,
} from "../approval.js"
import { disableMouse, enableMouse } from "../mouse.js"

const BODY_LINES = 14
const LABEL_WIDTH = 34
// Rows the dialog needs besides the option list: frame, title, search, footer, spacing.
const CHROME_ROWS = 10

type Answer = boolean | string | null | { approved: boolean; remember: boolean }

interface Props {
  request: InputRequest
  onAnswer: (answer: Answer) => void
}

// Keys are handled before React re-renders, so state lives in a ref; the
// answer is sent once even if a second Enter arrives in the same chunk.
function useDialogState<T>(initial: () => T) {
  const model = useRef<T | null>(null)
  if (model.current === null) model.current = initial()
  const answered = useRef(false)
  const [, redraw] = useReducer((n: number) => n + 1, 0)
  return {
    get: () => model.current as T,
    set: (next: T) => {
      model.current = next
      redraw()
    },
    settle: (answer: Answer, onAnswer: (value: Answer) => void) => {
      if (answered.current) return
      answered.current = true
      onAnswer(answer)
    },
  }
}

function Frame({ title, tone, footer, counter = "", children }: { title: string; tone: string; footer: string; counter?: string; children: ReactNode }) {
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={tone} paddingX={1}>
      <Box justifyContent="space-between">
        <Text bold wrap="truncate-end">{title}</Text>
        <Text color={color.dim}>esc</Text>
      </Box>
      {children}
      <Box justifyContent="space-between">
        <Text color={color.faint}>{footer}</Text>
        <Text color={color.faint}>{counter}</Text>
      </Box>
    </Box>
  )
}

function Body({ text }: { text: string }) {
  if (!text) return null
  const lines = text.split("\n")
  const shown = lines.slice(0, BODY_LINES)
  return (
    <Box flexDirection="column" marginTop={1}>
      {shown.map((line, i) => <Text key={i} wrap="truncate-end">{line || " "}</Text>)}
      {lines.length > shown.length ? <Text color={color.dim}>… {lines.length - shown.length} more lines</Text> : null}
    </Box>
  )
}

function Intro({ request }: { request: InputRequest }) {
  return (
    <>
      <Body text={request.body} />
      {request.help_text ? <Text color={color.dim}>{request.help_text}</Text> : null}
    </>
  )
}

function introRows(request: InputRequest): number {
  const body = request.body ? Math.min(request.body.split("\n").length, BODY_LINES + 1) + 1 : 0
  return body + (request.help_text ? request.help_text.split("\n").length : 0)
}

// Approval for dangerous commands and file overwrites: a numbered list
// (1. Yes / 2. Yes, and don't ask again / 3. No), arrows + Enter, digits,
// Esc = deny. Mouse: one click moves cursor, double click chooses.
function Approval({ request, onAnswer }: Props) {
  const subject = subjectOf(request)
  const options = approvalOptions(request)
  const dialog = useDialogState(() => initialApproval(Date.now()))
  const model = dialog.get()
  const clickState = useRef<ClickState>({ index: null, at: 0 })
  const cursorRow = useRef<number | null>(null)
  const waitingCursor = useRef(false)
  const frameBottom = useRef<number | null>(null)

  useEffect(() => {
    enableMouse(process.stdout)
    return () => disableMouse()
  }, [])

  // Request cursor position after first render to calibrate mouse clicks.
  useEffect(() => {
    if (cursorRow.current === null) {
      waitingCursor.current = true
      process.stdout.write("\x1b[6n")
    }
  }, [])

  const apply = (next: ReturnType<typeof stepApproval>) => {
    dialog.set(next.model)
    if (next.decision) {
      const choice = options[next.model.selected]
      const remember = choice?.decision === "remember"
      if (next.decision === "yes" || next.decision === "remember") {
        dialog.settle(remember ? { approved: true, remember: true } : true, onAnswer)
      } else {
        dialog.settle(false, onAnswer)
      }
    }
  }

  useInput((input, key) => {
    // Mouse reports and cursor position come as ANSI sequences in input.
    if (isTerminalReport(input)) {
      const cursorReport = parseCursorReport(input)
      if (cursorReport !== null) {
        if (waitingCursor.current) {
          // First calibration: cursor is right after the frame, so the frame's
          // bottom row is cursor - 1. Option list ends ROWS_AFTER_LIST rows above that.
          cursorRow.current = cursorReport
          frameBottom.current = cursorReport - 1
          waitingCursor.current = false
        }
        return
      }
      const mouse = parseMouse(input)
      if (mouse && mouse.button === 0 && !mouse.press && frameBottom.current !== null) {
        // Click released: map y to option index. The list's last row is
        // frameBottom - ROWS_AFTER_LIST; count upwards from there.
        const lastOptionRow = frameBottom.current - ROWS_AFTER_LIST
        const index = optionAt(mouse.y, lastOptionRow + 1, ROWS_AFTER_LIST, options.length)
        if (index !== null) {
          const result = stepClick(clickState.current, index, Date.now())
          clickState.current = result.state
          if (result.choose) {
            // Double click: choose immediately
            const choice = options[index]
            const remember = choice?.decision === "remember"
            if (choice?.decision === "no") {
              dialog.settle(false, onAnswer)
            } else {
              dialog.settle(remember ? { approved: true, remember: true } : true, onAnswer)
            }
          } else {
            // Single click: move cursor
            dialog.set({ ...model, selected: index, armedAt: Date.now() })
          }
        }
      }
      return
    }
    // Regular keyboard input
    apply(stepApproval(model, options, input, key, Date.now()))
  })

  const title = subject?.kind === "command" ? "Command approval" : subject?.kind === "overwrite" ? `Overwrite ${subject.path}` : request.title || "Approval required"

  return (
    <Box flexDirection="column">
      <Box height={1} backgroundColor={color.warn} />
      <Frame title={title} tone={color.warn} footer="↑↓ / 1 2 3 select · enter confirm · esc deny">
        {subject?.kind === "command" ? (
          <Box flexDirection="column" marginTop={1}>
            <Text>
              <Text color={color.accent}>$ </Text>
              <Text>{subject.command}</Text>
            </Text>
            {request.body && <Text color={color.dim}>{request.body}</Text>}
            <Box marginTop={1}>
              <Text color={color.warn}>This command requires approval</Text>
            </Box>
            <Box marginTop={1}>
              <Text>{subject.reason}</Text>
            </Box>
          </Box>
        ) : subject?.kind === "overwrite" ? (
          <Box flexDirection="column" marginTop={1}>
            {diffLines(subject.diff, "", 1, 10).slice(0, 20).map((row, i) => {
              const textColor = row.kind === "add" ? "#3fc47a" : row.kind === "del" ? "#f2565b" : color.dim
              return <Text key={i} color={textColor}>{row.text}</Text>
            })}
            {diffLines(subject.diff, "", 1, 10).length > 20 && <Text color={color.dim}>… {diffLines(subject.diff, "", 1, 10).length - 20} more lines</Text>}
          </Box>
        ) : (
          <Intro request={request} />
        )}
        <Box marginTop={1}>
          <Text bold>Do you want to proceed?</Text>
        </Box>
        <Box flexDirection="column" marginTop={1}>
          {options.map((option, i) => {
            const mark = model.selected === i ? "❯" : " "
            const selected = model.selected === i
            return (
              <Box key={i} backgroundColor={selected ? color.accent : undefined}>
                <Text color={selected ? color.onAccent : undefined}>
                  {mark} {i + 1}. {option.label}
                </Text>
              </Box>
            )
          })}
        </Box>
      </Frame>
    </Box>
  )
}

// Denying is the default: only an explicit `y` approves.
function Confirm({ request, onAnswer }: Props) {
  const answered = useRef(false)
  useInput((input, key) => {
    const answer = stepConfirm(input, key)
    if (answer === null || answered.current) return
    answered.current = true
    onAnswer(answer)
  })
  return (
    <Frame title={request.title || "Approval required"} tone={color.warn} footer="y allow once · n / enter / esc deny">
      <Intro request={request} />
      <Text> </Text>
    </Frame>
  )
}

function Row({ label, description, current, selected, labelWidth }: { label: string; description: string; current: boolean; selected: boolean; labelWidth: number }) {
  const dot = current ? "●" : " "
  const text = selected ? color.onAccent : undefined
  const muted = selected ? color.onAccentDim : color.dim
  return (
    <Box backgroundColor={selected ? color.accent : undefined}>
      <Text wrap="truncate-end" color={text}>
        <Text color={selected ? color.onAccent : color.accent}>{dot}</Text> {label.padEnd(labelWidth)}
        {description ? <Text color={muted}>  {description}</Text> : null}
      </Text>
    </Box>
  )
}

function Search({ query, cursor }: { query: string; cursor: number }) {
  return (
    <Box marginTop={1}>
      <Text color={color.accent}>⌕ </Text>
      {query ? <Line editor={{ text: query, cursor }} mask={false} active /> : (
        <Text>
          <Text inverse> </Text>
          <Text color={color.faint}>type to filter</Text>
        </Text>
      )}
    </Box>
  )
}

function Choice({ request, onAnswer }: Props) {
  const options = request.options ?? []
  const { rows } = useWindowSize()
  // The dialog is redrawn in place, so it has to fit the viewport.
  const size = Math.max(3, Math.min(DIALOG_ROWS, (rows || 24) - CHROME_ROWS - introRows(request)))
  const dialog = useDialogState(() => initialChoice(options, request.current_value))
  const model = dialog.get()

  const apply = (next: ReturnType<typeof stepChoice>) => {
    dialog.set(next.model)
    if (next.done) dialog.settle(next.done.value, onAnswer)
  }
  useInput((input, key) => apply(stepChoice(dialog.get(), options, size, input, key)))
  usePaste((text) => dialog.set(pasteChoice(dialog.get(), text)))

  const shown = filterOptions(options, model.query.text)
  const selected = Math.min(model.selected, Math.max(0, shown.length - 1))
  const { start, end } = choiceWindow(shown.length, selected, size)
  const labelWidth = Math.min(LABEL_WIDTH, Math.max(0, ...options.map((option) => [...option.label].length)))
  return (
    <Frame title={request.title} tone={color.accent} footer="↑↓ move · enter select · esc cancel" counter={counterLabel(shown.length, selected)}>
      <Intro request={request} />
      <Search query={model.query.text} cursor={model.query.cursor} />
      <Box flexDirection="column" marginTop={1}>
        {shown.length === 0 ? <Text color={color.dim}>  No matches</Text> : null}
        {shown.slice(start, end).map((option, i) => (
          <Row
            key={option.value}
            label={option.label}
            description={option.description ?? ""}
            current={option.value === request.current_value}
            selected={start + i === selected}
            labelWidth={labelWidth}
          />
        ))}
      </Box>
    </Frame>
  )
}

// The typed value lives only in this ref: a secret is never put into the
// reducer state, the input history or any log.
function Field({ request, onAnswer, secret }: Props & { secret: boolean }) {
  const fallback = secret ? "" : request.current_value
  const dialog = useDialogState(() => emptyEditor)
  useInput((input, key) => {
    const next = stepLine(dialog.get(), fallback, input, key)
    dialog.set(next.editor)
    if (next.done) dialog.settle(next.done.value, onAnswer)
  })
  usePaste((text) => dialog.set(pasteLine(dialog.get(), text)))

  const editor = dialog.get()
  return (
    <Frame title={request.title} tone={color.accent} footer="enter confirm · esc cancel">
      <Intro request={request} />
      <Box marginTop={1}>
        <Text color={color.accent}>❯ </Text>
        {editor.text || secret || !fallback ? (
          <Line editor={editor} mask={secret} active />
        ) : (
          <Text>
            <Text inverse> </Text>
            <Text color={color.faint}>{fallback}</Text>
          </Text>
        )}
      </Box>
    </Frame>
  )
}

export function Prompt(props: Props) {
  const subject = subjectOf(props.request)
  switch (props.request.kind) {
    case "confirm":
      return subject ? <Approval {...props} /> : <Confirm {...props} />
    case "choice":
      return <Choice {...props} />
    case "secret":
      return <Field {...props} secret />
    case "text":
      return <Field {...props} secret={false} />
  }
}
