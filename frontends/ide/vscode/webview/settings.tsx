import { useEffect, useState } from "preact/hooks"

import { EFFORTS } from "../../../tui/ink/commands"
import { findProvider } from "../src/messages"
import type { ConfigInfo } from "../src/messages"
import { post } from "./host"
import { Icon } from "./icons"

const ROLES = ["auto", "coder", "reader", "devops", "chat", "universal"]
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
  environment: Record<string, string> | null
  busy: boolean
  run: (name: string, args?: string[]) => void
  applyEnvironment: (environment: Record<string, string>) => void
  close: () => void
}

export function Row({ title, hint, children }: { title: string; hint?: string; children?: preact.ComponentChildren }) {
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

export function Segmented({ options, value, disabled, pick, labels }: { options: readonly string[]; value: string; disabled: boolean; pick: (value: string) => void; labels?: Record<string, string> }) {
  return (
    <div class="segmented" role="radiogroup">
      {options.map((option) => (
        <button key={option} role="radio" aria-checked={option === value} class={option === value ? "on" : ""} disabled={disabled} onClick={() => option !== value && pick(option)}>
          {labels?.[option] ?? EFFORT_LABELS[option] ?? option}
        </button>
      ))}
    </div>
  )
}

const SANDBOX_HINTS_RU: Record<string, string> = {
  auto: "Использует Bubblewrap, если он установлен",
  workspace: "Команды выполняются прямо на этом компьютере: без изоляции Bubblewrap, секреты не скрыты, папка .kvist не защищена",
  bubblewrap: "Команды оболочки блокируются, если Bubblewrap не найден",
}

interface EnvironmentProps {
  environment: Record<string, string>
  busy: boolean
  apply: (environment: Record<string, string>) => void
  russian?: boolean
  // Words instead of the variable's raw values, for people who have never seen a sandbox.
  plain?: boolean
}

// A choice equal to the core's default is not stored, so "auto" and "deny" leave the variable unset.
function chooseVariable(environment: Record<string, string>, apply: EnvironmentProps["apply"], name: string, value: string, fallback: string): void {
  const next = { ...environment }
  if (value === fallback) delete next[name]
  else next[name] = value
  apply(next)
}

export function SandboxRows({ environment, busy, apply, russian = false, plain = false }: EnvironmentProps) {
  const mode = environment[SANDBOX_MODE] ?? "auto"
  const net = environment[SANDBOX_NET] ?? "deny"
  const hints = russian ? SANDBOX_HINTS_RU : SANDBOX_HINTS
  const t = (en: string, ru: string) => (russian ? ru : en)
  const modeLabels = plain ? { auto: t("Auto", "Авто"), workspace: t("None", "Без изоляции"), bubblewrap: t("Strict", "Строго") } : undefined
  const netLabels = plain ? { deny: t("Blocked", "Запрещена"), allow: t("Allowed", "Разрешена") } : undefined
  return (
    <>
      <div class="row stack">
        <div class="row-title">{russian ? "Изоляция команд" : "Command sandbox"}</div>
        <Segmented options={SANDBOX_MODES} value={mode} labels={modeLabels} disabled={busy} pick={(value) => chooseVariable(environment, apply, SANDBOX_MODE, value, "auto")} />
        <div class={`row-hint ${mode === "workspace" ? "caution" : ""}`}>{hints[mode]}</div>
      </div>
      <div class="row stack">
        <div class="row-title">{russian ? "Сеть для команд" : "Network for commands"}</div>
        <Segmented options={SANDBOX_NETS} value={net} labels={netLabels} disabled={busy} pick={(value) => chooseVariable(environment, apply, SANDBOX_NET, value, "deny")} />
        <div class="row-hint">{russian ? "Работает только внутри Bubblewrap" : "Applies inside Bubblewrap only"}</div>
      </div>
    </>
  )
}

export function VariableRows({ environment, busy, apply, russian = false }: EnvironmentProps) {
  const [draft, setDraft] = useState<Variables>(customVariables(environment))
  useEffect(() => setDraft(customVariables(environment)), [JSON.stringify(environment)])

  const managed = (): Record<string, string> => Object.fromEntries(Object.entries(environment).filter(([name]) => name === SANDBOX_MODE || name === SANDBOX_NET))
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
    <div class="row stack">
      <div class="row-title">{russian ? "Переменные окружения" : "Other variables"}</div>
      {draft.map(([name, value], index) => (
        <div key={index} class="variable">
          <input class="field name" placeholder="NAME" value={name} disabled={busy} onInput={(event) => edit(index, 0, (event.target as HTMLInputElement).value)} />
          <input class="field" placeholder={russian ? "значение" : "value"} value={value} disabled={busy} onInput={(event) => edit(index, 1, (event.target as HTMLInputElement).value)} />
          <button class="icon" title={russian ? "Удалить" : "Remove"} disabled={busy} onClick={() => setDraft(draft.filter((_, i) => i !== index))}><Icon name="cross" /></button>
        </div>
      ))}
      {invalid ? <div class="row-hint caution">{russian ? "Имя может содержать буквы, цифры и подчёркивание и не начинается с цифры" : "Names may use letters, digits and underscores, and cannot start with a digit"}</div> : null}
      <div class="row-hint">
        {russian
          ? "Применяются при перезапуске ядра, он произойдёт сразу после «Применить». Хранятся открытым текстом, ключи API держите в настройках провайдера."
          : "Applied when the core restarts, which happens as soon as you apply. Stored in plain text, so keep API keys in the provider settings."}
      </div>
      <div class="buttons">
        <button class="btn" disabled={busy} onClick={() => setDraft([...draft, ["", ""]])}>{russian ? "Добавить переменную" : "Add variable"}</button>
        <button class="btn primary" disabled={busy || invalid || !changed} onClick={save}>{russian ? "Применить и перезапустить" : "Apply and restart"}</button>
      </div>
    </div>
  )
}

function Environment(props: EnvironmentProps) {
  return (
    <>
      <div class="group-title">Environment</div>
      <div class="group">
        <SandboxRows {...props} />
        <VariableRows {...props} />
      </div>
    </>
  )
}

export function Settings({ config, provider, role, effort, environment, busy, run, applyEnvironment, close }: Props) {
  useEffect(() => {
    post({ type: "getConfig" })
    post({ type: "getEnvironment" })
  }, [])
  const current = config && findProvider(config, provider)

  return (
    <div class="settings">
      <div class="settings-head">
        <button class="icon" title="Back" onClick={close}><Icon name="back" /></button>
        <span>Settings</span>
      </div>
      {busy ? <div class="row-hint pad">Changes are available when the agent is idle.</div> : null}

      <div class="group-title">Provider</div>
      <div class="group">
        <Row title={current?.label ?? (provider || "Not set")} hint={current && !current.has_credentials ? (current.auth_type === "oauth" ? "Continue with ChatGPT" : "API key is missing") : undefined}>
          <button class="btn" disabled={busy} onClick={() => run("provider")}>Change</button>
        </Row>
        <Row title="Models" hint="Fast and smart">
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
