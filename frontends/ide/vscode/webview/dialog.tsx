import { useEffect, useRef, useState } from "preact/hooks"

import { approvalOptions, subjectOf } from "../../../tui/ink/approval"
import { parseUnifiedDiff } from "../../../tui/ink/diff"
import type { InputOption, InputRequest } from "../../../tui/ink/protocol"
import { CodeBlock, diffRows } from "./transcript"

export type Answer = (answer: boolean | string | null, remember?: boolean) => void

function matches(option: InputOption, query: string): boolean {
  const haystack = `${option.label} ${option.value} ${option.description ?? ""}`.toLowerCase()
  return haystack.includes(query.toLowerCase())
}

// A short list (the agent's "plan first or start right away?") reads as buttons; the
// filter box only earns its row on a long catalog such as the model list.
const FILTER_FROM = 7

function ChoiceDialog({ request, answer }: { request: InputRequest; answer: Answer }) {
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState(0)
  const options = (request.options ?? []).filter((option) => matches(option, query))
  const filterable = (request.options?.length ?? 0) >= FILTER_FROM
  const active = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLDivElement>(null)

  useEffect(() => active.current?.scrollIntoView({ block: "nearest" }), [selected, query])
  useEffect(() => {
    if (!filterable) list.current?.focus()
  }, [filterable])

  function onKey(event: KeyboardEvent): void {
    if (event.key === "ArrowDown") setSelected(Math.min(selected + 1, options.length - 1))
    else if (event.key === "ArrowUp") setSelected(Math.max(selected - 1, 0))
    else if (event.key === "Enter" && options[selected]) answer(options[selected].value)
    else return
    event.preventDefault()
  }

  return (
    <>
      {filterable ? (
        <input
          class="field"
          autofocus
          placeholder="Type to filter"
          value={query}
          onInput={(event) => {
            setQuery((event.target as HTMLInputElement).value)
            setSelected(0)
          }}
          onKeyDown={onKey}
        />
      ) : null}
      <div class="options" ref={list} tabIndex={filterable ? undefined : 0} onKeyDown={filterable ? undefined : onKey}>
        {options.map((option, i) => (
          <div
            key={option.value}
            ref={i === selected ? active : undefined}
            class={`option ${i === selected ? "selected" : ""} ${option.value === request.current_value ? "current" : ""}`}
            onClick={() => answer(option.value)}
          >
            <div class="option-label">{option.label}</div>
            {option.description ? <div class="option-description">{option.description}</div> : null}
          </div>
        ))}
        {options.length === 0 ? <div class="empty">Nothing matches</div> : null}
      </div>
    </>
  )
}

function TextDialog({ request, answer }: { request: InputRequest; answer: Answer }) {
  const [value, setValue] = useState(request.kind === "secret" ? "" : request.current_value)
  return (
    <>
      <input
        class="field"
        autofocus
        type={request.kind === "secret" ? "password" : "text"}
        value={value}
        onInput={(event) => setValue((event.target as HTMLInputElement).value)}
        onKeyDown={(event) => event.key === "Enter" && answer(value)}
      />
      <div class="buttons">
        <button class="btn primary" onClick={() => answer(value)}>Continue</button>
        <button class="btn" onClick={() => answer(null)}>Cancel</button>
      </div>
    </>
  )
}

// Removed lines on red and added lines on green, as in the transcript's edit cards.
function OverwriteDiff({ diff }: { diff: string }) {
  const rows = parseUnifiedDiff(diff)
  if (rows.length === 0) return <pre class="output">{diff}</pre>
  const { lines, more } = diffRows(rows)
  return <CodeBlock lines={lines} more={more} />
}

function Approval({ request, answer }: { request: InputRequest; answer: Answer }) {
  const subject = subjectOf(request)
  return (
    <>
      {subject?.kind === "command" ? (
        <>
          <pre class="output">{subject.command}</pre>
          {subject.reason ? <div class="help">{subject.reason}</div> : null}
        </>
      ) : null}
      {subject?.kind === "overwrite" ? (
        <>
          <div class="target">{subject.path}</div>
          {subject.diff ? <OverwriteDiff diff={subject.diff} /> : null}
        </>
      ) : null}
      {subject === null && request.body ? <pre class="output">{request.body}</pre> : null}
      <div class="buttons column">
        {approvalOptions(request).map((option) => (
          <button
            key={option.decision}
            class={`btn ${option.decision === "yes" ? "primary" : ""}`}
            onClick={() => answer(option.decision !== "no", option.decision === "remember")}
          >
            {option.label}
          </button>
        ))}
      </div>
    </>
  )
}

export function Dialog({ request, answer }: { request: InputRequest; answer: Answer }) {
  const body =
    request.kind === "confirm" ? <Approval request={request} answer={answer} /> :
    request.kind === "choice" ? <ChoiceDialog request={request} answer={answer} /> :
    <TextDialog request={request} answer={answer} />
  return (
    <div class="dialog" onKeyDown={(event) => event.key === "Escape" && answer(request.kind === "confirm" ? false : null)}>
      <div class="dialog-title">{request.title}</div>
      {request.kind !== "confirm" && request.body ? <div class="help">{request.body}</div> : null}
      {body}
      {request.help_text ? <div class="help">{request.help_text}</div> : null}
    </div>
  )
}
