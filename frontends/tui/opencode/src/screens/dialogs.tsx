// The pickers of OpenCode's dialogs (sessions, agents, models, variants) and its status and
// help dialogs, filled from the Kvist core. The dialogs themselves are OpenCode's.

import { createMemo, createResource, For, Show } from "solid-js"

import { contextLabel, englishOnly } from "../../../ink/format"
import { sessionOp, type SessionStatus } from "../../../ink/session"
import { Keymap } from "../oc/tui/context/keymap"
import { useTheme } from "../oc/tui/context/theme"
import { DialogHeader, DialogLayout, useDialog } from "../oc/tui/ui/dialog"
import { DialogPrompt } from "../oc/tui/ui/dialog-prompt"
import { DialogSelect, type DialogSelectOption } from "../oc/tui/ui/dialog-select"
import { useToast } from "../oc/tui/ui/toast"
import { useKvist } from "../kvist"
import { abbreviateHome } from "../util"
import { ROLE_ORDER, titlecase } from "./roles"

export interface SessionItem {
  name: string
  title: string
  updated_at: number
  has_checkpoint: boolean
  journal_bytes: number
  closed?: boolean
}

type Listing = SessionStatus & { session_items?: SessionItem[] }

// "just now", "5m ago", "3h ago", "2d ago": the footer of a session row.
export function ago(epochSeconds: number, now = Date.now()): string {
  const seconds = Math.max(0, Math.round(now / 1000 - epochSeconds))
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

// Sessions nothing happened in are not worth a row; the one on screen is marked.
export function sessionOptions(items: readonly SessionItem[], active: string): DialogSelectOption<string>[] {
  return items
    .filter((item) => item.journal_bytes > 0 || item.has_checkpoint || item.name === active)
    .map((item) => ({
      title: englishOnly(item.title || item.name),
      value: item.name,
      footer: ago(item.updated_at),
      category: item.name === active ? "Current" : "Sessions",
    }))
}

export function DialogSessionList() {
  const kvist = useKvist()
  const dialog = useDialog()
  const theme = useTheme().surface("dialog")
  const [listing] = createResource(async () => (await sessionOp(kvist.command, kvist.workspace, "list")) as Listing)

  const choose = (name: string) => {
    dialog.clear()
    if (name === kvist.state().info.session) return
    void kvist.run("session", ["switch", name]).catch((error: Error) => kvist.note(error.message, "warn"))
  }

  return (
    <Show
      when={!listing.loading}
      fallback={
        <DialogLayout header={<DialogHeader title="Sessions" />}>
          <text fg={theme.text.muted}>Loading sessions…</text>
        </DialogLayout>
      }
    >
      <DialogSelect
        title="Sessions"
        placeholder="Search"
        options={sessionOptions(listing()?.session_items ?? [], kvist.state().info.session)}
        current={kvist.state().info.session}
        onSelect={(option) => choose(option.value)}
      />
    </Show>
  )
}

export function DialogAgent() {
  const kvist = useKvist()
  const dialog = useDialog()
  const options = createMemo(() => {
    const roles = kvist.manifest()?.roles ?? ROLE_ORDER.map((value) => ({ value, description: "" }))
    return roles.map((role) => ({ value: role.value, title: role.value, description: englishOnly(role.description) }))
  })
  return (
    <DialogSelect
      title="Select agent"
      placeholder="Search"
      current={kvist.state().info.role || "auto"}
      options={options()}
      onSelect={(option) => {
        dialog.clear()
        void kvist.run("role", [option.value]).catch((error: Error) => kvist.note(error.message, "warn"))
      }}
    />
  )
}

export function DialogVariant() {
  const kvist = useKvist()
  const dialog = useDialog()
  const options = createMemo(() => {
    const levels = kvist.manifest()?.effort_levels ?? ["false", "low", "medium", "high", "xhigh", "max"].map((value) => ({ value, description: "" }))
    return levels.map((level) => ({ value: level.value, title: level.value === "false" ? "off" : level.value, description: englishOnly(level.description) }))
  })
  return (
    <DialogSelect
      title="Select reasoning effort"
      placeholder="Search"
      current={kvist.state().effort}
      options={options()}
      onSelect={(option) => {
        dialog.clear()
        void kvist.run("effort", [option.value]).catch((error: Error) => kvist.note(error.message, "warn"))
      }}
    />
  )
}

interface CatalogProvider {
  provider: string
  label: string
  current: boolean
  chosen: { fast: string; smart: string }
  models: string[]
}

// A model picked in the list says which of the two it becomes.
function DialogUseAs(props: { provider: CatalogProvider; model: string }) {
  const kvist = useKvist()
  const dialog = useDialog()
  const toast = useToast()
  const apply = (fast: string, smart: string) => {
    dialog.clear()
    kvist
      .run("models", [props.provider.provider, fast, smart])
      .then((result) => toast.show({ message: result.ok ? `Using ${props.model}` : englishOnly(result.message), variant: result.ok ? "success" : "error" }))
      .catch((error: Error) => kvist.note(error.message, "warn"))
  }
  const { chosen } = props.provider
  return (
    <DialogSelect
      title={`Use ${props.model} as`}
      options={[
        { title: "Fast model", value: "fast", description: "routine steps, answers" },
        { title: "Smart model", value: "smart", description: "hard steps, after errors" },
        { title: "Both", value: "both" },
      ]}
      onSelect={(option) => {
        if (option.value === "fast") return apply(props.model, chosen.smart)
        if (option.value === "smart") return apply(chosen.fast, props.model)
        apply(props.model, props.model)
      }}
    />
  )
}

export function DialogModel() {
  const kvist = useKvist()
  const dialog = useDialog()
  const theme = useTheme().surface("dialog")
  const [catalog] = createResource(async () => {
    const result = await kvist.run("catalog")
    return (result.effects.providers as CatalogProvider[] | undefined) ?? []
  })

  const options = (): DialogSelectOption<{ provider: CatalogProvider; model: string }>[] =>
    (catalog() ?? []).flatMap((provider) =>
      provider.models.map((model) => ({
        title: model,
        value: { provider, model },
        category: provider.label,
        footer: [provider.chosen.fast === model ? "fast" : "", provider.chosen.smart === model ? "smart" : ""].filter(Boolean).join(" · ") || undefined,
      })),
    )

  const current = () => {
    const active = catalog()?.find((provider) => provider.current)
    return active ? options().find((option) => option.value.provider === active && option.value.model === active.chosen.fast)?.value : undefined
  }

  return (
    <Show
      when={!catalog.loading}
      fallback={
        <DialogLayout header={<DialogHeader title="Select model" />}>
          <text fg={theme.text.muted}>Loading models…</text>
        </DialogLayout>
      }
    >
      <DialogSelect
        title="Select model"
        placeholder="Search"
        options={options()}
        current={current()}
        onSelect={(option) => dialog.replace(() => <DialogUseAs provider={option.value.provider} model={option.value.model} />)}
      />
    </Show>
  )
}

export function DialogSessionRename() {
  const kvist = useKvist()
  const dialog = useDialog()
  return (
    <DialogPrompt
      title="Rename session"
      placeholder="Session title"
      value={kvist.state().info.title ?? ""}
      onConfirm={(value) => {
        dialog.clear()
        if (value.trim()) void kvist.run("rename", [value.trim()]).catch((error: Error) => kvist.note(error.message, "warn"))
      }}
      onCancel={() => dialog.clear()}
    />
  )
}

function StatusRow(props: { label: string; value: string }) {
  const theme = useTheme().surface("dialog")
  return (
    <box flexDirection="row" gap={1}>
      <text fg={theme.text.muted} width={11} flexShrink={0}>{props.label}</text>
      <text fg={theme.text.base} wrapMode="none" truncate>{props.value}</text>
    </box>
  )
}

export function DialogStatus() {
  const kvist = useKvist()
  const info = () => kvist.state().info
  const rows = (): [string, string][] => [
    ["Version", info().kvistVersion ?? "unknown"],
    ["Workspace", abbreviateHome(kvist.workspace)],
    ["Session", info().title || info().session || "-"],
    ["Role", info().role || "auto"],
    ["Provider", info().provider || "-"],
    ["Model", info().model || "-"],
    ["Effort", kvist.state().effort || "-"],
    ["Context", contextLabel(kvist.state().usage.context, kvist.state().usage.limit) || "-"],
  ]
  return (
    <DialogLayout header={<DialogHeader title="Status" />}>
      <box>
        <For each={rows()}>{([label, value]) => <StatusRow label={label} value={value} />}</For>
      </box>
    </DialogLayout>
  )
}

export function DialogHelp() {
  const theme = useTheme().surface("dialog")
  const shortcuts = Keymap.useShortcuts()
  const commands = Keymap.useCommands()
  const rows = createMemo(() =>
    commands()
      .filter((command) => command.id && command.title && shortcuts.all(command.id))
      .map((command) => [shortcuts.all(command.id!)!, command.title!] as const),
  )
  return (
    <DialogLayout header={<DialogHeader title="Help" />}>
      <box>
        <For each={rows()}>
          {([keys, title]) => (
            <box flexDirection="row" gap={1}>
              <text fg={theme.text.base} width={22} flexShrink={0} wrapMode="none" truncate>{keys}</text>
              <text fg={theme.text.muted} wrapMode="none" truncate>{title}</text>
            </box>
          )}
        </For>
      </box>
    </DialogLayout>
  )
}

export { titlecase }
