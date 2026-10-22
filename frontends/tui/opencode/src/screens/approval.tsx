// OpenCode asks for permission in the prompt's place (routes/session/permission.tsx); Kvist's
// approvals (a command that needs a yes, a file about to be overwritten) are asked the same way.

import { useTerminalDimensions } from "@opentui/solid"
import { createMemo, Show } from "solid-js"

import { ARM_MS, approvalOptions, subjectOf, type Decision } from "../../../ink/approval"
import type { InputRequest } from "../../../ink/protocol"
import { PatchDiff } from "../oc/tui/component/patch-diff"
import { useConfig } from "../oc/tui/config"
import { useTheme, useThemes } from "../oc/tui/context/theme"
import { SessionQuestion } from "../oc/tui/routes/session/question"
import { filetype } from "../oc/tui/util/filetype"

const LABEL: Record<Decision, string> = { yes: "Allow once", remember: "Allow always", no: "Reject" }
const KEY: Record<Decision, "once" | "always" | "reject"> = { yes: "once", remember: "always", no: "reject" }

function DiffBody(props: { file: string; diff: string }) {
  const theme = useTheme()
  const syntax = useThemes().currentSyntax
  const config = useConfig().data
  const dimensions = useTerminalDimensions()
  const view = () => (config.diffs?.view === "split" || (config.diffs?.view !== "unified" && dimensions().width > 120) ? "split" : "unified")
  return (
    <box flexDirection="column" gap={1}>
      <text fg={theme.text.base}>{`← Overwrite ${props.file}`}</text>
      <scrollbox height="100%" verticalScrollbarOptions={{ trackOptions: { backgroundColor: theme.background.base, foregroundColor: theme.scrollbar.base } }}>
        <PatchDiff
          diff={props.diff}
          hunkFg={theme.diff.text.hunkHeader}
          view={view()}
          filetype={filetype(props.file)}
          syntaxStyle={syntax()}
          showLineNumbers={true}
          width="100%"
          wrapMode="word"
          fg={theme.text.base}
          addedBg={theme.diff.background.added}
          removedBg={theme.diff.background.removed}
          contextBg={theme.diff.background.context}
          addedSignColor={theme.diff.highlight.added}
          removedSignColor={theme.diff.highlight.removed}
          lineNumberFg={theme.diff.lineNumber.text}
          lineNumberBg={theme.diff.background.context}
          addedLineNumberBg={theme.diff.lineNumber.background.added}
          removedLineNumberBg={theme.diff.lineNumber.background.removed}
        />
      </scrollbox>
    </box>
  )
}

export function ApprovalPrompt(props: { request: InputRequest; onDecide: (decision: Decision) => void }) {
  const theme = useTheme()
  const subject = () => subjectOf(props.request)
  const options = createMemo(() => approvalOptions(props.request))
  // Enter typed in the moment the prompt appears most likely belongs to whatever the person was
  // typing, not to the question.
  const armedAt = Date.now() + ARM_MS

  const labels = createMemo(() => Object.fromEntries(options().map((option) => [KEY[option.decision], LABEL[option.decision]])) as Record<"once" | "always" | "reject", string>)
  const decide = (key: "once" | "always" | "reject") => {
    if (key !== "reject" && Date.now() < armedAt) return
    props.onDecide(key === "once" ? "yes" : key === "always" ? "remember" : "no")
  }

  const header = () => (
    <box flexDirection="column" gap={0}>
      <box flexDirection="row" gap={1} flexShrink={0}>
        <text fg={theme.text.feedback.warning.base}>{"△"}</text>
        <text fg={theme.text.base}>Permission required</text>
      </box>
    </box>
  )

  const body = () => {
    const current = subject()
    if (current?.kind === "overwrite") return <DiffBody file={current.path} diff={current.diff} />
    if (current?.kind === "command") {
      return (
        <box paddingLeft={1} gap={1}>
          <text fg={theme.text.base} wrapMode="char">{`$ ${current.command}`}</text>
          <Show when={current.reason}>
            <text fg={theme.text.muted} wrapMode="word">{current.reason}</text>
          </Show>
        </box>
      )
    }
    return (
      <box paddingLeft={1} gap={1}>
        <text fg={theme.text.base} wrapMode="word">{props.request.title}</text>
        <Show when={props.request.body}>
          <text fg={theme.text.muted} wrapMode="word">{props.request.body}</text>
        </Show>
      </box>
    )
  }

  return (
    <SessionQuestion
      title="Permission required"
      instance={props.request.request_id}
      header={header()}
      body={body()}
      options={labels()}
      escapeKey="reject"
      onSelect={decide}
    />
  )
}
