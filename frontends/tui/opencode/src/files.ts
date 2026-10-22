// The files `@` offers: what the project holds, minus what Kvist never lets the agent read.

import { execFile } from "node:child_process"
import { readdir } from "node:fs/promises"
import path from "node:path"

const MAX_FILES = 5000
const SKIPPED_DIRECTORIES = new Set([".git", "node_modules", ".kvist", "dist", "build", "target", "__pycache__", ".venv", "venv"])

// A path the core refuses to read (secret_paths.py) is not offered either.
const PROTECTED = [
  /(^|\/)\.env($|\.)/,
  /(^|\/)\.(ssh|aws|gnupg|kube|azure)\//,
  /(^|\/)(id_rsa|id_ed25519|id_ecdsa|credentials)$/,
  /\.(pem|key|p12|pfx|crt)$/,
]

export function isOffered(file: string): boolean {
  return !PROTECTED.some((pattern) => pattern.test(file)) && !file.split("/").some((part) => SKIPPED_DIRECTORIES.has(part))
}

// Null when git is missing or this is not a repository: the caller walks the folder instead.
function gitFiles(workspace: string): Promise<string[] | null> {
  return new Promise((resolve) => {
    execFile(
      "git",
      ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
      { cwd: workspace, maxBuffer: 32 * 1024 * 1024, timeout: 5000 },
      (error, stdout) => resolve(error ? null : stdout.split("\0").filter(Boolean)),
    )
  })
}

// A folder that cannot be read has no files to offer; anything else is a real fault.
const UNREADABLE = new Set(["ENOENT", "EACCES", "EPERM", "ENOTDIR"])

async function entriesOf(directory: string) {
  try {
    return await readdir(directory, { withFileTypes: true })
  } catch (error) {
    if (UNREADABLE.has((error as NodeJS.ErrnoException).code ?? "")) return []
    throw error
  }
}

async function walk(root: string, directory = "", found: string[] = []): Promise<string[]> {
  if (found.length >= MAX_FILES) return found
  const entries = await entriesOf(path.join(root, directory))
  for (const entry of entries) {
    if (found.length >= MAX_FILES) break
    const relative = directory ? `${directory}/${entry.name}` : entry.name
    if (entry.isDirectory()) {
      if (!SKIPPED_DIRECTORIES.has(entry.name) && !entry.name.startsWith(".")) await walk(root, relative, found)
    } else if (entry.isFile()) {
      found.push(relative)
    }
  }
  return found
}

// A git checkout is listed by git (it knows what is ignored); any other folder is walked.
export async function listWorkspaceFiles(workspace: string): Promise<string[]> {
  const files = (await gitFiles(workspace)) ?? (await walk(workspace))
  return files.filter(isOffered).slice(0, MAX_FILES).sort()
}

export interface Mention {
  // Where the "@" is and where the typed word ends.
  start: number
  end: number
  query: string
}

// The `@word` the caret is in or right after, when the "@" starts a word.
export function mentionAt(text: string, caret: number): Mention | null {
  const before = text.slice(0, caret)
  const match = /(^|\s)@(\S*)$/.exec(before)
  if (!match) return null
  const start = caret - match[2]!.length - 1
  return { start, end: caret, query: match[2]! }
}
