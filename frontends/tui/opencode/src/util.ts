import os from "node:os"

export function abbreviateHome(directory: string, home = os.homedir()): string {
  if (directory === home) return "~"
  return directory.startsWith(home + "/") ? `~${directory.slice(home.length)}` : directory
}

// What the home screen suggests; every line names something this interface really has.
export const TIPS = [
  "Press Tab to cycle the role: auto, coder, reader, devops, chat",
  "Press Ctrl+P to open the command list",
  "Use /models to choose the fast and the smart model",
  "Press Ctrl+T to change how hard the model thinks",
  "Use /session to continue an earlier conversation",
  "Use /new to start a clean conversation; the old one stays",
  "Use /revert to undo the changes the agent made",
  "Use /themes to change the colours",
  "Press Esc twice to pause a run, Ctrl+C to stop everything",
  "Use /memory to see what Kvist remembers about this project",
  "Use /export to bring in sessions from Claude Code, Codex or OpenCode",
  "Type @ to mention a file in your request",
] as const

export function pickTip(random: () => number = Math.random): string {
  return TIPS[Math.floor(random() * TIPS.length)] ?? TIPS[0]
}

// Token counts the way the footer shows them: 1.2K, 3.4M.
export function compact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1000) return `${(value / 1000).toFixed(1)}K`
  return String(value)
}
