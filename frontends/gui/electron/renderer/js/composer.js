"use strict"
// The message box: autosize, history, @file mentions, /commands, image
// attachments, and the chips that choose permissions, role and model.

;(function defineComposer(root) {
  const K = (root.K = root.K || {})
  const { h, t } = K

  const MAX_IMAGES = 8
  const MAX_IMAGE_BYTES = 5 * 1024 * 1024
  const MAX_PROMPT_HEIGHT = 220

  const ROLES = [
    { value: "auto", label: () => t("Auto", "Авто"), hint: () => t("Kvist picks the role per task", "Kvist сам выбирает роль") },
    { value: "coder", label: () => t("Coder", "Кодер"), hint: () => t("Reads, edits and tests code", "Читает, правит и тестирует код") },
    { value: "reader", label: () => t("Reader", "Читатель"), hint: () => t("Only reads: never changes files", "Только читает, файлы не трогает") },
    { value: "devops", label: () => t("DevOps", "DevOps"), hint: () => t("Shell, services, environments", "Оболочка, сервисы, окружения") },
    { value: "chat", label: () => t("Chat", "Чат"), hint: () => t("Talk without touching the project", "Разговор без правок проекта") },
  ]

  const APPROVALS = [
    { value: "ask", icon: "shield", label: () => t("Ask before risky commands", "Спрашивать про опасное"), hint: () => t("You approve each dangerous command", "Ты подтверждаешь каждую опасную команду") },
    { value: "deny", icon: "shield", label: () => t("Deny risky commands", "Запрещать опасное"), hint: () => t("Dangerous commands are refused outright", "Опасные команды отклоняются сразу") },
  ]

  function loadRun() {
    try {
      const saved = JSON.parse(K.store.get("run", "{}"))
      return {
        provider: String(saved.provider || ""),
        models: { fast: String(saved.models?.fast || ""), smart: String(saved.models?.smart || "") },
        role: ROLES.some((role) => role.value === saved.role) ? saved.role : "auto",
        approvalMode: saved.approvalMode === "deny" ? "deny" : "ask",
      }
    } catch {
      return { provider: "", models: { fast: "", smart: "" }, role: "auto", approvalMode: "ask" }
    }
  }

  function readImages(files) {
    const images = Array.from(files).filter((file) => file.type.startsWith("image/"))
    if (images.some((file) => file.size > MAX_IMAGE_BYTES)) return Promise.reject(new Error(t("An image is over 5 MiB.", "Картинка больше 5 МиБ.")))
    return Promise.all(images.map((file) => new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onerror = () => reject(new Error(t("Could not read the image.", "Не удалось прочитать картинку.")))
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
        role: document.getElementById("role-chip"),
        model: document.getElementById("model-chip"),
      }
      this.meter = document.getElementById("context-meter")
      this.bind()
      this.updateChips()
      this.updateSend()
    }

    // --- run options -----------------------------------------------------------------
    options() {
      return { provider: this.run.provider, models: { ...this.run.models }, role: this.run.role, approvalMode: this.run.approvalMode }
    }

    saveRun() {
      K.store.set("run", JSON.stringify(this.run))
      this.updateChips()
      this.host.onRunChange?.()
    }

    resetRun() {
      this.run = { provider: "", models: { fast: "", smart: "" }, role: "auto", approvalMode: "ask" }
      this.saveRun()
    }

    providerLabel(id) {
      const info = this.host.getConfig()
      const all = [...(info?.providers || []), ...(info?.custom_providers || [])]
      return all.find((entry) => entry.id === id)?.label || id
    }

    updateChips() {
      const approval = APPROVALS.find((item) => item.value === this.run.approvalMode)
      const role = ROLES.find((item) => item.value === this.run.role)
      K.clear(this.chips.approval)
      this.chips.approval.append(K.icon(approval.icon, 14), h("span", {}, approval.label()))
      K.clear(this.chips.role)
      this.chips.role.append(K.icon("sparkle", 14), h("span", {}, role.label()))
      const model = this.run.models.smart || this.run.models.fast
      const provider = this.run.provider ? this.providerLabel(this.run.provider) : ""
      const text = model ? `${provider ? `${provider} · ` : ""}${model}` : provider || t("Saved model", "Модель по умолчанию")
      K.clear(this.chips.model)
      this.chips.model.append(h("span", { class: "chip-text" }, text), K.icon("chevronDown", 13))
      this.chips.model.title = t("Provider and models for the next task", "Провайдер и модели для следующей задачи")
    }

    // --- chips ----------------------------------------------------------------------------
    openApproval() {
      const list = h("div", { class: "menu" }, h("div", { class: "menu-title" }, t("Permissions", "Разрешения")))
      for (const item of APPROVALS) {
        list.append(K.menuItem({ icon: item.icon, label: item.label(), hint: item.hint(), active: item.value === this.run.approvalMode, onClick: () => { this.run.approvalMode = item.value; this.saveRun() } }))
      }
      K.popover(this.chips.approval, list, { placement: "above" })
    }

    openRole() {
      const list = h("div", { class: "menu" }, h("div", { class: "menu-title" }, t("Agent role", "Роль агента")))
      for (const item of ROLES) {
        list.append(K.menuItem({ icon: "sparkle", label: item.label(), hint: item.hint(), active: item.value === this.run.role, onClick: () => { this.run.role = item.value; this.saveRun() } }))
      }
      K.popover(this.chips.role, list, { placement: "above" })
    }

    async openModel() {
      await this.host.ensureConfig()
      const info = this.host.getConfig()
      const select = h("select", { class: "field-input", "aria-label": t("Provider", "Провайдер") })
      select.append(new Option(t("Saved default", "Сохранённый по умолчанию") + (info?.saved_provider ? ` · ${this.providerLabel(info.saved_provider)}` : ""), ""))
      for (const entry of [...(info?.providers || []), ...(info?.custom_providers || [])]) {
        select.append(new Option(`${entry.has_credentials ? "● " : "○ "}${entry.label}`, entry.id))
      }
      select.value = this.run.provider
      if (select.value !== this.run.provider) select.value = ""

      const fast = h("input", { class: "field-input", list: "model-suggestions", placeholder: t("fast model", "быстрая модель"), value: this.run.models.fast, "aria-label": t("Fast model", "Быстрая модель") })
      const smart = h("input", { class: "field-input", list: "model-suggestions", placeholder: t("smart model", "умная модель"), value: this.run.models.smart, "aria-label": t("Smart model", "Умная модель") })
      const suggestions = h("datalist", { id: "model-suggestions" })
      const hint = h("div", { class: "field-hint" })

      const refresh = () => {
        const summary = [...(info?.providers || []), ...(info?.custom_providers || [])].find((entry) => entry.id === (select.value || info?.saved_provider))
        K.clear(suggestions)
        for (const option of summary?.options || []) suggestions.append(new Option(option.label || "", option.value))
        fast.placeholder = summary?.saved_models?.fast || summary?.defaults?.fast || t("fast model", "быстрая модель")
        smart.placeholder = summary?.saved_models?.smart || summary?.defaults?.smart || t("smart model", "умная модель")
        hint.textContent = summary && !summary.has_credentials
          ? t("No API key saved for this provider: add it in Settings.", "Для этого провайдера нет ключа: добавь его в настройках.")
          : t("Empty fields use the provider's saved or default models.", "Пустые поля берут сохранённые или стандартные модели провайдера.")
        hint.classList.toggle("is-warn", Boolean(summary && !summary.has_credentials))
      }
      const commit = () => {
        const models = { fast: fast.value.trim(), smart: smart.value.trim() }
        if (Boolean(models.fast) !== Boolean(models.smart)) {
          hint.textContent = t("Fill both model fields, or clear both.", "Заполни оба поля модели или очисти оба.")
          hint.classList.add("is-warn")
          return
        }
        this.run.provider = select.value
        this.run.models = models
        this.saveRun()
        refresh()
      }
      select.addEventListener("change", () => {
        fast.value = ""
        smart.value = ""
        commit()
      })
      fast.addEventListener("change", commit)
      smart.addEventListener("change", commit)
      refresh()

      const panel = h(
        "div",
        { class: "model-panel" },
        h("div", { class: "menu-title" }, t("Model for the next task", "Модель для следующей задачи")),
        h("label", { class: "field" }, h("span", { class: "field-label" }, t("Provider", "Провайдер")), select),
        h("label", { class: "field" }, h("span", { class: "field-label" }, t("Fast model", "Быстрая модель")), fast),
        h("label", { class: "field" }, h("span", { class: "field-label" }, t("Smart model", "Умная модель")), smart),
        suggestions,
        hint,
        h(
          "div",
          { class: "model-actions" },
          h("button", { class: "btn small", type: "button", onclick: () => { K.closePopover(); this.host.addProvider() } }, K.icon("plus", 13), t("Custom provider", "Свой провайдер")),
          h("button", { class: "btn small", type: "button", onclick: () => { K.closePopover(); this.host.openSettings("models") } }, K.icon("sliders", 13), t("Settings", "Настройки")),
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
        ? t("Kvist is working… Esc pauses it", "Kvist работает… Esc ставит на паузу")
        : t("Ask Kvist to do something. @ mentions a file, / opens commands", "Попроси Kvist что-нибудь сделать. @ — файл, / — команды")
    }

    updateSend() {
      K.clear(this.send)
      this.send.classList.toggle("is-stop", this.running)
      this.send.append(K.icon(this.running ? "stop" : "arrowUp", this.running ? 18 : 17))
      this.send.setAttribute("aria-label", this.running ? t("Stop", "Остановить") : t("Send", "Отправить"))
      this.send.title = this.running ? t("Stop all", "Остановить всё") : t("Send (Enter)", "Отправить (Enter)")
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
      this.meter.title = t(`Context: ${K.formatTokens(tokens)} of ${K.formatTokens(limit)} tokens`, `Контекст: ${K.formatTokens(tokens)} из ${K.formatTokens(limit)} токенов`)
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
        if (this.images.length + added.length > MAX_IMAGES) throw new Error(t("Up to 8 images.", "Не больше 8 картинок."))
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
          h("button", { class: "thumb-remove", type: "button", "aria-label": t("Remove", "Убрать"), onclick: () => { this.images.splice(index, 1); this.renderTray(); this.updateSend() } }, K.icon("x", 12)),
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
      const picker = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp,image/gif", multiple: true, class: "is-hidden" })
      picker.addEventListener("change", () => { void this.addImages(picker.files); picker.value = "" })
      this.form.append(picker)
      this.attach.addEventListener("click", () => {
        const menu = h("div", { class: "menu" })
        menu.append(
          K.menuItem({ icon: "image", label: t("Attach images", "Прикрепить картинки"), onClick: () => picker.click() }),
          K.menuItem({ icon: "file", label: t("Mention a file", "Упомянуть файл"), hint: "@", onClick: () => { this.setText(`${this.input.value}${this.input.value && !/\s$/.test(this.input.value) ? " " : ""}@`); this.updateSuggest() } }),
          K.menuItem({ icon: "terminal", label: t("Slash commands", "Слэш-команды"), hint: "/", onClick: () => { this.setText("/"); this.updateSuggest() } }),
        )
        K.popover(this.attach, menu, { placement: "above" })
      })
      this.chips.approval.addEventListener("click", () => this.openApproval())
      this.chips.role.addEventListener("click", () => this.openRole())
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
          K.toast(t("Kvist is working. Press Esc to pause it, or use Stop.", "Kvist работает. Esc ставит на паузу, кнопка Стоп останавливает."), { ms: 2800 })
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
