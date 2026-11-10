"use strict"

// Read-mostly git helpers for the review panel and the commit dialog. Every
// path that comes from the renderer is checked to stay inside the workspace.

const fs = require("node:fs")
const path = require("node:path")
const { execFile } = require("node:child_process")
const { promisify } = require("node:util")

const execFileAsync = promisify(execFile)

const EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
const MAX_UNTRACKED_BYTES = 200 * 1024
const MAX_DIFF_BYTES = 4 * 1024 * 1024
const MAX_UNTRACKED_FILES = 60
// Kvist keeps its own state in the project; it is never part of a review or commit.
const NOT_KVIST = [":(exclude).kvist"]

async function git(workspace, args, { allowFailure = false } = {}) {
  try {
    const { stdout } = await execFileAsync("git", ["-c", "core.quotepath=off", ...args], {
      cwd: workspace,
      encoding: "utf8",
      maxBuffer: MAX_DIFF_BYTES * 2,
      timeout: 20000,
      windowsHide: true,
      env: { ...process.env, GIT_OPTIONAL_LOCKS: "0", LC_ALL: "C" },
    })
    return stdout
  } catch (error) {
    if (allowFailure && typeof error.stdout === "string") return error.stdout
    throw new Error(String(error.stderr || error.message).trim().split(/\r?\n/).at(-1))
  }
}

async function isRepository(workspace) {
  try {
    return (await git(workspace, ["rev-parse", "--is-inside-work-tree"])).trim() === "true"
  } catch {
    return false
  }
}

async function baseRevision(workspace) {
  try {
    await git(workspace, ["rev-parse", "--verify", "--quiet", "HEAD"])
    return "HEAD"
  } catch {
    return EMPTY_TREE
  }
}

// `status --porcelain -z` entries are "XY path" and, for a rename, "XY new\0old".
function parseStatus(raw) {
  const entries = []
  const parts = raw.split("\0")
  for (let index = 0; index < parts.length; index += 1) {
    const item = parts[index]
    if (item.length < 4) continue
    const code = item.slice(0, 2)
    entries.push({ path: item.slice(3), code, untracked: code === "??" })
    if (code[0] === "R" || code[0] === "C") index += 1
  }
  return entries
}

async function gitInfo(workspace) {
  if (!(await isRepository(workspace))) return { isRepo: false }
  const [branchRaw, statusRaw, numstat] = await Promise.all([
    git(workspace, ["branch", "--show-current"], { allowFailure: true }),
    git(workspace, ["status", "--porcelain", "-z", "--untracked-files=all", "--", ".", ...NOT_KVIST], { allowFailure: true }),
    git(workspace, ["diff", await baseRevision(workspace), "--numstat", "--", ".", ...NOT_KVIST], { allowFailure: true }),
  ])
  let branch = branchRaw.trim()
  if (!branch) {
    const short = await git(workspace, ["rev-parse", "--short", "HEAD"], { allowFailure: true })
    branch = short.trim() ? `detached @ ${short.trim()}` : "(no commits)"
  }
  let added = 0
  let removed = 0
  for (const line of numstat.split("\n")) {
    const [plus, minus] = line.split("\t")
    added += Number(plus) || 0
    removed += Number(minus) || 0
  }
  return { isRepo: true, branch, files: parseStatus(statusRaw), added, removed }
}

function newFilePatch(relative, text) {
  const lines = text.split("\n")
  if (lines.at(-1) === "") lines.pop()
  const body = lines.map((line) => `+${line}`).join("\n")
  return [
    `diff --git a/${relative} b/${relative}`,
    "new file mode 100644",
    "--- /dev/null",
    `+++ b/${relative}`,
    `@@ -0,0 +1,${lines.length} @@`,
    body,
    "",
  ].join("\n")
}

async function untrackedPatches(workspace, entries) {
  const patches = []
  for (const entry of entries.filter((item) => item.untracked).slice(0, MAX_UNTRACKED_FILES)) {
    const absolute = path.join(workspace, entry.path)
    let stats
    try {
      stats = await fs.promises.stat(absolute)
    } catch {
      continue
    }
    if (!stats.isFile() || stats.size > MAX_UNTRACKED_BYTES) continue
    const buffer = await fs.promises.readFile(absolute)
    if (buffer.includes(0)) continue
    patches.push(newFilePatch(entry.path, buffer.toString("utf8")))
  }
  return patches
}

async function gitDiff(workspace) {
  if (!(await isRepository(workspace))) return { isRepo: false, patch: "" }
  const base = await baseRevision(workspace)
  const [tracked, statusRaw] = await Promise.all([
    git(workspace, ["diff", base, "--no-color", "--no-ext-diff", "--", ".", ...NOT_KVIST], { allowFailure: true }),
    git(workspace, ["status", "--porcelain", "-z", "--untracked-files=all", "--", ".", ...NOT_KVIST], { allowFailure: true }),
  ])
  const extra = await untrackedPatches(workspace, parseStatus(statusRaw))
  const patch = [tracked, ...extra].join("\n")
  return { isRepo: true, patch: patch.length > MAX_DIFF_BYTES ? patch.slice(0, MAX_DIFF_BYTES) : patch }
}

function checkedRelativePaths(workspace, paths) {
  if (!Array.isArray(paths) || !paths.length) throw new Error("Choose at least one file.")
  const root = path.resolve(workspace)
  return paths.map((item) => {
    const value = String(item)
    const resolved = path.resolve(root, value)
    if (value.startsWith("-") || !resolved.startsWith(root + path.sep)) {
      throw new Error(`Not a path inside the workspace: ${value}`)
    }
    return path.relative(root, resolved)
  })
}

async function gitCommit(workspace, { message, paths }) {
  const text = String(message || "").trim()
  if (!text) throw new Error("The commit message is empty.")
  const files = checkedRelativePaths(workspace, paths)
  await git(workspace, ["add", "--", ...files])
  await git(workspace, ["commit", "-m", text, "--", ...files])
  const subject = (await git(workspace, ["log", "-1", "--pretty=%h %s"])).trim()
  return { ok: true, commit: subject }
}

// The payload `--commit-message` expects: one unified diff per selected file.
function splitPatchByFile(patch) {
  const files = []
  let current = null
  for (const line of patch.split("\n")) {
    const header = /^diff --git a\/(.+) b\/(.+)$/.exec(line)
    if (header) {
      current = { path: header[2], lines: [] }
      files.push(current)
    }
    if (current) current.lines.push(line)
  }
  return files.map((file) => ({ path: file.path, patch: file.lines.join("\n") }))
}

module.exports = {
  gitInfo,
  gitDiff,
  gitCommit,
  splitPatchByFile,
  checkedRelativePaths,
  parseStatus,
  newFilePatch,
}
