#!/usr/bin/env node
import { render } from "ink"
import { writeFileSync } from "node:fs"
import path from "node:path"
import { parseArgs } from "node:util"

import { resolveCoreCommand } from "./core.js"
import { farewellText } from "./farewell.js"
import { kittyGraphics } from "./kitty.js"
import { probeTerminal } from "./probe.js"
import { Pointer } from "./pointer.js"
import { sessionOp } from "./session.js"
import { starterSet } from "./starters.js"
import { renderWithRawInput } from "./terminal.js"
import { routeWarningsToLog } from "./warnings.js"
import { App } from "./ui/app.js"
import { SKINS, applySkin } from "./ui/theme.js"

// Exit codes the launcher (kernel/main.py) reads: 64 applies the interface written to
// KVIST_SWITCH_FILE, 65 carries on in the classic terminal.
const SETUP_SWITCH_CODE = 64
const SETUP_CLASSIC_CODE = 65
// A provider-only setup that saved a provider.
const SETUP_DONE_CODE = 66

const { values } = parseArgs({
  options: {
    workspace: { type: "string" },
    resume: { type: "string" },
    role: { type: "string", default: "auto" },
    provider: { type: "string" },
    "fast-model": { type: "string" },
    "smart-model": { type: "string" },
    "approval-mode": { type: "string", default: "ask" },
    skin: { type: "string", default: "ink" },
    // First-run setup instead of the agent: the launcher passes it when no provider is saved.
    setup: { type: "boolean", default: false },
    // With --setup: only configure a provider (the ACP login), no folder and no interface choice.
    "setup-provider-only": { type: "boolean", default: false },
  },
})

const skin = SKINS.find((name) => name === values.skin)
if (!skin) {
  console.error(`Unknown skin '${values.skin}'; expected one of: ${SKINS.join(", ")}.`)
  process.exit(2)
}
applySkin(skin)

if (!process.stdin.isTTY || !process.stdout.isTTY) {
  console.error("kvist-tui needs an interactive terminal.")
  process.exit(2)
}

if (values.setup) {
  const { runSetup } = await import("./setup/run.js")
  const { installScriptFromEnv } = await import("./setup/ops.js")
  const outcome = await runSetup({
    command: resolveCoreCommand(),
    // Set by the release installer's bootstrap: the screen then starts with the install choice.
    install: installScriptFromEnv(process.env),
    workspace: path.resolve(values.workspace ?? process.cwd()),
    scope: values["setup-provider-only"] ? "provider" : "full",
  })
  if (outcome.kind === "done") process.exit(SETUP_DONE_CODE)
  if (outcome.kind === "frontend") {
    // The launcher saves the choice and starts that interface; without one there is nobody to tell.
    const target = process.env.KVIST_SWITCH_FILE
    if (!target) {
      console.log(`Setup finished. Start Kvist again to use the '${outcome.value}' interface.`)
      process.exit(0)
    }
    // The interface on the first line, the folder to work in on the second.
    writeFileSync(target, `${outcome.value}\n${outcome.workspace}\n`, "utf8")
    process.exit(SETUP_SWITCH_CODE)
  }
  // The classic terminal has the full provider catalog; 0 is the person leaving.
  if (outcome.kind === "classic" && process.env.KVIST_SWITCH_FILE) writeFileSync(process.env.KVIST_SWITCH_FILE, `\n${outcome.workspace}\n`, "utf8")
  process.exit(outcome.kind === "classic" ? SETUP_CLASSIC_CODE : 0)
}

const workspace = path.resolve(values.workspace ?? process.cwd())
routeWarningsToLog(path.join(workspace, ".kvist", "logs", "tui.log"))
const command = resolveCoreCommand()
const models =
  values["fast-model"] && values["smart-model"]
    ? { fast: values["fast-model"], smart: values["smart-model"] }
    : undefined

// A launch always opens its own session (UI_API.md §8a).
const launched = values.resume === undefined
  ? await sessionOp(command, workspace, "launch")
  : values.resume
    ? await sessionOp(command, workspace, "switch", values.resume)
    : { ok: true }
if (!launched.ok) {
  console.error(`session launch failed: ${launched.error ?? "unknown error"}`)
  process.exit(2)
}

// The claude skin takes the whole screen and the mouse (see pointer.ts); the ink
// skin keeps the terminal's scrollback and native selection.
const fullscreen = skin === "claude"
// kitty's protocol is recognised from the environment; Sixel has to be asked for, before Ink owns the input.
const probe = fullscreen && !kittyGraphics(process.env) ? await probeTerminal(process.stdin, process.stdout) : null
const pictures = kittyGraphics(process.env) ? "kitty" : probe?.sixel ? "sixel" : null
const pointer = fullscreen ? new Pointer(process.stdin, process.stdout, { pictures, cell: probe?.cell ?? null }) : null
let lastSession = ""

const app = renderWithRawInput(process.stdin, () => render(
  <App
    command={command}
    workspace={workspace}
    resume={values.resume}
    provider={values.provider}
    models={models}
    role={values.role ?? "auto"}
    approvalMode={values["approval-mode"] ?? "ask"}
    skin={skin}
    pointer={pointer}
    starters={values.resume === undefined ? starterSet(workspace) : null}
    onSession={(name) => (lastSession = name)}
  />,
  pointer
    ? { exitOnCtrlC: false, alternateScreen: true, stdin: pointer.stdin, stdout: pointer.stdout, kittyKeyboard: { mode: "auto" } }
    : { exitOnCtrlC: false, kittyKeyboard: { mode: "auto" } },
))
pointer?.start()
await app.waitUntilExit()
pointer?.stop()
if (fullscreen) {
  // The alternate screen is gone; leave a way back. A session nothing happened in has
  // nothing to come back to: the core reports an empty journal and no checkpoint for it.
  const listed = lastSession ? await sessionOp(command, workspace, "list") : null
  const used = listed?.session_items?.find((item) => item.name === lastSession)
  const resumable = used && (used.journal_bytes > 0 || used.has_checkpoint) ? lastSession : null
  process.stdout.write(farewellText(resumable, process.stdout.columns ?? 80))
}
// exitCode is set by /tui so the launcher can tell a switch from a normal quit.
process.exit()
