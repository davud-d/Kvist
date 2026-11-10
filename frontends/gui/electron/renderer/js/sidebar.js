"use strict"
// The left column: projects, each with its threads (Kvist sessions).

;(function defineSidebar(root) {
  const K = (root.K = root.K || {})
  const { h, t } = K

  const THREADS_PER_PROJECT = 7

  function loadSet(key) {
    try {
      return new Set(JSON.parse(K.store.get(key, "[]")))
    } catch {
      return new Set()
    }
  }

  class Sidebar {
    // host: { onNewThread(path), onOpenThread(path, name), onDeleteThread(path, name), onOpenProject(path), onRemoveProject(path), onAddProject() }
    constructor(host) {
      this.host = host
      this.list = document.getElementById("projects")
      this.collapsed = loadSet("collapsed")
      this.showAll = new Set()
      this.state = { projects: [], workspace: "", activeSession: "", running: false, awaiting: false }
    }

    render(state) {
      this.state = { ...this.state, ...state }
      K.clear(this.list)
      const { projects } = this.state
      if (!projects.length) {
        this.list.append(h("div", { class: "side-empty" }, t("No projects yet. Add a folder to begin.", "Проектов пока нет. Добавь папку, чтобы начать.")))
        return
      }
      for (const project of projects) this.list.append(this.renderProject(project))
    }

    toggleCollapsed(path) {
      if (this.collapsed.has(path)) this.collapsed.delete(path)
      else this.collapsed.add(path)
      K.store.set("collapsed", JSON.stringify([...this.collapsed]))
      this.render({})
    }

    renderProject(project) {
      const { workspace } = this.state
      const isCurrent = project.path === workspace
      const collapsed = this.collapsed.has(project.path)
      const group = h("div", { class: `project${isCurrent ? " is-current" : ""}${project.available ? "" : " is-missing"}` })

      const menuButton = h("button", { class: "icon-btn small", type: "button", title: t("More", "Ещё"), "aria-label": t("Project menu", "Меню проекта") }, K.icon("sliders", 14))
      menuButton.addEventListener("click", (event) => {
        event.stopPropagation()
        const menu = h("div", { class: "menu" })
        menu.append(
          K.menuItem({ icon: "folder", label: t("Open as current project", "Сделать текущим"), disabled: isCurrent || !project.available, onClick: () => this.host.onOpenProject(project.path) }),
          K.menuItem({ icon: "x", label: t("Remove from the list", "Убрать из списка"), danger: true, onClick: () => this.host.onRemoveProject(project.path) }),
        )
        K.popover(menuButton, menu, { align: "end" })
      })
      const newButton = h("button", { class: "icon-btn small", type: "button", title: t("New thread in this project", "Новый тред в проекте"), "aria-label": t("New thread", "Новый тред"), disabled: !project.available }, K.icon("compose", 14))
      newButton.addEventListener("click", (event) => {
        event.stopPropagation()
        this.host.onNewThread(project.path)
      })

      const head = h(
        "div",
        { class: "project-head", role: "button", tabindex: "0", title: project.path, "aria-expanded": String(!collapsed) },
        K.icon(collapsed ? "chevronRight" : "chevronDown", 13),
        K.icon("folder", 15),
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

      const items = [...(project.status?.session_items || [])].sort((a, b) => Number(b.updated_at) - Number(a.updated_at))
      if (!project.available) {
        group.append(h("div", { class: "side-empty" }, t("Folder not found", "Папка не найдена")))
        return group
      }
      if (!items.length) group.append(h("div", { class: "side-empty" }, t("No threads yet", "Тредов пока нет")))
      const expanded = this.showAll.has(project.path)
      const visible = expanded ? items : items.slice(0, THREADS_PER_PROJECT)
      for (const item of visible) group.append(this.renderThread(project, item))
      if (items.length > THREADS_PER_PROJECT) {
        const more = h("button", { class: "thread-more", type: "button" }, expanded ? t("Show less", "Свернуть") : t(`Show all ${items.length}`, `Показать все ${items.length}`))
        more.addEventListener("click", () => {
          if (expanded) this.showAll.delete(project.path)
          else this.showAll.add(project.path)
          this.render({})
        })
        group.append(more)
      }
      return group
    }

    renderThread(project, item) {
      const { workspace, activeSession, running, awaiting } = this.state
      const active = project.path === workspace && item.name === activeSession
      const row = h("div", { class: `thread${active ? " is-active" : ""}`, role: "button", tabindex: "0", title: `${item.title}\n${item.name}` })
      let trailing
      if (active && awaiting) trailing = h("span", { class: "thread-flag", title: t("Waiting for you", "Ждёт тебя") })
      else if (active && running) trailing = h("span", { class: "spinner small", title: t("Working", "Работает") })
      else if (item.has_checkpoint) trailing = h("span", { class: "thread-flag is-paused", title: t("Unfinished task", "Незавершённая задача") })
      else trailing = h("span", { class: "thread-time" }, K.timeAgo(item.updated_at))
      row.append(h("span", { class: "thread-title" }, item.title || item.name), trailing)
      if (!active) {
        const remove = h("button", { class: "icon-btn small thread-delete", type: "button", title: t("Delete thread", "Удалить тред"), "aria-label": t("Delete thread", "Удалить тред") }, K.icon("trash", 13))
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
