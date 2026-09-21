import { useEffect, useState } from "preact/hooks"

import { EFFORTS } from "../../src/commands"
import type { ConfigInfo } from "../src/messages"
import { post } from "./host"
import { Icon } from "./icons"

const ROLES = ["auto", "coder", "reader", "devops", "chat"]
const EFFORT_LABELS: Record<string, string> = { false: "off", low: "low", medium: "med", high: "high", xhigh: "xhigh", max: "max" }

// The two variables that have their own controls; everything else is a free-form list.
const SANDBOX_MODE = "AGENT_SANDBOX_MODE"
const SANDBOX_NET = "AGENT_SANDBOX_NET"
const SANDBOX_MODES = ["auto", "workspace", "bubblewrap"]
const SANDBOX_NETS = ["deny", "allow"]
const VARIABLE_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/

const SANDBOX_HINTS: Record<string, string> = {
  auto: "Uses Bubblewrap when it is installed",
  workspace: "Commands run directly on this machine: no Bubblewrap isolation, secrets are not masked and .kvist is not protected",
  bubblewrap: "Shell commands are blocked when Bubblewrap is missing",
}

type Variables = [string, string][]

const customVariables = (environment: Record<string, string>): Variables =>
  Object.entries(environment).filter(([name]) => name !== SANDBOX_MODE && name !== SANDBOX_NET)

interface Props {
  config: ConfigInfo | null
  provider: string
  role: string
  effort: string
  planning: boolean | null
  environment: Record<string, string> | null
  busy: boolean
  run: (name: string, args?: string[]) => void
  applyEnvironment: (environment: Record<string, string>) => void
  close: () => void
}

function Row({ title, hint, children }: { title: string; hint?: string; children?: preact.ComponentChildren }) {
  return (
    <div class="row">
      <div class="row-text">
        <div class="row-title">{title}</div>
        {hint ? <div class="row-hint">{hint}</div> : null}
      </div>
      {children ? <div class="row-control">{children}</div> : null}
    </div>
  )
}

function Segmented({ options, value, disabled, pick }: { options: readonly string[]; value: string; disabled: boolean; pick: (value: string) => void }) {
  return (
    <div class="segmented" role="radiogroup">
      {options.map((option) => (
        <button key={option} role="radio" aria-checked={option === value} class={option === value ? "on" : ""} disabled={disabled} onClick={() => option !== value && pick(option)}>
          {EFFORT_LABELS[option] ?? option}
        </button>
      ))}
    </div>
  )
}

function Environment({ environment, busy, apply }: { environment: Record<string, string>; busy: boolean; apply: (environment: Record<string, string>) => void }) {
  const [draft, setDraft] = useState<Variables>(customVariables(environment))
  useEffect(() => setDraft(customVariables(environment)), [JSON.stringify(environment)])

  const mode = environment[SANDBOX_MODE] ?? "auto"
  const net = environment[SANDBOX_NET] ?? "deny"
  const managed = (): Record<string, string> => Object.fromEntries(Object.entries(environment).filter(([name]) => name === SANDBOX_MODE || name === SANDBOX_NET))

  // A choice equal to the core's default is not stored, so "auto" and "deny" leave the variable unset.
  const choose = (name: string, value: string, fallback: string) => {
    const next = { ...environment }
    if (value === fallback) delete next[name]
    else next[name] = value
    apply(next)
  }

  const names = draft.map(([name]) => name.trim())
  const invalid = names.some((name) => name !== "" && !VARIABLE_NAME.test(name))
  const saved = JSON.stringify(customVariables(environment))
  const changed = JSON.stringify(draft.filter(([name]) => name.trim() !== "").map(([name, value]) => [name.trim(), value])) !== saved
  const save = () => {
    // The button is disabled for a bad name, but the check belongs here too.
    if (invalid) return
    apply({ ...Object.fromEntries(draft.filter(([name]) => name.trim() !== "").map(([name, value]) => [name.trim(), value])), ...managed() })
  }
  const edit = (index: number, part: 0 | 1, value: string) =>
    setDraft((rows) => rows.map((row, i) => (i === index ? (part === 0 ? [value, row[1]] : [row[0], value]) : row)))

  return (
    <>
      <div class="group-title">Environment</div>
      <div class="group">
        <div class="row stack">
          <div class="row-title">Command sandbox</div>
          <Segmented options={SANDBOX_MODES} value={mode} disabled={busy} pick={(value) => choose(SANDBOX_MODE, value, "auto")} />
          <div class={`row-hint ${mode === "workspace" ? "caution" : ""}`}>{SANDBOX_HINTS[mode]}</div>
        </div>
        <div class="row stack">
          <div class="row-title">Network for commands</div>
          <Segmented options={SANDBOX_NETS} value={net} disabled={busy} pick={(value) => choose(SANDBOX_NET, value, "deny")} />
          <div class="row-hint">Applies inside Bubblewrap only</div>
        </div>
        <div class="row stack">
          <div class="row-title">Other variables</div>
          {draft.map(([name, value], index) => (
            <div key={index} class="variable">
              <input class="field name" placeholder="NAME" value={name} disabled={busy} onInput={(event) => edit(index, 0, (event.target as HTMLInputElement).value)} />
              <input class="field" placeholder="value" value={value} disabled={busy} onInput={(event) => edit(index, 1, (event.target as HTMLInputElement).value)} />
              <button class="icon" title="Remove" disabled={busy} onClick={() => setDraft(draft.filter((_, i) => i !== index))}><Icon name="cross" /></button>
            </div>
          ))}
          {invalid ? <div class="row-hint caution">Names may use letters, digits and underscores, and cannot start with a digit</div> : null}
          <div class="row-hint">Applied when the core restarts, which happens as soon as you apply. Stored in plain text, so keep API keys in the provider settings.</div>
          <div class="buttons">
            <button class="btn" disabled={busy} onClick={() => setDraft([...draft, ["", ""]])}>Add variable</button>
            <button class="btn primary" disabled={busy || invalid || !changed} onClick={save}>Apply and restart</button>
          </div>
        </div>
      </div>
    </>
  )
}

export function Settings({ config, provider, role, effort, planning, environment, busy, run, applyEnvironment, close }: Props) {
  useEffect(() => {
    post({ type: "getConfig" })
    post({ type: "getEnvironment" })
  }, [])
  const current = config?.providers.find((item) => item.id === provider)

  return (
    <div class="settings">
      <div class="settings-head">
        <button class="icon" title="Back" onClick={close}><Icon name="back" /></button>
        <span>Settings</span>
      </div>
      {busy ? <div class="row-hint pad">Changes are available when the agent is idle.</div> : null}

      <div class="group-title">Provider</div>
      <div class="group">
        <Row title={current?.label ?? (provider || "Not set")} hint={current && !current.has_credentials ? "API key is missing" : undefined}>
          <button class="btn" disabled={busy} onClick={() => run("provider")}>Change</button>
        </Row>
        <Row title="Models" hint="Fast, smart and planner">
          <button class="btn" disabled={busy} onClick={() => run("models")}>Choose</button>
        </Row>
        <Row title="API key">
          <button class="btn" disabled={busy} onClick={() => run("change-api")}>Change</button>
        </Row>
        {current?.custom ? (
          <Row title="Custom provider">
            <button class="btn" disabled={busy} onClick={() => run("strime")}>Streaming</button>
            <button class="btn" disabled={busy} onClick={() => run("del-provider")}>Delete</button>
          </Row>
        ) : null}
      </div>

      <div class="group-title">Agent</div>
      <div class="group">
        <Row title="Planning" hint="Plan the task before acting">
          <button class="switch" role="switch" aria-checked={planning === true} disabled={busy || planning === null} onClick={() => run("plan", [String(!planning)])}>
            <span class="knob" />
          </button>
        </Row>
        <div class="row stack">
          <div class="row-title">Role</div>
          <Segmented options={ROLES} value={role} disabled={busy} pick={(value) => run("role", [value])} />
        </div>
        <div class="row stack">
          <div class="row-title">Reasoning effort</div>
          <Segmented options={EFFORTS} value={effort} disabled={busy} pick={(value) => run("effort", [value])} />
        </div>
        <Row title="Style" hint="How the agent works and talks">
          <button class="btn" disabled={busy} onClick={() => run("style")}>Choose</button>
        </Row>
      </div>

      {environment ? <Environment environment={environment} busy={busy} apply={applyEnvironment} /> : null}

      <div class="group-title">Advanced</div>
      <div class="group">
        <Row title="Requests" hint="Pacing, timeouts, context window">
          <button class="btn" disabled={busy} onClick={() => run("settings-api")}>Open</button>
        </Row>
        <Row title="Changes" hint="Files the agent changed">
          <button class="btn" disabled={busy} onClick={() => run("changes")}>Show</button>
          <button class="btn" disabled={busy} onClick={() => run("revert")}>Revert</button>
        </Row>
        <Row title="Reset everything" hint="Erases every saved provider and key">
          <button class="btn danger" disabled={busy} onClick={() => run("full-reset")}>Reset</button>
        </Row>
      </div>
    </div>
  )
}
