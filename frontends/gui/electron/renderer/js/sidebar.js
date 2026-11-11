"use strict"
// The side panel's thread lists: projects with their threads, then recent threads.

;(function defineSidebar(root) {
  const K = (root.K = root.K || {})
  const { h } = K

  const THREADS_PER_PROJECT = 5
  const RECENT_COUNT = 12

  function loadSet(key) {
    try {
      return new Set(JSON.parse(K.store.get(key, "[]")))
    } catch {
      return new Set()
    }
  }

  class Sidebar {
    // host: { onNewThread(path), onOpenThread(path, name), onDeleteThread(path, name, title), onOpenProject(path), onRemoveProject(path) }
    constructor(host) {
      this.host = host
      this.projectsEl = document.getElementById("projects")
      this.recentEl = document.getElementById("recent")
      this.collapsed = loadSet("collapsed")
      this.showAll = new Set()
      this.state = { projects: [], workspace: "", activeSession: "", running: false, awaiting: false }
    }

    render(state) {
      this.state = { ...this.state, ...state }
      const { projects } = this.state
      K.clear(this.projectsEl)
      K.clear(this.recentEl)
      if (!projects.length) this.projectsEl.append(h("div", { class: "side-empty" }, "No projects yet."))
      for (const project of projects) this.projectsEl.append(this.renderProject(project))

      const recent = projects
        .flatMap((project) => (project.status?.session_items || []).map((item) => ({ project, item })))
        .sort((a, b) => Number(b.item.updated_at) - Number(a.item.updated_at))
        .slice(0, RECENT_COUNT)
      for (const { project, item } of recent) this.recentEl.append(this.renderThread(project, item, false))
    }

    toggleCollapsed(path) {
      if (this.collapsed.has(path)) this.collapsed.delete(path)
      else this.collapsed.add(path)
      K.store.set("collapsed", JSON.stringify([...this.collapsed]))
      this.render({})
    }

    renderProject(project) {
      const isCurrent = project.path === this.state.workspace
      const collapsed = this.collapsed.has(project.path)
      const group = h("div", { class: `project${project.available ? "" : " is-missing"}` })

      const menuButton = h("button", { class: "icon-btn small", type: "button", title: "More", "aria-label": "Project menu" }, K.icon("sliders", 14))
      menuButton.addEventListener("click", (event) => {
        event.stopPropagation()
        const menu = h("div", { class: "menu" })
        menu.append(
          K.menuItem({ icon: "folder", label: "Make current", disabled: isCurrent || !project.available, onClick: () => this.host.onOpenProject(project.path) }),
          K.menuItem({ icon: "x", label: "Remove from the list", danger: true, onClick: () => this.host.onRemoveProject(project.path) }),
        )
        K.popover(menuButton, menu, { align: "end" })
      })
      const newButton = h("button", { class: "icon-btn small", type: "button", title: "New thread in this project", "aria-label": "New thread", disabled: !project.available }, K.icon("compose", 14))
      newButton.addEventListener("click", (event) => {
        event.stopPropagation()
        this.host.onNewThread(project.path)
      })

      const head = h(
        "div",
        { class: "project-head", role: "button", tabindex: "0", title: project.path, "aria-expanded": String(!collapsed) },
        K.icon(collapsed ? "folder" : "folderOpen", 17),
        h("span", { class: "project-name" }, K.baseName(project.path)),
        h("span", { class: "project-actions" }, newButton, menuButton),
      )
      const toggle = () => this.toggleCollapsed(project.path)
      head.addEventListener("click", toggle)
      head.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle() }
      })
      group.append(head)
      if (collapsed) return group

      if (!project.available) {
        group.append(h("div", { class: "side-empty" }, "Folder not found"))
        return group
      }
      const items = [...(project.status?.session_items || [])].sort((a, b) => Number(b.updated_at) - Number(a.updated_at))
      const expanded = this.showAll.has(project.path)
      for (const item of expanded ? items : items.slice(0, THREADS_PER_PROJECT)) group.append(this.renderThread(project, item, true))
      if (items.length > THREADS_PER_PROJECT) {
        const more = h("button", { class: "thread-more", type: "button" }, expanded ? "Show less" : "Show more")
        more.addEventListener("click", () => {
          if (expanded) this.showAll.delete(project.path)
          else this.showAll.add(project.path)
          this.render({})
        })
        group.append(more)
      }
      return group
    }

    renderThread(project, item, nested) {
      const { workspace, activeSession, running, awaiting } = this.state
      const active = project.path === workspace && item.name === activeSession
      const row = h("div", { class: `thread${active ? " is-active" : ""}${nested ? " is-nested" : ""}`, role: "button", tabindex: "0", title: `${item.title}\n${K.baseName(project.path)}` })
      let trailing = null
      if (active && awaiting) trailing = h("span", { class: "thread-flag", title: "Waiting for you" })
      else if (active && running) trailing = h("span", { class: "spinner small", title: "Working" })
      else if (item.has_checkpoint) trailing = h("span", { class: "thread-flag is-paused", title: "Unfinished task" })
      row.append(h("span", { class: "thread-title" }, item.title || item.name))
      if (trailing) row.append(trailing)
      if (!active) {
        const remove = h("button", { class: "icon-btn small thread-delete", type: "button", title: "Delete thread", "aria-label": "Delete thread" }, K.icon("trash", 13))
        remove.addEventListener("click", (event) => {
          event.stopPropagation()
          this.host.onDeleteThread(project.path, item.name, item.title || item.name)
        })
        row.append(remove)
      }
      const open = () => this.host.onOpenThread(project.path, item.name)
      row.addEventListener("click", open)
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open() }
      })
      return row
    }
  }

  K.Sidebar = Sidebar
})(typeof window !== "undefined" ? window : globalThis)
