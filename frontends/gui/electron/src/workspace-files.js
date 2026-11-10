"use strict"

// File names for the composer's @-mentions. The list comes from git when the
// workspace is a repository (so ignored files stay out), otherwise a bounded walk.

const fs = require("node:fs")
const path = require("node:path")
const { execFile } = require("node:child_process")
const { promisify } = require("node:util")

const execFileAsync = promisify(execFile)

const SKIPPED_DIRECTORIES = new Set([".git", "node_modules", ".kvist", ".venv", "__pycache__", "dist", "build"])
const WALK_LIMIT = 8000
const RESULT_LIMIT = 40

async function listWithGit(workspace) {
  try {
    const { stdout } = await execFileAsync(
      "git",
      ["-c", "core.quotepath=off", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
      { cwd: workspace, encoding: "utf8", maxBuffer: 32 * 1024 * 1024, timeout: 15000, windowsHide: true },
    )
    return stdout.split("\0").filter(Boolean)
  } catch {
    return null
  }
}

async function walk(root) {
  const found = []
  const queue = [""]
  while (queue.length && found.length < WALK_LIMIT) {
    const relative = queue.shift()
    let entries
    try {
      entries = await fs.promises.readdir(path.join(root, relative), { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      const child = relative ? `${relative}/${entry.name}` : entry.name
      if (entry.isDirectory()) {
        if (!SKIPPED_DIRECTORIES.has(entry.name)) queue.push(child)
      } else if (entry.isFile()) {
        found.push(child)
      }
    }
  }
  return found
}

// Names that start with the query beat names that merely contain it, which beat
// a match somewhere in the directory part; shorter paths win ties.
function rank(files, query) {
  const needle = query.toLowerCase()
  if (!needle) return files.slice(0, RESULT_LIMIT)
  const scored = []
  for (const file of files) {
    const lower = file.toLowerCase()
    const base = lower.slice(lower.lastIndexOf("/") + 1)
    let score
    if (base.startsWith(needle)) score = 0
    else if (base.includes(needle)) score = 1
    else if (lower.includes(needle)) score = 2
    else continue
    scored.push({ file, score })
  }
  scored.sort((left, right) => left.score - right.score || left.file.length - right.file.length)
  return scored.slice(0, RESULT_LIMIT).map((item) => item.file)
}

async function listWorkspaceFiles(workspace, query = "") {
  const files = (await listWithGit(workspace)) ?? (await walk(workspace))
  return rank(files, String(query))
}

module.exports = { listWorkspaceFiles, rank }
