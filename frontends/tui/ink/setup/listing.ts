// Reading and making folders for the chooser. Failures come back as a sentence the screen can
// show, never as an exception.
import { access, mkdir, readdir, stat } from "node:fs/promises"
import { join } from "node:path"

import { DRIVES, type DirEntry, type Listing } from "./folders.js"

// A folder with more subfolders than this is shown by its first ones; the chooser is not a file manager.
const MAX_DIRS = 3000
// Only this many folders are looked into for a .git; the rest are shown without the mark.
const MAX_MARKED = 400

function describe(error: unknown, path: string): string {
  const code = (error as NodeJS.ErrnoException | undefined)?.code
  if (code === "EACCES" || code === "EPERM") return "No permission to read this folder."
  if (code === "ENOENT") return "This folder no longer exists."
  if (code === "ENOTDIR") return "This is not a folder."
  if (code === "EEXIST") return "A file or folder with this name already exists."
  return `Could not read ${path}.`
}

const exists = (path: string) =>
  access(path).then(
    () => true,
    // Not there, or not readable: either way there is no mark to show.
    () => false,
  )

export async function listDirectory(path: string): Promise<Listing> {
  let entries
  try {
    entries = await readdir(path, { withFileTypes: true })
  } catch (error) {
    return { path, dirs: [], files: 0, error: describe(error, path) }
  }
  const names: string[] = []
  let files = 0
  await Promise.all(
    entries.map(async (entry) => {
      if (entry.isDirectory()) return void names.push(entry.name)
      if (!entry.isSymbolicLink()) return void files++
      // A link counts as a folder when what it points at is one.
      const target = await stat(join(path, entry.name)).catch(() => null)
      if (target?.isDirectory()) names.push(entry.name)
      else files++
    }),
  )
  names.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true }))
  const shown = names.slice(0, MAX_DIRS)
  const dirs: DirEntry[] = await Promise.all(
    shown.map(async (name, index) => ({ name, git: index < MAX_MARKED && (await exists(join(path, name, ".git"))) })),
  )
  return { path, dirs, files }
}

export async function makeDirectory(parent: string, name: string): Promise<{ path: string; error?: string }> {
  const path = join(parent, name)
  if (/[\\/]/.test(name) || name === "." || name === "..") return { path, error: "A folder name cannot contain a slash." }
  try {
    await mkdir(path)
    return { path }
  } catch (error) {
    return { path, error: describe(error, path) }
  }
}

// A drive that does not answer (an unplugged network share) must not hold the chooser up.
const DRIVE_WAIT_MS = 1500

const readable = (path: string) =>
  access(path).then(
    () => true,
    // No such drive, or no medium in it.
    () => false,
  )

function within(promise: Promise<boolean>, ms: number): Promise<boolean> {
  return Promise.race([promise, new Promise<boolean>((resolve) => setTimeout(() => resolve(false), ms))])
}

// The drive letters of a Windows machine that answer, as roots ("C:\\"). `check` is a parameter so the
// list can be made without a Windows machine.
export async function listDrives(check: (root: string) => Promise<boolean> = readable): Promise<Listing> {
  const roots = await Promise.all(
    [..."ABCDEFGHIJKLMNOPQRSTUVWXYZ"].map(async (letter) => {
      const root = `${letter}:\\`
      return (await within(check(root), DRIVE_WAIT_MS)) ? root : null
    }),
  )
  const dirs: DirEntry[] = roots.flatMap((root) => (root ? [{ name: root, git: false }] : []))
  return { path: DRIVES, dirs, files: 0 }
}
