// Mounts the whole app against the Ink suite's scripted stand-in for the core, so a test
// drives what a person would: type, press keys, read the frame.

import type { KeyInput } from "@opentui/core/testing"
import { testRender } from "@opentui/solid"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"

import { App } from "../src/app.js"

const FAKE_CORE = path.join(import.meta.dirname, "..", "..", "ink", "tests", "fixtures", "fake-core.mjs")

export interface Mounted {
  frame(): string
  type(text: string): Promise<void>
  press(key: string, modifiers?: { ctrl?: boolean; shift?: boolean; meta?: boolean }): void
  enter(): void
  // Renders until `text` is on screen; fails with the frame when it never shows.
  waitFor(text: string | RegExp, timeoutMs?: number): Promise<void>
  settle(ms?: number): Promise<void>
  quitCodes: number[]
  // The folder the core was started in.
  workspace: string
  destroy(): void
}

// The names a test uses for the keys the mock input knows by their escape sequences.
const KEY_NAMES: Record<string, KeyInput> = {
  escape: "ESCAPE", tab: "TAB", return: "RETURN", backspace: "BACKSPACE", delete: "DELETE",
  up: "ARROW_UP", down: "ARROW_DOWN", left: "ARROW_LEFT", right: "ARROW_RIGHT", home: "HOME", end: "END",
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

export interface MountOptions {
  width?: number
  height?: number
  env?: Record<string, string>
  // Files put in the workspace before the app starts, by relative path.
  files?: Record<string, string>
  // The interface's saved preferences.
  prefs?: Record<string, unknown>
}

export async function mount(options: MountOptions = {}): Promise<Mounted> {
  const root = mkdtempSync(path.join(os.tmpdir(), "kvist-opencode-"))
  for (const [relative, content] of Object.entries(options.files ?? {})) {
    mkdirSync(path.dirname(path.join(root, relative)), { recursive: true })
    writeFileSync(path.join(root, relative), content)
  }
  if (options.prefs) {
    mkdirSync(path.join(root, "config"), { recursive: true })
    writeFileSync(path.join(root, "config", "opencode-tui.json"), JSON.stringify(options.prefs))
  }
  const saved: Record<string, string | undefined> = {}
  const env: Record<string, string> = {
    KVIST_CORE_CMD: JSON.stringify(["node", FAKE_CORE]),
    KVIST_CONFIG_DIR: path.join(root, "config"),
    KVIST_SWITCH_FILE: path.join(root, "switch"),
    ...options.env,
  }
  for (const [key, value] of Object.entries(env)) {
    saved[key] = process.env[key]
    process.env[key] = value
  }
  const quitCodes: number[] = []
  const { resolveCoreCommand } = await import("../../ink/core.js")
  const setup = await testRender(
    () => (
      <App
        terminalMode="dark"
        options={{ command: resolveCoreCommand(), frontend: "opencode", core: { workspace: root, role: "auto", approvalMode: "ask" } }}
        onQuit={(code) => quitCodes.push(code)}
      />
    ),
    { width: options.width ?? 100, height: options.height ?? 30 },
  )
  await setup.renderOnce()

  const frame = () => setup.captureCharFrame()
  const waitFor = async (text: string | RegExp, timeoutMs = 6000) => {
    const started = Date.now()
    for (;;) {
      await setup.renderOnce()
      const shown = frame()
      if (typeof text === "string" ? shown.includes(text) : text.test(shown)) return
      if (Date.now() - started > timeoutMs) throw new Error(`Timed out waiting for ${String(text)}. Frame:\n${shown}`)
      await sleep(30)
    }
  }
  return {
    frame,
    type: async (text) => {
      await setup.mockInput.typeText(text)
    },
    press: (key, modifiers) => setup.mockInput.pressKey(KEY_NAMES[key] ?? key, modifiers),
    enter: () => setup.mockInput.pressEnter(),
    waitFor,
    settle: async (ms = 100) => {
      await sleep(ms)
      await setup.renderOnce()
    },
    quitCodes,
    workspace: root,
    destroy: () => {
      setup.renderer.destroy()
      for (const [key, value] of Object.entries(saved)) {
        if (value === undefined) delete process.env[key]
        else process.env[key] = value
      }
      rmSync(root, { recursive: true, force: true })
    },
  }
}
