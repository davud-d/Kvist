// The commands of the palette (ctrl+p), the slash list and the key bindings. OpenCode's own are
// here under its ids (so its default keys apply) and its titles; each does what the nearest Kvist
// feature does. The ones OpenCode has no name for are `kvist.*`.

import { writeFileSync } from "node:fs"
import type { JSX } from "solid-js"

import { FRONTENDS, sessionArgs } from "../../ink/commands"
import { answerFromEnd } from "../../ink/state"
import { DialogThemeList } from "./oc/tui/component/dialog-theme-list"
import type { KeymapCommand } from "./oc/tui/context/keymap"
import type { useThemes } from "./oc/tui/context/theme"
import type { useConfig } from "./oc/tui/config"
import type { DialogContext } from "./oc/tui/ui/dialog"
import { DialogSelect } from "./oc/tui/ui/dialog-select"
import type { ToastContext } from "./oc/tui/ui/toast"
import type { Kvist } from "./kvist"
import { CommandPaletteDialog } from "./screens/palette"
import { DialogAgent, DialogHelp, DialogModel, DialogSessionList, DialogSessionRename, DialogStatus, DialogVariant } from "./screens/dialogs"
import { ROLE_ORDER } from "./screens/roles"
import { toggleSidebar } from "./screens/sidebar"

export interface CommandDeps {
  kvist: Kvist
  dialog: DialogContext
  themes: ReturnType<typeof useThemes>
  config: ReturnType<typeof useConfig>
  toast: ToastContext
  // Ends the process; 64 asks the launcher to start another interface.
  quit(code?: number): void
  // Puts text on the clipboard.
  copy(text: string): Promise<void>
  columns(): number
}

const SWITCH_CODE = 64

// What the core can do that OpenCode has no name for, reachable under the name the core knows.
// `aliases` are the names OpenCode gives its nearest command.
const CORE_COMMANDS: { name: string; title: string; group: string; aliases?: string[] }[] = [
  { name: "resume", title: "Continue interrupted run", group: "Session" },
  { name: "changes", title: "Show changes", group: "Session", aliases: ["diff"] },
  { name: "export", title: "Import sessions from other agents", group: "Session" },
  { name: "context", title: "Show context size", group: "Session", aliases: ["stats"] },
  { name: "memory", title: "What Kvist remembers", group: "Session" },
  { name: "style", title: "Working style", group: "Agent" },
  { name: "provider", title: "Switch or configure provider", group: "Provider" },
  { name: "change-api", title: "Change API key", group: "Provider" },
  { name: "settings-api", title: "Request settings", group: "Provider", aliases: ["settings"] },
  { name: "strime", title: "Streaming for a custom provider", group: "Provider" },
  { name: "del-provider", title: "Delete the custom provider", group: "Provider" },
  { name: "full-reset", title: "Erase every saved provider and key", group: "Provider" },
  { name: "web-search-provider", title: "Web search provider", group: "Provider" },
  { name: "verbose", title: "Toggle verbose mode", group: "System" },
]

export function createCommands(deps: CommandDeps): KeymapCommand[] {
  const { kvist, dialog, themes, config, toast } = deps
  const core = (name: string, args: string[] = []) => void kvist.run(name, args).catch((error: Error) => kvist.note(error.message, "warn"))
  const open = (render: () => JSX.Element) => dialog.replace(render)
  const words = (input: string | undefined) => (input?.trim() ? input.trim().split(/\s+/) : [])

  const cycle = <T,>(values: readonly T[], current: T, step: 1 | -1): T | undefined => {
    const at = Math.max(0, values.indexOf(current))
    return values[(at + step + values.length) % values.length]
  }

  const cycleRole = (step: 1 | -1) => {
    const roles = (kvist.manifest()?.roles.map((role) => role.value) ?? [...ROLE_ORDER]) as string[]
    const next = cycle(roles, kvist.state().info.role || "auto", step)
    if (next) core("role", [next])
  }

  const cycleEffort = () => {
    const levels = (kvist.manifest()?.effort_levels.map((level) => level.value) ?? ["false", "low", "medium", "high", "xhigh", "max"]) as string[]
    const next = cycle(levels, kvist.state().effort, 1)
    if (next) core("effort", [next])
  }

  const copyLast = async (input?: string) => {
    const fromEnd = Math.max(1, Number.parseInt(input?.trim() ?? "1", 10) || 1)
    const text = answerFromEnd(kvist.state().items, fromEnd)
    if (!text) return toast.show({ message: "No answer to copy yet.", variant: "warning", duration: 3000 })
    await deps.copy(text)
    toast.show({ message: `Copied ${text.length} character${text.length === 1 ? "" : "s"} to the clipboard`, variant: "success", duration: 2500 })
  }

  const chooseInterface = () =>
    open(() => (
      <DialogSelect
        title="Terminal interface"
        current={kvist.frontend}
        options={FRONTENDS.map((frontend) => ({ title: frontend.label, value: frontend.value, description: frontend.description }))}
        onSelect={(option) => {
          dialog.clear()
          if (option.value === kvist.frontend) return
          const target = process.env.KVIST_SWITCH_FILE
          if (!target) return toast.show({ message: "Switching needs Kvist to be started through its launcher (kvist).", variant: "warning" })
          try {
            writeFileSync(target, option.value, "utf8")
          } catch (error) {
            return toast.show({ message: `Could not record the choice: ${error instanceof Error ? error.message : String(error)}`, variant: "error" })
          }
          deps.quit(SWITCH_CODE)
        }}
      />
    ))

  // Names a slash command is typed with, so the core's own list can skip them.
  const typed = new Set<string>()
  const named = (command: KeymapCommand & { id: string }): KeymapCommand => {
    if (command.slash) {
      typed.add(command.slash.name)
      for (const alias of command.slash.aliases ?? []) typed.add(alias)
    }
    return { bind: false, palette: true, ...command }
  }

  const own: KeymapCommand[] = [
    { id: "command.palette.show", title: "Show command palette", group: "System", bind: false, run: () => open(() => <CommandPaletteDialog />) },
    named({ id: "session.list", title: "Switch session", group: "Session", slash: { name: "sessions", aliases: ["session", "continue"] }, run: (input) => (input?.trim() ? core("session", sessionArgs(words(input))) : open(() => <DialogSessionList />)) }),
    named({ id: "session.new", title: "New session", group: "Session", slash: { name: "new" }, run: () => core("new") }),
    named({ id: "session.rename", title: "Rename session", group: "Session", slash: { name: "rename" }, run: (input) => (input?.trim() ? core("rename", words(input)) : open(() => <DialogSessionRename />)) }),
    named({ id: "session.undo", title: "Undo changes", group: "Session", slash: { name: "undo", aliases: ["revert"] }, run: () => core("revert") }),
    named({ id: "messages.copy", title: "Copy last answer", group: "Session", slash: { name: "copy" }, run: copyLast }),
    named({ id: "agent.list", title: "Switch agent", group: "Agent", slash: { name: "agents", aliases: ["role", "roles"] }, run: (input) => (input?.trim() ? core("role", [input.trim()]) : open(() => <DialogAgent />)) }),
    named({ id: "agent.cycle", title: "Next agent", group: "Agent", run: () => cycleRole(1) }),
    named({ id: "agent.cycle.reverse", title: "Previous agent", group: "Agent", run: () => cycleRole(-1) }),
    named({ id: "variant.cycle", title: "Cycle reasoning effort", group: "Agent", run: cycleEffort }),
    named({ id: "variant.list", title: "Select reasoning effort", group: "Agent", slash: { name: "effort" }, run: (input) => (input?.trim() ? core("effort", [input.trim()]) : open(() => <DialogVariant />)) }),
    named({ id: "model.list", title: "Switch model", group: "Agent", slash: { name: "models", aliases: ["model"] }, run: (input) => (input?.trim() ? core("models", words(input)) : open(() => <DialogModel />)) }),
    named({ id: "provider.connect", title: "Connect provider", group: "Provider", slash: { name: "connect" }, run: () => core("provider") }),
    named({
      id: "theme.switch",
      title: "Switch theme",
      group: "System",
      slash: { name: "themes", aliases: ["theme"] },
      run: (input) => (input?.trim() && themes.set(input.trim()) ? undefined : open(() => <DialogThemeList />)),
    }),
    named({
      id: "theme.switch_mode",
      title: "Switch between light and dark",
      group: "System",
      run: () => {
        themes.setMode(themes.mode() === "dark" ? "light" : "dark")
        dialog.clear()
      },
    }),
    named({
      id: "session.sidebar.toggle",
      title: "Toggle sidebar",
      group: "Session",
      run: () => toggleSidebar(config, deps.columns()),
    }),
    named({
      id: "session.toggle.thinking",
      title: "Toggle thinking blocks visibility",
      group: "Session",
      slash: { name: "thinking" },
      run: () => {
        void config.update((draft) => {
          draft.session = { ...draft.session, thinking: config.data.session?.thinking === "hide" ? "show" : "hide" }
        })
      },
    }),
    named({ id: "opencode.status", title: "View status", group: "System", slash: { name: "status" }, run: () => open(() => <DialogStatus />) }),
    named({ id: "help.show", title: "Help", group: "System", slash: { name: "help" }, run: () => open(() => <DialogHelp />) }),
    named({ id: "kvist.debug", title: "Show or hide debug events", group: "System", slash: { name: "debug" }, run: () => kvist.dispatch({ type: "toggle_debug" }) }),
    named({ id: "kvist.clear", title: "Clear the screen", group: "System", slash: { name: "clear" }, run: () => kvist.dispatch({ type: "clear" }) }),
    named({ id: "kvist.tui", title: "Choose the terminal interface", group: "System", slash: { name: "tui" }, run: chooseInterface }),
    named({ id: "app.exit", title: "Exit the app", group: "System", slash: { name: "exit", aliases: ["quit", "q", "bye"] }, run: () => deps.quit(0) }),
  ]

  const rest = CORE_COMMANDS.filter((command) => !typed.has(command.name)).map((command) =>
    named({ id: `kvist.${command.name}`, title: command.title, group: command.group, slash: { name: command.name, aliases: command.aliases }, run: (input) => core(command.name, words(input)) }),
  )
  return [...own, ...rest]
}
