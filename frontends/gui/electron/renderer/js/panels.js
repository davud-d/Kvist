"use strict"
// The right-hand Review panel, the bottom Commands drawer and the commit dialog.

;(function definePanels(root) {
  const K = (root.K = root.K || {})
  const { h } = K

  // --- helpers -----------------------------------------------------------------------
  // The same file edited several times arrives as several patches; show it once.
  function mergeFiles(patches) {
    const merged = new Map()
    for (const patch of patches) {
      for (const file of K.diff.parseUnifiedDiff(patch)) {
        const known = merged.get(file.path)
        if (!known) {
          merged.set(file.path, { ...file, hunks: [...file.hunks] })
          continue
        }
        known.hunks.push(...file.hunks)
        known.added += file.added
        known.removed += file.removed
      }
    }
    return [...merged.values()]
  }

  function totals(files) {
    return files.reduce((sum, file) => ({ added: sum.added + file.added, removed: sum.removed + file.removed }), { added: 0, removed: 0 })
  }

  // --- Review panel ----------------------------------------------------------------------
  class ReviewPanel {
    // host: { thread() -> Thread, canWrite() -> bool, onRevert(), onCommit() }
    constructor(host) {
      this.host = host
      this.el = document.getElementById("review")
      this.body = document.getElementById("review-body")
      this.actions = document.getElementById("review-actions")
      this.tabs = Array.from(this.el.querySelectorAll("[data-tab]"))
      this.tab = K.store.get("reviewTab", "agent") === "git" ? "git" : "agent"
      this.open = false
      this.token = 0
      for (const button of this.tabs) {
        button.addEventListener("click", () => {
          this.tab = button.dataset.tab
          K.store.set("reviewTab", this.tab)
          void this.refresh()
        })
      }
      this.bindResize()
    }

    bindResize() {
      const handle = document.getElementById("review-resize")
      const saved = Number(K.store.get("reviewWidth", "0"))
      if (saved >= 320) document.documentElement.style.setProperty("--review-width", `${saved}px`)
      handle.addEventListener("mousedown", (event) => {
        event.preventDefault()
        const move = (moveEvent) => {
          const width = Math.max(320, Math.min(root.innerWidth - 460, root.innerWidth - moveEvent.clientX))
          document.documentElement.style.setProperty("--review-width", `${width}px`)
          K.store.set("reviewWidth", Math.round(width))
        }
        const up = () => {
          document.removeEventListener("mousemove", move)
          document.removeEventListener("mouseup", up)
          document.body.classList.remove("is-resizing")
        }
        document.body.classList.add("is-resizing")
        document.addEventListener("mousemove", move)
        document.addEventListener("mouseup", up)
      })
    }

    setOpen(open) {
      this.open = open
      this.el.classList.toggle("is-open", open)
      document.getElementById("app").classList.toggle("review-open", open)
      if (open) void this.refresh()
    }

    toggle() {
      this.setOpen(!this.open)
    }

    async refresh() {
      if (!this.open) return
      const token = (this.token += 1)
      for (const button of this.tabs) button.classList.toggle("is-active", button.dataset.tab === this.tab)
      K.clear(this.actions)
      K.clear(this.body)
      if (this.tab === "agent") {
        this.renderFiles(mergeFiles(this.host.thread().changes().flatMap((entry) => entry.patches)), "agent")
        return
      }
      this.body.append(h("div", { class: "panel-empty" }, h("span", { class: "spinner" })))
      let result
      try {
        result = await root.kvist.gitDiff()
      } catch (error) {
        if (token === this.token) this.renderMessage(error.message)
        return
      }
      if (token !== this.token) return
      if (!result.isRepo) {
        this.renderMessage("This project is not a git repository.")
        return
      }
      this.renderFiles(K.diff.parseUnifiedDiff(result.patch), "git")
    }

    renderMessage(text) {
      K.clear(this.body)
      this.body.append(h("div", { class: "panel-empty" }, text))
    }

    renderFiles(files, kind) {
      K.clear(this.body)
      K.clear(this.actions)
      const sum = totals(files)
      this.actions.append(
        h("span", { class: "review-summary" }, files.length ? [`${files.length} file(s)`, " ", K.diff.counts(sum)] : "No changes"),
        h("span", { class: "grow" }),
        h("button", { class: "icon-btn", type: "button", title: "Refresh", "aria-label": "Refresh", onclick: () => void this.refresh() }, K.icon("refresh", 15)),
      )
      if (kind === "git") {
        this.actions.append(h("button", { class: "btn small primary", type: "button", disabled: !files.length || !this.host.canWrite(), onclick: () => this.host.onCommit() }, K.icon("branch", 13), "Commit…"))
      } else {
        this.actions.append(h("button", { class: "btn small", type: "button", disabled: !this.host.canWrite(), onclick: () => this.host.onRevert(), title: "Restore every file the agent changed" }, K.icon("undo", 13), "Revert"))
      }
      if (!files.length) {
        this.body.append(h("div", { class: "panel-empty" }, kind === "agent"
          ? "The agent has not changed any files in this thread yet."
          : "The working tree is clean."))
        return
      }
      files.forEach((file, index) => this.body.append(K.diff.renderDiffFile(file, { open: files.length <= 4 && index < 4 })))
    }
  }

  // --- Commands drawer ----------------------------------------------------------------------
  class Drawer {
    // host: { thread() -> Thread }
    constructor(host) {
      this.host = host
      this.el = document.getElementById("drawer")
      this.body = document.getElementById("drawer-body")
      this.open = false
      this.frame = 0
    }

    setOpen(open) {
      this.open = open
      this.el.classList.toggle("is-open", open)
      if (open) this.render()
    }

    toggle() {
      this.setOpen(!this.open)
    }

    schedule() {
      if (!this.open || this.frame) return
      this.frame = root.requestAnimationFrame(() => {
        this.frame = 0
        this.render()
      })
    }

    render() {
      const thread = this.host.thread()
      const stick = this.body.scrollHeight - this.body.scrollTop - this.body.clientHeight < 80
      K.clear(this.body)
      const jobs = thread.items.filter((item) => item.kind === "job")
      const calls = thread.shellCalls()
      if (!calls.length && !jobs.length) {
        this.body.append(h("div", { class: "panel-empty" }, "Commands the agent runs show up here."))
        return
      }
      for (const job of jobs) {
        const command = Array.isArray(job.command) ? job.command.join(" ") : String(job.command)
        this.body.append(this.entry(command, job.status === "running" ? "running" : job.exitCode === 0 ? "ok" : "failed", job.output, "background"))
      }
      for (const call of calls) {
        const out = call.state === "running" ? call.live : call.output
        const mark = call.state === "ok" && call.exitCode ? "failed" : call.state
        this.body.append(this.entry(K.describeCall(call).subject, mark, out, call.exitCode !== null ? `exit ${call.exitCode}` : ""))
      }
      if (stick) this.body.scrollTop = this.body.scrollHeight
    }

    entry(command, state, output, meta) {
      return h(
        "div",
        { class: `drawer-entry is-${state}` },
        h("div", { class: "drawer-cmd" }, h("span", { class: "prompt" }, "$ "), h("span", { class: "drawer-cmd-text" }, command), meta ? h("span", { class: "drawer-meta" }, meta) : null, state === "running" ? h("span", { class: "spinner small" }) : null),
        output ? h("pre", { class: "term" }, output) : null,
      )
    }
  }

  // --- Commit dialog -------------------------------------------------------------------------------
  async function openCommitDialog({ onDone }) {
    let result
    try {
      result = await root.kvist.gitDiff()
    } catch (error) {
      K.toast(error.message, { kind: "error" })
      return
    }
    const files = K.diff.parseUnifiedDiff(result.patch)
    if (!files.length) {
      K.toast("Nothing to commit.")
      return
    }
    const checks = new Map()
    const list = h("div", { class: "commit-files" })
    for (const file of files) {
      const box = h("input", { type: "checkbox", checked: true })
      checks.set(file.path, box)
      list.append(h("label", { class: "commit-file" }, box, h("span", { class: "commit-path", title: file.path }, file.path), K.diff.counts(file)))
    }
    const message = h("textarea", { class: "field-input commit-message", rows: "5", placeholder: "Commit message", "aria-label": "Commit message" })
    const error = h("div", { class: "field-hint is-warn" })
    const selected = () => [...checks].filter(([, box]) => box.checked).map(([path]) => path)

    const generate = h("button", { class: "btn small", type: "button" }, K.icon("sparkle", 13), "Write with Kvist")
    generate.addEventListener("click", async () => {
      error.textContent = ""
      if (!selected().length) {
        error.textContent = "Choose at least one file."
        return
      }
      generate.disabled = true
      try {
        message.value = (await root.kvist.commitMessage(selected())).message
      } catch (failure) {
        error.textContent = failure.message
      } finally {
        generate.disabled = false
      }
    })

    const body = h("div", { class: "commit-dialog" }, list, h("div", { class: "commit-tools" }, generate), message, error)
    K.modal({
      title: "Commit changes",
      body,
      wide: true,
      actions: [
        { label: "Cancel" },
        {
          label: "Commit",
          kind: "primary",
          keepOpen: true,
          onClick: async (close) => {
            error.textContent = ""
            try {
              const done = await root.kvist.gitCommit(message.value, selected())
              close()
              K.toast(`Committed ${done.commit}`)
              onDone?.()
            } catch (failure) {
              error.textContent = failure.message
            }
          },
        },
      ],
    })
  }

  K.ReviewPanel = ReviewPanel
  K.Drawer = Drawer
  K.openCommitDialog = openCommitDialog
  K.mergeFiles = mergeFiles
  K.totals = totals
})(typeof window !== "undefined" ? window : globalThis)
