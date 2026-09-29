// Copies plain text to the user's clipboard. Tries the desktop's own utility
// first (Wayland/X11/macOS/Windows); when none is available (e.g. a bare SSH
// session with no X/Wayland forwarding), falls back to an OSC 52 escape
// sequence written straight to the terminal, which works over SSH too.
import { spawnSync } from "node:child_process"

export type ClipboardMethod = "wl-copy" | "xclip" | "pbcopy" | "clip.exe" | "osc52"

export interface ClipboardResult {
  method: ClipboardMethod
  chars: number
}

export interface ClipboardTarget {
  method: ClipboardMethod
  command: string
  args: string[]
}

type Spawner = (command: string, args: string[], input: string) => boolean

function defaultSpawn(command: string, args: string[], input: string): boolean {
  const result = spawnSync(command, args, { input, stdio: ["pipe", "ignore", "ignore"] })
  return result.status === 0
}

// Ordered by what this session would actually use: a Wayland compositor
// before X11 when both display variables happen to be set, then the
// platform's own single utility on macOS/Windows.
export function candidateTargets(platform: NodeJS.Platform, env: NodeJS.ProcessEnv): ClipboardTarget[] {
  if (platform === "darwin") return [{ method: "pbcopy", command: "pbcopy", args: [] }]
  if (platform === "win32") return [{ method: "clip.exe", command: "clip.exe", args: [] }]
  const targets: ClipboardTarget[] = []
  if (env.WAYLAND_DISPLAY) targets.push({ method: "wl-copy", command: "wl-copy", args: [] })
  if (env.DISPLAY) targets.push({ method: "xclip", command: "xclip", args: ["-selection", "clipboard"] })
  return targets
}

export function oscCopySequence(text: string): string {
  const payload = Buffer.from(text, "utf8").toString("base64")
  return `\x1b]52;c;${payload}\x07`
}

export function copyToClipboard(
  text: string,
  options: {
    platform?: NodeJS.Platform
    env?: NodeJS.ProcessEnv
    spawn?: Spawner
    writeOsc52?: (sequence: string) => void
  } = {},
): ClipboardResult {
  const platform = options.platform ?? process.platform
  const env = options.env ?? process.env
  const spawn = options.spawn ?? defaultSpawn
  const writeOsc52 = options.writeOsc52 ?? ((sequence: string) => process.stdout.write(sequence))

  for (const target of candidateTargets(platform, env)) {
    let ok: boolean
    try {
      ok = spawn(target.command, target.args, text)
    } catch {
      ok = false
    }
    if (ok) return { method: target.method, chars: text.length }
  }
  writeOsc52(oscCopySequence(text))
  return { method: "osc52", chars: text.length }
}
