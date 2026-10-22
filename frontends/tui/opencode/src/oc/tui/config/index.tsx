// Kvist: OpenCode's TUI reads its settings from the server's config service. Here they are one small
// file of this interface (`$KVIST_CONFIG_DIR|~/.kvist/opencode-tui.json`) in the same shape OpenCode's
// `tui.json` has, so the copied components read `config.data.*` unchanged.

export * as Config from "."

import { createBindingLookup } from "@opentui/keymap/extras"
import { createContext, useContext, type JSX } from "solid-js"
import { createStore, reconcile } from "solid-js/store"
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import { TuiKeybind } from "./keybind"

export type MiniWorkSpinner =
  | "block-soft-slide" | "block-soft-sweep" | "block-low-comet" | "block-low-duet" | "block-shuttle" | "block-bridge"
  | "block-squeeze" | "small-toggle" | "square-toggle" | "grow-shrink" | "quadrant-orbit" | "crosshatch" | "density-wave" | "seed"

export interface Info {
  keybinds?: TuiKeybind.KeybindOverrides
  leader?: { timeout?: number }
  theme?: { name?: string; mode?: "system" | "dark" | "light" }
  animations?: boolean
  mouse?: boolean
  cursor?: { style?: "block" | "underline" | "line" | "default"; blinking?: boolean }
  scroll?: { speed?: number; acceleration?: boolean }
  session?: { sidebar?: "auto" | "hide"; scrollbar?: boolean; thinking?: "show" | "hide"; tps?: boolean; permissions?: "prompt" | "autoaccept" }
  diffs?: { wrap?: "word" | "none"; view?: "auto" | "split" | "unified" }
  terminal?: { title?: boolean; copy?: "manual" | "select" }
  prompt?: Record<string, unknown>
  experimental?: Record<string, boolean>
}

export const DEFAULTS: Info = {
  theme: { name: "kvist", mode: "system" },
  animations: true,
  mouse: true,
  session: { sidebar: "auto", scrollbar: false, thinking: "show" },
  diffs: { wrap: "word", view: "auto" },
  terminal: { title: true, copy: "select" },
}

export function configFile(env: NodeJS.ProcessEnv = process.env): string {
  return path.join(env.KVIST_CONFIG_DIR || path.join(os.homedir(), ".kvist"), "opencode-tui.json")
}

function merge(base: Info, extra: Info): Info {
  const result: Record<string, unknown> = { ...base }
  for (const [key, value] of Object.entries(extra)) {
    const current = result[key]
    result[key] =
      typeof value === "object" && value !== null && !Array.isArray(value) && typeof current === "object" && current !== null
        ? { ...(current as object), ...(value as object) }
        : value
  }
  return result as Info
}

export function loadConfig(file = configFile()): Info {
  let text: string
  try {
    text = readFileSync(file, "utf8")
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return DEFAULTS
    throw error
  }
  try {
    return merge(DEFAULTS, JSON.parse(text) as Info)
  } catch (error) {
    if (error instanceof SyntaxError) return DEFAULTS
    throw error
  }
}

// Write-then-rename so a crash never leaves half a file.
export function saveConfig(info: Info, file = configFile()): void {
  mkdirSync(path.dirname(file), { recursive: true })
  const scratch = `${file}.${process.pid}.tmp`
  writeFileSync(scratch, JSON.stringify(info, null, 2) + "\n", "utf8")
  renameSync(scratch, file)
}

export type Resolved = Omit<Info, "keybinds" | "leader" | "mouse" | "cursor"> & {
  keybinds: TuiKeybind.BindingLookupView
  leader: { timeout: number }
  mouse: boolean
  cursor?: { style: "block" | "underline" | "line" | "default"; blinking: boolean }
}

export function resolve(input: Info): Resolved {
  return {
    ...input,
    keybinds: createBindingLookup(TuiKeybind.toBindingConfig(TuiKeybind.parse({ ...input.keybinds })), {
      bindingDefaults: TuiKeybind.bindingDefaults(),
    }),
    leader: { timeout: input.leader?.timeout ?? 2000 },
    mouse: input.mouse ?? true,
    cursor: input.cursor ? { style: input.cursor.style ?? "block", blinking: input.cursor.blinking ?? true } : undefined,
  }
}

interface ConfigContextValue {
  data: Resolved
  update(change: (draft: Info) => void): Promise<Info>
}

const ConfigContext = createContext<ConfigContextValue>()

export function ConfigProvider(props: { file?: string; initial?: Info; children: JSX.Element }) {
  let info: Info = props.initial ?? loadConfig(props.file)
  const [data, setData] = createStore<Resolved>(resolve(info))
  const update: ConfigContextValue["update"] = async (change) => {
    const draft = JSON.parse(JSON.stringify(info)) as Info
    change(draft)
    info = draft
    setData(reconcile(resolve(draft)))
    saveConfig(draft, props.file)
    return draft
  }
  return <ConfigContext.Provider value={{ data, update }}>{props.children}</ConfigContext.Provider>
}

export function useConfig() {
  const value = useContext(ConfigContext)
  if (!value) throw new Error("ConfigProvider is missing")
  return value
}

/** Reactive accessor for OpenCode's temporary "new-designs" experiment: always off here. */
export function useNewDesigns() {
  const config = useConfig()
  return () => config.data.experimental?.["new-designs"] === true
}
