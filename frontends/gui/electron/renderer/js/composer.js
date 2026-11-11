"use strict"
// The message box: autosize, history, @file mentions, /commands, image
// attachments, and the chips that choose permissions, role and model.

;(function defineComposer(root) {
  const K = (root.K = root.K || {})
  const { h } = K

  const MAX_IMAGES = 8
  const MAX_IMAGE_BYTES = 5 * 1024 * 1024
  const MAX_PROMPT_HEIGHT = 220

  const ROLES = [
    { value: "universal", label: () => "Universal", hint: () => "One agent for every kind of task" },
    { value: "auto", label: () => "Auto", hint: () => "Kvist picks the role per task" },
    { value: "coder", label: () => "Coder", hint: () => "Reads, edits and tests code" },
    { value: "reader", label: () => "Reader", hint: () => "Only reads: never changes files" },
    { value: "devops", label: () => "DevOps", hint: () => "Shell, services, environments" },
    { value: "chat", label: () => "Chat", hint: () => "Talk without touching the project" },
  ]

  const APPROVALS = [
    { value: "ask", icon: "hand", label: () => "Ask me first", title: "Ask for permission", hint: () => "You approve each risky command, and each overwrite of a file." },
    { value: "deny", icon: "shield", label: () => "Deny risky commands", title: "Deny risky commands", hint: () => "Risky commands are refused without asking. Kvist keeps working on what is left." },
  ]

  function loadRun() {
    try {
      const saved = JSON.parse(K.store.get("run", "{}"))
      return {
        provider: String(saved.provider || ""),
        models: { fast: String(saved.models?.fast || ""), smart: String(saved.models?.smart || "") },
        role: ROLES.some((role) => role.value === saved.role) ? saved.role : "",
        approvalMode: saved.approvalMode === "deny" ? "deny" : "ask",
      }
    } catch {
      return { provider: "", models: { fast: "", smart: "" }, role: "", approvalMode: "ask" }
    }
  }

  function readImages(files) {
    const images = Array.from(files).filter((file) => file.type.startsWith("image/"))
    if (images.some((file) => file.size > MAX_IMAGE_BYTES)) return Promise.reject(new Error("An image is over 5 MiB."))
    return Promise.all(images.map((file) => new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onerror = () => reject(new Error("Could not read the image."))
      reader.onload = () => resolve({ name: file.name, data_url: String(reader.result) })
      reader.readAsDataURL(file)
    })))
  }

  class Composer {
    // host: { getConfig() -> configInfo|null, ensureConfig(), commands() -> [...], onSubmit(text, images), onPause(), onStop(), openSettings(), addProvider() }
    constructor(host) {
      this.host = host
      this.run = loadRun()
      this.running = false
      this.locked = false
      this.images = []
      this.suggestions = []
      this.suggestIndex = 0
      this.suggestKind = ""
      this.historyEntries = null
      this.historyIndex = -1
      this.draft = ""
      this.mentionTimer = 0

      this.form = document.getElementById("composer")
      this.input = document.getElementById("prompt")
      this.send = document.getElementById("send-btn")
      this.attach = document.getElementById("attach-btn")
      this.tray = document.getElementById("attachments")
      this.suggest = document.getElementById("suggest")
      this.chips = {
        approval: document.getElementById("approval-chip"),
        model: document.getElementById("model-chip"),
      }
      this.meter = document.getElementById("context-meter")
      this.bind()
      this.updateChips()
      this.updateSend()
    }

    // --- run options -----------------------------------------------------------------
    options() {
      return { provider: this.run.provider, models: { ...this.run.models }, role: this.effectiveRole(), approvalMode: this.run.approvalMode }
    }

    // The role the next task runs under: the one picked here, else the one saved in
    // the terminal app (so both frontends behave alike).
    effectiveRole() {
      return this.run.role || this.host.getConfig()?.saved_role || "auto"
    }

    saveRun() {
      K.store.set("run", JSON.stringify(this.run))
      this.updateChips()
      this.host.onRunChange?.()
    }

    resetRun() {
      this.run = { provider: "", models: { fast: "", smart: "" }, role: "", approvalMode: "ask" }
      this.saveRun()
    }

    providerLabel(id) {
      const info = this.host.getConfig()
      const all = [...(info?.providers || []), ...(info?.custom_providers || [])]
      return all.find((entry) => entry.id === id)?.label || id
    }

    updateChips() {
      const approval = APPROVALS.find((item) => item.value === this.run.approvalMode)
      K.clear(this.chips.approval)
      this.chips.approval.append(K.icon(approval.icon, 16), h("span", {}, approval.label()))

      // The model the next task will really use: the one picked here, else the saved one.
      const info = this.host.getConfig()
      const id = this.run.provider || info?.saved_provider || ""
      const entry = [...(info?.providers || []), ...(info?.custom_providers || [])].find((item) => item.id === id)
      const model = this.run.models.smart || entry?.saved_models?.smart || entry?.defaults?.smart || ""
      K.clear(this.chips.model)
      this.chips.model.append(...[
        K.brand(entry?.model_icons?.[model] || entry?.icon || "", entry?.label || model, 16),
        model ? h("span", { class: "model-name" }, model) : null,
        h("span", { class: "chip-text" }, entry?.label || (model ? "" : "Choose a model")),
        K.icon("chevronDown", 14),
      ].filter(Boolean))
      this.chips.model.title = `Provider, models and role for the next task (${this.effectiveRole()})`
    }

    // --- chips ----------------------------------------------------------------------------
    openApproval() {
      const list = h("div", { class: "menu" }, h("div", { class: "menu-title" }, "How should Kvist ask?"))
      for (const item of APPROVALS) {
        list.append(K.menuItem({ icon: item.icon, label: item.title, description: item.hint(), checked: item.value === this.run.approvalMode, onClick: () => { this.run.approvalMode = item.value; this.saveRun() } }))
      }
      K.popover(this.chips.approval, list, { placement: "above" })
    }

    // The "+" menu: what can be added to the message, then the skills Kvist has.
    async openPlus() {
      const menu = h("div", { class: "menu" }, h("div", { class: "menu-title" }, "Add"))
      menu.append(
        K.menuItem({ icon: "image", label: "Images", description: "Attach pictures for Kvist to look at", onClick: () => this.picker.click() }),
        K.menuItem({ icon: "file", label: "A project file", description: "Mention a file with @", onClick: () => { this.setText(`${this.input.value}${this.input.value && !/\s$/.test(this.input.value) ? " " : ""}@`); this.updateSuggest() } }),
        K.menuItem({ icon: "folder", label: "Work in a project", description: "Choose the project for new threads", onClick: () => this.host.chooseProject() }),
      )
      const skills = await this.host.skillList().catch(() => [])
      if (skills.length) {
        menu.append(h("div", { class: "menu-title" }, "Skills"))
        for (const skill of skills.filter((entry) => entry.available)) {
          menu.append(K.menuItem({ icon: "sparkle", label: skill.name, hint: skill.summary, onClick: () => this.setText(`Use the ${skill.name} skill to `) }))
        }
      }
      K.popover(this.attach, menu, { placement: "above", className: "is-wide" })
    }

    async openModel() {
      await this.host.ensureConfig()
      const info = this.host.getConfig()
      const everyone = [...(info?.providers || []), ...(info?.custom_providers || [])]
      let chosen = this.run.provider
      const providerList = h("div", { class: "role-list" })
      const drawProviders = () => {
        K.clear(providerList)
        const usable = everyone.filter((entry) => entry.has_credentials || entry.id === chosen)
        const saved = everyone.find((entry) => entry.id === info?.saved_provider)
        providerList.append(K.menuItem({ iconNode: K.brand(saved?.icon || "", saved?.label || "Saved", 20), label: "Saved default", hint: saved?.label || "", checked: chosen === "", keepOpen: true, onClick: () => pick("") }))
        for (const entry of usable) {
          providerList.append(K.menuItem({ iconNode: K.brand(entry.icon, entry.label, 20), label: entry.label, checked: chosen === entry.id, keepOpen: true, onClick: () => pick(entry.id) }))
        }
      }
      const pick = (id) => {
        chosen = id
        fast.value = ""
        smart.value = ""
        commit()
        drawProviders()
      }

      const fast = h("input", { class: "field-input", list: "model-suggestions", placeholder: "fast model", value: this.run.models.fast, "aria-label": "Fast model" })
      const smart = h("input", { class: "field-input", list: "model-suggestions", placeholder: "smart model", value: this.run.models.smart, "aria-label": "Smart model" })
      const suggestions = h("datalist", { id: "model-suggestions" })
      const hint = h("div", { class: "field-hint" })

      const refresh = () => {
        const summary = everyone.find((entry) => entry.id === (chosen || info?.saved_provider))
        K.clear(suggestions)
        for (const option of summary?.options || []) suggestions.append(new Option(option.label || "", option.value))
        fast.placeholder = summary?.saved_models?.fast || summary?.defaults?.fast || "fast model"
        smart.placeholder = summary?.saved_models?.smart || summary?.defaults?.smart || "smart model"
        hint.textContent = summary && !summary.has_credentials
          ? "No API key saved for this provider: add it in Settings."
          : "Empty fields use the provider's saved or default models."
        hint.classList.toggle("is-warn", Boolean(summary && !summary.has_credentials))
      }
      const commit = () => {
        const models = { fast: fast.value.trim(), smart: smart.value.trim() }
        if (Boolean(models.fast) !== Boolean(models.smart)) {
          hint.textContent = "Fill both model fields, or clear both."
          hint.classList.add("is-warn")
          return
        }
        this.run.provider = chosen
        this.run.models = models
        this.saveRun()
        refresh()
      }
      fast.addEventListener("change", commit)
      smart.addEventListener("change", commit)
      refresh()
      drawProviders()

      const roles = h("div", { class: "role-list" })
      const drawRoles = () => {
        K.clear(roles)
        for (const item of ROLES) {
          roles.append(K.menuItem({ icon: "sparkle", label: item.label(), hint: item.hint(), checked: item.value === this.effectiveRole(), keepOpen: true, onClick: () => { this.run.role = item.value; this.saveRun(); drawRoles() } }))
        }
      }
      drawRoles()

      const panel = h(
        "div",
        { class: "model-panel" },
        h("div", { class: "menu-title" }, "Model for the next task"),
        h("div", { class: "menu-title" }, "Provider"),
        providerList,
        h("label", { class: "field" }, h("span", { class: "field-label" }, "Fast model"), fast),
        h("label", { class: "field" }, h("span", { class: "field-label" }, "Smart model"), smart),
        suggestions,
        hint,
        h("div", { class: "menu-title" }, "Role"),
        roles,
        h(
          "div",
          { class: "model-actions" },
          h("button", { class: "btn small", type: "button", onclick: () => { K.closePopover(); this.host.addProvider() } }, K.icon("plus", 13), "Custom provider"),
          h("button", { class: "btn small", type: "button", onclick: () => { K.closePopover(); this.host.openSettings("models") } }, K.icon("sliders", 13), "Settings"),
        ),
      )
      K.popover(this.chips.model, panel, { placement: "above", align: "end", className: "is-wide" })
    }

    // --- state ------------------------------------------------------------------------------
    setRunning(running) {
      this.running = running
      this.updateSend()
      this.input.placeholder = this.placeholder()
    }

    setLocked(locked) {
      this.locked = locked
      this.form.classList.toggle("is-hidden", locked)
    }

    placeholder() {
      return this.running
        ? "Kvist is working… Esc pauses it"
        : "Ask Kvist anything"
    }

    updateSend() {
      K.clear(this.send)
      this.send.classList.toggle("is-stop", this.running)
      this.send.append(K.icon(this.running ? "stop" : "arrowUp", this.running ? 18 : 18))
      this.send.setAttribute("aria-label", this.running ? "Stop" : "Send")
      this.send.title = this.running ? "Stop all" : "Send (Enter)"
      this.send.disabled = !this.running && !this.input.value.trim() && !this.images.length
    }

    setContext(tokens, limit) {
      if (!limit || !tokens) {
        this.meter.classList.add("is-hidden")
        return
      }
      const share = Math.min(1, tokens / limit)
      this.meter.classList.remove("is-hidden")
      this.meter.dataset.level = share > 0.9 ? "high" : share > 0.7 ? "mid" : "low"
      this.meter.title = `Context: ${K.formatTokens(tokens)} of ${K.formatTokens(limit)} tokens`
      this.meter.textContent = `${Math.round(share * 100)}%`
    }

    focus() {
      this.input.focus()
    }

    setText(text) {
      this.input.value = text
      this.autosize()
      this.updateSend()
      this.input.focus()
      this.input.setSelectionRange(text.length, text.length)
    }

    autosize() {
      this.input.style.height = "auto"
      this.input.style.height = `${Math.min(this.input.scrollHeight, MAX_PROMPT_HEIGHT)}px`
    }

    // --- submit ----------------------------------------------------------------------------
    submit() {
      if (this.running) {
        this.host.onStop()
        return
      }
      const text = this.input.value.trim()
      if (!text && !this.images.length) return
      if (text.startsWith("/") && this.runCommand(text)) return
      const images = this.images
      this.host.onSubmit(text, images)
      this.input.value = ""
      this.images = []
      this.renderTray()
      this.historyIndex = -1
      this.historyEntries = null
      this.closeSuggest()
      this.autosize()
      this.updateSend()
    }

    runCommand(text) {
      const name = text.slice(1).trim().split(/\s+/)[0].toLowerCase()
      const command = this.host.commands().find((entry) => entry.name === name)
      if (!command) return false
      this.input.value = ""
      this.autosize()
      this.updateSend()
      this.closeSuggest()
      command.run()
      return true
    }

    // --- suggestions (slash commands and @mentions) --------------------------------------------
    closeSuggest() {
      this.suggestKind = ""
      this.suggestions = []
      this.suggest.classList.add("is-hidden")
      K.clear(this.suggest)
    }

    showSuggest(kind, entries) {
      if (!entries.length) {
        this.closeSuggest()
        return
      }
      this.suggestKind = kind
      this.suggestions = entries
      this.suggestIndex = Math.min(this.suggestIndex, entries.length - 1)
      K.clear(this.suggest)
      entries.forEach((entry, index) => {
        const row = h(
          "button",
          { class: `suggest-row${index === this.suggestIndex ? " is-active" : ""}`, type: "button", role: "option" },
          K.icon(entry.icon || "file", 14),
          h("span", { class: "suggest-label" }, entry.label),
          entry.hint ? h("span", { class: "suggest-hint" }, entry.hint) : null,
        )
        row.addEventListener("mousedown", (event) => {
          event.preventDefault()
          this.acceptSuggestion(index)
        })
        this.suggest.append(row)
      })
      this.suggest.classList.remove("is-hidden")
    }

    updateSuggest() {
      const caret = this.input.selectionStart
      const before = this.input.value.slice(0, caret)
      if (/^\/[\w-]*$/.test(before)) {
        const word = before.slice(1).toLowerCase()
        const entries = this.host.commands()
          .filter((command) => command.name.startsWith(word))
          .map((command) => ({ icon: command.icon, label: `/${command.name}`, hint: command.label, value: `/${command.name}`, kind: "command" }))
        this.suggestIndex = 0
        this.showSuggest("command", entries)
        return
      }
      const mention = /(^|\s)@([^\s@]*)$/.exec(before)
      if (mention) {
        root.clearTimeout(this.mentionTimer)
        this.mentionTimer = root.setTimeout(async () => {
          let files = []
          try {
            files = await root.kvist.listFiles(mention[2])
          } catch {
            files = []
          }
          if (!/(^|\s)@([^\s@]*)$/.test(this.input.value.slice(0, this.input.selectionStart))) return
          this.suggestIndex = 0
          this.showSuggest("file", files.map((file) => ({ icon: "file", label: K.baseName(file), hint: file, value: file, kind: "file" })))
        }, 90)
        return
      }
      this.closeSuggest()
    }

    acceptSuggestion(index) {
      const entry = this.suggestions[index]
      if (!entry) return
      const caret = this.input.selectionStart
      const before = this.input.value.slice(0, caret)
      const after = this.input.value.slice(caret)
      if (entry.kind === "command") {
        this.input.value = ""
        this.closeSuggest()
        this.autosize()
        this.updateSend()
        this.host.commands().find((command) => `/${command.name}` === entry.value)?.run()
        return
      }
      const replaced = before.replace(/@([^\s@]*)$/, `@${entry.value} `)
      this.input.value = replaced + after
      this.input.setSelectionRange(replaced.length, replaced.length)
      this.closeSuggest()
      this.autosize()
      this.updateSend()
    }

    // --- input history ------------------------------------------------------------------------
    async navigateHistory(direction) {
      if (this.historyEntries === null) {
        try {
          this.historyEntries = (await root.kvist.inputHistory()).entries || []
        } catch {
          this.historyEntries = []
        }
        this.historyIndex = this.historyEntries.length
        this.draft = this.input.value
      }
      const next = this.historyIndex + direction
      if (next < 0 || next > this.historyEntries.length) return
      this.historyIndex = next
      const text = next === this.historyEntries.length ? this.draft : this.historyEntries[next]
      this.input.value = text
      this.autosize()
      this.updateSend()
    }

    // --- attachments ---------------------------------------------------------------------------
    async addImages(files) {
      try {
        const added = await readImages(files)
        if (this.images.length + added.length > MAX_IMAGES) throw new Error("Up to 8 images.")
        this.images = [...this.images, ...added]
        this.renderTray()
        this.updateSend()
      } catch (error) {
        K.toast(error.message, { kind: "error" })
      }
    }

    renderTray() {
      K.clear(this.tray)
      this.tray.classList.toggle("is-hidden", !this.images.length)
      this.images.forEach((image, index) => {
        this.tray.append(h(
          "div",
          { class: "thumb" },
          h("img", { src: image.data_url, alt: image.name }),
          h("button", { class: "thumb-remove", type: "button", "aria-label": "Remove", onclick: () => { this.images.splice(index, 1); this.renderTray(); this.updateSend() } }, K.icon("x", 12)),
        ))
      })
    }

    // --- wiring --------------------------------------------------------------------------------
    bind() {
      this.form.addEventListener("submit", (event) => { event.preventDefault(); this.submit() })
      this.input.addEventListener("input", () => { this.autosize(); this.updateSend(); this.updateSuggest() })
      this.input.addEventListener("keydown", (event) => this.onKey(event))
      this.input.addEventListener("blur", () => root.setTimeout(() => this.closeSuggest(), 120))
      this.input.addEventListener("paste", (event) => {
        const files = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"))
        if (!files.length) return
        event.preventDefault()
        void this.addImages(files)
      })
      this.form.addEventListener("dragover", (event) => event.preventDefault())
      this.form.addEventListener("drop", (event) => {
        event.preventDefault()
        void this.addImages(event.dataTransfer?.files || [])
      })
      this.picker = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp,image/gif", multiple: true, class: "is-hidden" })
      this.picker.addEventListener("change", () => { void this.addImages(this.picker.files); this.picker.value = "" })
      this.form.append(this.picker)
      this.attach.addEventListener("click", () => void this.openPlus())
      this.chips.approval.addEventListener("click", () => this.openApproval())
      this.chips.model.addEventListener("click", () => void this.openModel())
    }

    onKey(event) {
      if (this.suggestions.length && this.suggestKind) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault()
          const step = event.key === "ArrowDown" ? 1 : -1
          this.suggestIndex = (this.suggestIndex + step + this.suggestions.length) % this.suggestions.length
          this.showSuggest(this.suggestKind, this.suggestions)
          return
        }
        if (event.key === "Enter" || event.key === "Tab") {
          event.preventDefault()
          this.acceptSuggestion(this.suggestIndex)
          return
        }
        if (event.key === "Escape") {
          event.preventDefault()
          event.stopPropagation()
          this.closeSuggest()
          return
        }
      }
      if (event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.isComposing) {
        event.preventDefault()
        // Enter never stops a task: only the Stop button does.
        if (this.running) {
          K.toast("Kvist is working. Press Esc to pause it, or use Stop.", { ms: 2800 })
          return
        }
        this.submit()
        return
      }
      if (event.key === "Enter" && event.ctrlKey) {
        event.preventDefault()
        const { selectionStart: start, selectionEnd: end } = this.input
        this.input.setRangeText("\n", start, end, "end")
        this.autosize()
        return
      }
      const atStart = this.input.selectionStart === 0 && this.input.selectionEnd === 0
      if (event.key === "ArrowUp" && atStart && !this.running) {
        event.preventDefault()
        void this.navigateHistory(-1)
      } else if (event.key === "ArrowDown" && this.historyEntries !== null && this.input.selectionStart === this.input.value.length) {
        event.preventDefault()
        void this.navigateHistory(1)
      }
    }
  }

  K.Composer = Composer
  K.ROLES = ROLES
})(typeof window !== "undefined" ? window : globalThis)
