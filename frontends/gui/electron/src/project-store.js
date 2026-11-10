"use strict"

const fs = require("node:fs")
const path = require("node:path")

class ProjectStore {
  constructor(storagePath) {
    this.storagePath = storagePath
  }

  load() {
    let payload
    try {
      payload = JSON.parse(fs.readFileSync(this.storagePath, "utf8"))
    } catch {
      return []
    }
    if (!payload || payload.version !== 1 || !Array.isArray(payload.projects)) return []
    const unique = new Map()
    for (const project of payload.projects) {
      if (!project || typeof project.path !== "string" || !project.path.trim()) continue
      const resolved = path.resolve(project.path)
      const lastOpenedAt = Number(project.lastOpenedAt) || 0
      const previous = unique.get(resolved)
      if (!previous || lastOpenedAt > previous.lastOpenedAt) {
        unique.set(resolved, { path: resolved, lastOpenedAt })
      }
    }
    return [...unique.values()]
      .sort((left, right) => right.lastOpenedAt - left.lastOpenedAt)
      .slice(0, 200)
  }

  remember(projectPath, openedAt = Date.now()) {
    const resolved = path.resolve(projectPath)
    const projects = this.load().filter((project) => project.path !== resolved)
    projects.unshift({ path: resolved, lastOpenedAt: openedAt })
    return this.save(projects.slice(0, 200))
  }

  // Forgetting a project only drops it from the sidebar; its folder and
  // sessions stay on disk.
  forget(projectPath) {
    const resolved = path.resolve(projectPath)
    return this.save(this.load().filter((project) => project.path !== resolved))
  }

  save(projects) {
    fs.mkdirSync(path.dirname(this.storagePath), { recursive: true })
    const temporary = `${this.storagePath}.${process.pid}.tmp`
    fs.writeFileSync(temporary, `${JSON.stringify({ version: 1, projects }, null, 2)}\n`, {
      encoding: "utf8",
      mode: 0o600,
    })
    fs.renameSync(temporary, this.storagePath)
    return projects
  }
}

module.exports = { ProjectStore }
