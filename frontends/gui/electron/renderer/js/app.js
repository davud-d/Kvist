"use strict"
// Wires everything together: navigation, the running task, approvals, views.

;(function startApp(root) {
  const K = root.K
  const { h, t } = K
  const $ = (id) => document.getElementById(id)

  // --- state ------------------------------------------------------------------------------
  const thread = new K.Thread()
  const protocol = new root.KvistProtocol.ProtocolState()
  const state = {
    workspace: "",
    projects: [],
    activeSession: "",
    protocolVersion: 1,
    running: false,
    taskState: "idle",
    startedAt: 0,
    requests: [],
    view: "thread",
    settingsSection: "general",
    configInfo: null,
    configStale: true,
    hasCheckpoint: false,
    closedNotice: "",
    git: null,
    lastTitle: "",
  }

  const els = {
    app: $("app"),
    page: $("page"),
    threadView: $("thread-view"),
    empty: $("empty"),
    title: $("thread-title"),
    project: $("thread-project"),
    requestHost: $("request-host"),
    closedBar: $("closed-bar"),
    resumeBar: $("resume-bar"),
    setupBar: $("setup-bar"),
    envbar: $("envbar"),
    reviewButton: $("review-toggle"),
    drawerButton: $("drawer-toggle"),
    commitButton: $("commit-btn"),
  }

  const timeline = new K.Timeline({ scroller: $("timeline"), inner: $("timeline-inner"), jump: $("jump") })
  timeline.attach(thread)

  const composer = new K.Composer({
    getConfig: () => state.configInfo,
    ensureConfig: () => ensureConfig(),
    commands: () => slashCommands(),
    onSubmit: (text, images) => void startTask(text, images),
    onStop: () => void stopTask(),
    openSettings: (section) => showView("settings", section),
    addProvider: () => K.pages.customProviderDialog(() => { state.configStale = true; composer.updateChips() }),
    onRunChange: () => updateSetupBar(),
  })

  const review = new K.ReviewPanel({
    thread: () => thread,
    canWrite: () => !state.running,
    onRevert: () => void confirmRevert(),
    onCommit: () => void commit(),
  })
  const drawer = new K.Drawer({ thread: () => thread })

  const sidebar = new K.Sidebar({
    onNewThread: (path) => void newThread(path),
    onOpenThread: (path, name) => void openThread(path, name),
    onDeleteThread: (path, name, title) => void deleteThread(path, name, title),
    onOpenProject: (path) => void openProject(path),
    onRemoveProject: (path) => void removeProject(path),
    onAddProject: () => void addProject(),
  })

  // --- theme ----------------------------------------------------------------------------------
  K.applyTheme = function applyTheme() {
    const theme = K.store.get("theme", "system")
    if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme
    else delete document.documentElement.dataset.theme
    void root.kvist.setTheme(theme)
  }

  // --- configuration --------------------------------------------------------------------------
  async function ensureConfig() {
    if (state.configInfo && !state.configStale) return state.configInfo
    return refreshConfig()
  }

  async function refreshConfig() {
    state.configInfo = await root.kvist.configInfo()
    state.configStale = false
    composer.updateChips()
    updateSetupBar()
    return state.configInfo
  }

  // The provider the next task will use is the one picked in the composer, else
  // the saved default. An empty config still names a default (OpenAI), so what
  // matters is whether that provider has credentials.
  function updateSetupBar() {
    const info = state.configInfo
    if (!info) return
    const id = composer.run.provider || info.saved_provider
    const provider = [...(info.providers || []), ...(info.custom_providers || [])].find((entry) => entry.id === id)
    els.setupBar.classList.toggle("is-hidden", Boolean(provider?.has_credentials))
  }

  // --- navigation --------------------------------------------------------------------------------
  function currentProject() {
    return state.projects.find((project) => project.path === state.workspace) || null
  }

  function sessionItem(project, name) {
    return project?.status?.session_items?.find((item) => item.name === name) || null
  }

  function applyNavigation(navigation) {
    state.projects = navigation.projects || []
    state.workspace = navigation.workspace || state.workspace
    const project = currentProject()
    state.activeSession = project?.status?.active_session || ""
    const item = sessionItem(project, state.activeSession)
    state.hasCheckpoint = Boolean(item?.has_checkpoint)
    state.closedNotice = item?.closed ? item.closed_notice || " " : ""
    renderChrome()
  }

  async function refreshNavigation() {
    try {
      applyNavigation(await root.kvist.navigation())
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
  }

  function threadTitle() {
    const item = sessionItem(currentProject(), state.activeSession)
    if (thread.items.some((entry) => entry.kind === "user") || item?.has_checkpoint) return item?.title || state.lastTitle || t("New thread", "Новый тред")
    return t("New thread", "Новый тред")
  }

  function renderChrome() {
    sidebar.render({ projects: state.projects, workspace: state.workspace, activeSession: state.activeSession, running: state.running, awaiting: state.requests.length > 0 })
    const title = state.view === "thread" ? threadTitle() : { skills: t("Skills", "Навыки"), memory: t("Memory", "Память"), settings: t("Settings", "Настройки") }[state.view]
    els.title.textContent = title
    els.project.textContent = state.view === "thread" ? K.baseName(state.workspace) : ""
    document.title = `${title} · Kvist`
    renderEnvbar()
    renderStateBars()
    renderEmpty()
    updateReviewButton()
  }

  function renderStateBars() {
    const closed = Boolean(thread.closedNotice || state.closedNotice)
    const text = (thread.closedNotice && thread.closedNotice.trim()) || (state.closedNotice && state.closedNotice.trim()) || ""
    els.closedBar.classList.toggle("is-hidden", !closed)
    els.closedBar.replaceChildren(closed ? h("span", {}, text || t("This conversation is closed.", "Этот разговор закрыт.")) : "", closed ? h("button", { class: "btn small", type: "button", onclick: () => void newThread(state.workspace) }, t("New thread", "Новый тред")) : "")
    composer.setLocked(closed || state.requests.length > 0)
    const resumable = state.hasCheckpoint && !state.running && !closed && state.requests.length === 0
    els.resumeBar.classList.toggle("is-hidden", !resumable)
    if (resumable) {
      els.resumeBar.replaceChildren(
        K.icon("pause", 14),
        h("span", {}, t("This thread has an unfinished task.", "В этом треде есть незавершённая задача.")),
        h("button", { class: "btn small primary", type: "button", onclick: () => void resumeTask() }, t("Resume", "Продолжить")),
      )
    }
  }

  function renderEnvbar() {
    const git = state.git
    const parts = [
      h("span", { class: "env-item", title: state.workspace }, K.icon("folder", 13), K.baseName(state.workspace) || "—"),
      h("span", { class: "env-item" }, K.icon("monitor", 13), t("Local", "Локально")),
    ]
    if (git?.isRepo) {
      parts.push(h("span", { class: "env-item", title: t("Current branch", "Текущая ветка") }, K.icon("branch", 13), git.branch))
      if (git.files.length) parts.push(h("span", { class: "env-item is-dirty" }, t(`${git.files.length} changed`, `изменено: ${git.files.length}`)))
    }
    els.envbar.replaceChildren(...parts)
    els.commitButton.disabled = !git?.isRepo || !git.files.length || state.running
  }

  function updateReviewButton() {
    const changes = thread.changes()
    const sum = K.totals(K.mergeFiles(changes.flatMap((entry) => entry.patches)))
    const label = els.reviewButton.querySelector(".review-counts")
    label.replaceChildren(...(sum.added || sum.removed ? [h("span", { class: "plus" }, `+${sum.added}`), h("span", { class: "minus" }, `−${sum.removed}`)] : []))
  }

  async function refreshGit() {
    try {
      state.git = await root.kvist.gitInfo()
    } catch {
      state.git = null
    }
    renderEnvbar()
  }

  function renderEmpty() {
    const empty = state.view === "thread" && !thread.items.length
    els.empty.classList.toggle("is-hidden", !empty)
    $("timeline").classList.toggle("is-hidden", empty)
    if (!empty) return
    const prompts = [
      [t("Explain how this project is structured", "Объясни, как устроен этот проект"), "search"],
      [t("Find and fix a bug", "Найди и исправь баг"), "bolt"],
      [t("Write tests for the main module", "Напиши тесты для главного модуля"), "check"],
      [t("Review my uncommitted changes without editing anything", "Проверь мои незакоммиченные изменения, ничего не правя"), "review"],
    ]
    els.empty.replaceChildren(
      h("img", { class: "empty-mark", src: "kvist-mark.svg", alt: "" }),
      h("h1", {}, t("What should we build in ", "Что будем делать в "), h("span", { class: "accent" }, K.baseName(state.workspace) || "…"), "?"),
      h("div", { class: "suggestions" }, prompts.map(([text, icon]) => h("button", { class: "suggestion", type: "button", onclick: () => composer.setText(text) }, K.icon(icon, 15), h("span", {}, text)))),
    )
  }

  // --- views ----------------------------------------------------------------------------------------
  function showView(view, section) {
    state.view = view
    if (section) state.settingsSection = section
    els.threadView.classList.toggle("is-hidden", view !== "thread")
    els.page.classList.toggle("is-hidden", view === "thread")
    for (const button of document.querySelectorAll("[data-nav]")) button.classList.toggle("is-active", button.dataset.nav === view)
    $("topbar-actions").classList.toggle("is-hidden", view !== "thread")
    renderChrome()
    if (view === "thread") {
      composer.focus()
      return
    }
    const ctx = {
      refreshConfig,
      addProvider: (done) => K.pages.customProviderDialog(() => { state.configStale = true; composer.updateChips(); done?.() }),
      openSettings: (next) => showView("settings", next),
      isRunning: () => state.running,
      onReset: () => { state.configStale = true; composer.resetRun(); void refreshConfig() },
      protocolVersion: state.protocolVersion,
      workspace: state.workspace,
    }
    if (view === "skills") void K.pages.skills(els.page)
    else if (view === "memory") void K.pages.memory(els.page)
    else void K.pages.settings(els.page, ctx, state.settingsSection)
  }

  // --- threads ----------------------------------------------------------------------------------------
  function requireIdle() {
    if (!state.running) return true
    K.toast(t("Finish or stop the current task first.", "Сначала дождись конца задачи или останови её."), { kind: "warn" })
    return false
  }

  async function loadThread(projectPath, name) {
    thread.reset()
    state.closedNotice = ""
    let payload = null
    try {
      payload = await root.kvist.sessionHistory(projectPath, name)
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
    if (payload) {
      thread.applyAll(payload.events || [])
      thread.endTurn()
      if (payload.has_more) thread.items.unshift({ id: 0, kind: "notice", level: "info", text: t("Older messages of this thread are not shown.", "Более старые сообщения этого треда не показаны.") })
      if (payload.closed) thread.closedNotice = payload.closed_notice || " "
    }
    timeline.renderAll()
    drawer.render()
    review.refresh()
    renderChrome()
  }

  async function openThread(projectPath, name) {
    if (!requireIdle()) return
    showView("thread")
    try {
      const result = await root.kvist.switchSession(projectPath, name)
      applyNavigation(result.navigation)
      await loadThread(projectPath, name)
      void refreshGit()
      composer.focus()
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
  }

  async function newThread(projectPath) {
    if (!requireIdle()) return
    try {
      const result = await root.kvist.newSession(projectPath || state.workspace)
      applyNavigation(result.navigation)
      thread.reset()
      state.closedNotice = ""
      timeline.renderAll()
      drawer.render()
      review.refresh()
      showView("thread")
      void refreshGit()
      composer.focus()
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
  }

  async function deleteThread(projectPath, name, title) {
    if (!requireIdle()) return
    if (!(await K.confirm({ title: t("Delete this thread?", "Удалить тред?"), text: title, confirmLabel: t("Delete", "Удалить"), danger: true }))) return
    try {
      applyNavigation((await root.kvist.deleteSession(projectPath, name)).navigation)
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
  }

  async function openProject(path) {
    if (!requireIdle()) return
    try {
      applyNavigation(await root.kvist.activateProject(path))
      await loadThread(state.workspace, state.activeSession)
      showView("thread")
      void refreshGit()
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
  }

  async function addProject() {
    if (!requireIdle()) return
    try {
      const chosen = await root.kvist.chooseWorkspace()
      if (!chosen) return
      await refreshNavigation()
      await loadThread(state.workspace, state.activeSession)
      showView("thread")
      void refreshGit()
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
  }

  async function removeProject(path) {
    if (!requireIdle()) return
    if (path === state.workspace) {
      K.toast(t("This is the current project. Open another one first.", "Это текущий проект. Сначала открой другой."), { kind: "warn" })
      return
    }
    try {
      applyNavigation(await root.kvist.removeProject(path))
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
  }

  // --- the running task -----------------------------------------------------------------------------------
  function setRunning(running) {
    state.running = running
    if (!running) state.taskState = "idle"
    composer.setRunning(running)
    els.app.classList.toggle("is-running", running)
    updateLive()
    renderChrome()
  }

  function goalText(text, images) {
    return images.length ? JSON.stringify({ type: "kvist-image-goal", text, images }) : text
  }

  async function startTask(text, images) {
    if (state.running || !(await ensureConfigSafe())) return
    showView("thread")
    thread.addUser(text, { images: images.length })
    thread.markLiveStart()
    state.startedAt = Date.now()
    protocol.reset()
    setRunning(true)
    timeline.scrollToEnd(true)
    renderEmpty()
    void root.kvist.inputHistoryAdd(text).catch(() => {})
    try {
      await root.kvist.start(goalText(text, images), state.workspace, composer.options())
    } catch (error) {
      failTask(error.message)
    }
  }

  async function ensureConfigSafe() {
    try {
      await ensureConfig()
    } catch {
      // A broken config read must not block a task; the core reports its own error.
    }
    return true
  }

  async function resumeTask() {
    if (state.running) return
    thread.markLiveStart()
    state.startedAt = Date.now()
    protocol.reset()
    setRunning(true)
    try {
      await root.kvist.resume(state.workspace, composer.options())
    } catch (error) {
      failTask(error.message)
    }
  }

  function failTask(message) {
    thread.apply({ type: "error", message })
    state.requests = []
    renderRequest()
    setRunning(false)
  }

  async function stopTask() {
    if (!state.running) return
    try {
      await root.kvist.stopAll()
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
    // A task that ignores "stop all" is killed after a grace period.
    root.setTimeout(() => { if (state.running) void root.kvist.stop().catch(() => {}) }, 15000)
  }

  async function pauseTask() {
    if (!state.running || ["pause_requested", "paused", "cancelling"].includes(state.taskState)) return
    try {
      await root.kvist.pause()
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
  }

  const TASK_STATE_TEXT = {
    pause_requested: () => t("Pausing: waiting for the current step to finish (Stop ends it now)…", "Пауза: жду конца текущего шага (Стоп прервёт сразу)…"),
    paused: () => t("Paused", "Пауза"),
    cancelling: () => t("Stopping the agent and everything it started…", "Останавливаю агента и всё, что он запустил…"),
    awaiting_input: () => t("Waiting for your answer", "Жду твой ответ"),
  }

  const liveActions = h("span", { class: "live-actions" },
    h("button", { class: "btn small", type: "button", onclick: () => void pauseTask() }, K.icon("pause", 13), t("Pause", "Пауза")),
    h("button", { class: "btn small", type: "button", onclick: () => void stopTask() }, K.icon("stop", 13), t("Stop", "Стоп")),
  )

  function updateLive() {
    if (!state.running) {
      timeline.setLive({ running: false })
      return
    }
    const text = TASK_STATE_TEXT[state.taskState]?.() || thread.status.text || t("Working…", "Работаю…")
    const seconds = Math.floor((Date.now() - state.startedAt) / 1000)
    timeline.setLive({ running: true, text, elapsed: K.duration(seconds), actions: liveActions })
    timeline.liveNode.classList.toggle("is-waiting", state.taskState === "awaiting_input")
  }
  root.setInterval(() => { if (state.running) updateLive() }, 1000)

  // --- protocol events ------------------------------------------------------------------------------------------
  function notifyIfAway(title, body) {
    if (document.hasFocus()) return
    try {
      new Notification(title, { body })
    } catch {
      // Notifications are a nicety.
    }
  }

  function handleEvent(event) {
    const action = protocol.consume(event)
    if (action.kind === "protocol_error") {
      thread.apply({ type: "error", message: action.message })
      setRunning(false)
      return
    }
    if (action.kind === "ignore" || action.kind === "hello") return
    switch (event.type) {
      case "input_request":
        state.requests.push(event)
        renderRequest()
        notifyIfAway("Kvist", event.title || t("Kvist needs your answer", "Kvist ждёт твой ответ"))
        return
      case "input_closed":
        state.requests = state.requests.filter((request) => request.request_id !== event.request_id)
        renderRequest()
        return
      case "notice":
        K.toast(event.text || "", { kind: event.level === "error" ? "error" : event.level === "warn" ? "warn" : "info" })
        return
      case "debug":
        if (K.store.get("debug") !== "1") return
        thread.apply({ type: "notice", level: "info", text: event.message || "" })
        return
      case "session_title":
        void refreshNavigation()
        return
      case "task_state":
        state.taskState = event.state || "idle"
        updateLive()
        break
      case "status":
        thread.apply(event)
        updateLive()
        return
      default:
        break
    }
    thread.apply(event)
    if (event.type === "usage" || event.type === "context") composer.setContext(thread.stats.contextTokens, thread.stats.contextLimit)
    if (event.type === "action_result" || event.type === "step") { drawer.schedule(); review.open && review.tab === "agent" && review.refresh(); updateReviewButton() }
    if (event.type === "action_output" || event.type === "job_output") drawer.schedule()
    if (event.type === "final" || event.type === "error") onTurnEnded(event)
  }

  function onTurnEnded(event) {
    setRunning(false)
    state.requests = []
    renderRequest()
    void refreshNavigation()
    void refreshGit()
    if (review.open) void review.refresh()
    const body = event.type === "final" ? String(event.message || "").slice(0, 140) : event.message
    notifyIfAway(t("Kvist finished", "Kvist закончил"), body)
  }

  // --- approvals and questions -------------------------------------------------------------------------------------------
  async function answer(request, payload) {
    state.requests = state.requests.filter((entry) => entry.request_id !== request.request_id)
    try {
      await root.kvist.respond({ request_id: request.request_id, ...payload })
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
    renderRequest()
  }

  function renderRequest() {
    const request = state.requests[0]
    els.requestHost.replaceChildren()
    els.requestHost.classList.toggle("is-hidden", !request)
    renderChrome()
    if (!request) return
    const panel = h("div", { class: "request", role: "alertdialog", "aria-label": request.title })
    panel.append(h("div", { class: "request-title" }, K.icon(request.kind === "confirm" ? "shield" : "info", 16), h("span", {}, request.title)))
    if (request.subject?.kind === "overwrite" && request.subject.diff) {
      const files = K.diff.parseUnifiedDiff(request.subject.diff)
      panel.append(h("div", { class: "request-diff" }, files.length ? files.map((file) => K.diff.renderDiffFile(file, { open: true })) : h("pre", { class: "term" }, request.subject.diff)))
    } else if (request.subject?.kind === "command") {
      panel.append(h("pre", { class: "term request-command" }, h("span", { class: "prompt" }, "$ "), request.subject.command || ""))
      if (request.subject.reason) panel.append(h("div", { class: "request-body" }, request.subject.reason))
    } else if (request.body) {
      panel.append(h("pre", { class: "request-body is-pre" }, request.body))
    }
    if (request.help_text) panel.append(h("div", { class: "request-hint" }, request.help_text))
    if (request.timeout_seconds) panel.append(h("div", { class: "request-hint" }, t(`Unanswered in ${Math.round(request.timeout_seconds)} s counts as “no”.`, `Без ответа за ${Math.round(request.timeout_seconds)} с считается отказом.`)))

    const actions = h("div", { class: "request-actions" })
    let focus = null
    if (request.kind === "confirm") {
      const deny = h("button", { class: "btn", type: "button", onclick: () => void answer(request, { approved: false }) }, t("Deny", "Отклонить"))
      const allow = h("button", { class: "btn primary", type: "button", onclick: () => void answer(request, { approved: true }) }, t("Allow", "Разрешить"))
      actions.append(deny)
      if (request.remember_pattern) actions.append(h("button", { class: "btn", type: "button", title: request.remember_pattern, onclick: () => void answer(request, { approved: true, remember: true }) }, t("Always allow", "Всегда разрешать")))
      actions.append(allow)
      focus = deny
    } else if (request.kind === "choice") {
      const list = h("div", { class: "request-options" })
      for (const option of request.options || []) {
        const button = h("button", { class: `request-option${option.value === request.current_value ? " is-current" : ""}`, type: "button", onclick: () => void answer(request, { value: option.value }) }, h("span", { class: "option-label" }, option.label), option.description ? h("span", { class: "option-desc" }, option.description) : null)
        list.append(button)
        focus = focus || button
      }
      panel.append(list)
      actions.append(h("button", { class: "btn", type: "button", onclick: () => void answer(request, { value: null }) }, t("Cancel", "Отмена")))
    } else {
      const secret = request.kind === "secret"
      const input = secret
        ? h("input", { class: "field-input", type: "password", autocomplete: "off" })
        : h("textarea", { class: "field-input", rows: "3" })
      if (!secret) input.value = request.current_value || ""
      const submit = () => void answer(request, { value: input.value })
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); submit() }
      })
      panel.append(input)
      actions.append(h("button", { class: "btn", type: "button", onclick: () => void answer(request, { value: null }) }, t("Cancel", "Отмена")), h("button", { class: "btn primary", type: "button", onclick: submit }, t("Send", "Отправить")))
      focus = input
    }
    panel.append(actions)
    els.requestHost.append(panel)
    if (state.requests.length > 1) panel.append(h("div", { class: "request-hint" }, t(`${state.requests.length - 1} more waiting`, `Ещё в очереди: ${state.requests.length - 1}`)))
    root.requestAnimationFrame(() => focus?.focus())
    timeline.scrollToEnd(false)
  }

  // --- review / commit / revert -----------------------------------------------------------------------------------------------
  async function confirmRevert() {
    if (!requireIdle()) return
    const ok = await K.confirm({
      title: t("Revert the agent's changes?", "Откатить изменения агента?"),
      text: t("Restores every file the agent changed (in all threads of this project) to its recorded baseline. Your own later edits are kept.", "Вернёт каждый файл, который менял агент (во всех тредах проекта), к записанному исходному состоянию. Твои более поздние правки сохранятся."),
      confirmLabel: t("Revert", "Откатить"),
      danger: true,
    })
    if (!ok) return
    try {
      const result = await root.kvist.revert()
      const changed = (result.reverted || []).filter((item) => item.action !== "skipped")
      K.toast(changed.length ? t(`Reverted ${changed.length} file(s).`, `Откатил файлов: ${changed.length}.`) : t("Nothing needed reverting.", "Откатывать было нечего."))
      void refreshGit()
      void review.refresh()
    } catch (error) {
      K.toast(error.message, { kind: "error" })
    }
  }

  async function commit() {
    if (!requireIdle()) return
    await K.openCommitDialog({ onDone: () => { void refreshGit(); void review.refresh() } })
  }

  // --- commands and the palette --------------------------------------------------------------------------------------------------------
  function slashCommands() {
    return [
      { name: "new", icon: "compose", label: t("Start a new thread", "Новый тред"), run: () => void newThread(state.workspace) },
      { name: "resume", icon: "undo", label: t("Resume the unfinished task", "Продолжить незавершённую задачу"), run: () => (state.hasCheckpoint ? void resumeTask() : K.toast(t("Nothing to resume here.", "Здесь нечего продолжать."))) },
      { name: "review", icon: "review", label: t("Open the review panel", "Открыть панель Review"), run: () => review.setOpen(true) },
      { name: "commit", icon: "branch", label: t("Commit the working tree", "Закоммитить изменения"), run: () => void commit() },
      { name: "commands", icon: "terminal", label: t("Show the commands drawer", "Показать панель команд"), run: () => drawer.setOpen(true) },
      { name: "skills", icon: "sparkle", label: t("Browse skills", "Навыки"), run: () => showView("skills") },
      { name: "memory", icon: "memory", label: t("Browse memory", "Память"), run: () => showView("memory") },
      { name: "folder", icon: "folder", label: t("Open the project folder", "Открыть папку проекта"), run: () => void root.kvist.openProjectFolder().catch((error) => K.toast(error.message, { kind: "error" })) },
      { name: "settings", icon: "sliders", label: t("Open settings", "Настройки"), run: () => showView("settings") },
    ]
  }

  function openPalette() {
    const input = h("input", { class: "palette-input", placeholder: t("Search threads and commands", "Искать треды и команды"), "aria-label": t("Search", "Поиск") })
    const list = h("div", { class: "palette-list", role: "listbox" })
    let entries = []
    let index = 0
    let modal

    const collect = (query) => {
      const needle = query.trim().toLowerCase()
      const threads = []
      for (const project of state.projects) {
        for (const item of project.status?.session_items || []) {
          const title = item.title || item.name
          if (needle && !`${title} ${K.baseName(project.path)}`.toLowerCase().includes(needle)) continue
          threads.push({ icon: "file", label: title, hint: K.baseName(project.path), updated: Number(item.updated_at) || 0, run: () => void openThread(project.path, item.name) })
        }
      }
      threads.sort((a, b) => b.updated - a.updated)
      const commands = [
        { icon: "compose", label: t("New thread", "Новый тред"), hint: "Ctrl+N", run: () => void newThread(state.workspace) },
        { icon: "folderPlus", label: t("Add a project folder", "Добавить папку проекта"), run: () => void addProject() },
        { icon: "sparkle", label: t("Skills", "Навыки"), run: () => showView("skills") },
        { icon: "memory", label: t("Memory", "Память"), run: () => showView("memory") },
        { icon: "review", label: t("Toggle review panel", "Панель Review"), hint: "Ctrl+Shift+D", run: () => review.toggle() },
        { icon: "terminal", label: t("Toggle commands drawer", "Панель команд"), hint: "Ctrl+J", run: () => drawer.toggle() },
        { icon: "sliders", label: t("Settings", "Настройки"), hint: "Ctrl+,", run: () => showView("settings") },
      ].filter((command) => !needle || command.label.toLowerCase().includes(needle))
      return [...commands, ...threads.slice(0, 20)]
    }

    const draw = () => {
      K.clear(list)
      entries.forEach((entry, position) => {
        const row = h("button", { class: `palette-row${position === index ? " is-active" : ""}`, type: "button", role: "option" }, K.icon(entry.icon, 15), h("span", { class: "palette-label" }, entry.label), entry.hint ? h("span", { class: "palette-hint" }, entry.hint) : null)
        row.addEventListener("click", () => { modal.close(); entry.run() })
        list.append(row)
      })
      if (!entries.length) list.append(h("div", { class: "panel-empty small" }, t("Nothing found", "Ничего не найдено")))
      list.querySelector(".is-active")?.scrollIntoView({ block: "nearest" })
    }
    input.addEventListener("input", () => { entries = collect(input.value); index = 0; draw() })
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault()
        index = (index + (event.key === "ArrowDown" ? 1 : -1) + entries.length) % Math.max(entries.length, 1)
        draw()
      } else if (event.key === "Enter" && entries[index]) {
        event.preventDefault()
        const chosen = entries[index]
        modal.close()
        chosen.run()
      }
    })
    entries = collect("")
    modal = K.modal({ title: t("Search", "Поиск"), body: h("div", { class: "palette" }, input, list), wide: true })
    modal.dialog.classList.add("is-palette")
    draw()
  }

  // --- shell wiring ---------------------------------------------------------------------------------------------------------------------
  function toggleSidebar() {
    const collapsed = els.app.classList.toggle("sidebar-collapsed")
    K.store.set("sidebar", collapsed ? "0" : "1")
  }

  function bindShell() {
    $("nav-new").addEventListener("click", () => void newThread(state.workspace))
    $("nav-search").addEventListener("click", openPalette)
    $("nav-skills").addEventListener("click", () => showView("skills"))
    $("nav-memory").addEventListener("click", () => showView("memory"))
    $("nav-settings").addEventListener("click", () => showView("settings"))
    $("add-project").addEventListener("click", () => void addProject())
    $("sidebar-toggle").addEventListener("click", toggleSidebar)
    $("sidebar-open").addEventListener("click", toggleSidebar)
    els.reviewButton.addEventListener("click", () => review.toggle())
    els.drawerButton.addEventListener("click", () => drawer.toggle())
    els.commitButton.addEventListener("click", () => void commit())
    $("open-folder").addEventListener("click", () => void root.kvist.openProjectFolder().catch((error) => K.toast(error.message, { kind: "error" })))
    $("drawer-close").addEventListener("click", () => drawer.setOpen(false))
    $("review-close").addEventListener("click", () => review.setOpen(false))
    $("setup-open").addEventListener("click", () => showView("settings", "models"))
    if (K.store.get("sidebar", "1") === "0") els.app.classList.add("sidebar-collapsed")

    document.addEventListener("keydown", (event) => {
      const ctrl = event.ctrlKey || event.metaKey
      const key = event.key.toLowerCase()
      if (ctrl && key === "n") { event.preventDefault(); void newThread(state.workspace) }
      else if (ctrl && key === "k") { event.preventDefault(); openPalette() }
      else if (ctrl && key === "b") { event.preventDefault(); toggleSidebar() }
      else if (ctrl && key === "j") { event.preventDefault(); drawer.toggle() }
      else if (ctrl && event.shiftKey && key === "d") { event.preventDefault(); review.toggle() }
      else if (ctrl && key === ",") { event.preventDefault(); showView("settings") }
      else if (event.key === "Escape" && state.running && !document.querySelector(".modal-backdrop, .popover") && !composer.suggestions.length) { event.preventDefault(); void pauseTask() }
    })
  }

  // --- start-up -----------------------------------------------------------------------------------------------------------------------------
  root.kvist.onEvent(handleEvent)
  root.kvist.onProtocolError(({ message }) => { thread.apply({ type: "error", message }); setRunning(false) })
  root.kvist.onProcessError(({ message }) => { thread.apply({ type: "error", message }); state.requests = []; renderRequest(); setRunning(false) })
  root.kvist.onExit(() => {
    if (state.running) {
      // The process ended without a final event (stopped, or it crashed): close the turn.
      thread.endTurn()
      setRunning(false)
    }
    state.requests = []
    renderRequest()
    void refreshNavigation()
    void refreshGit()
  })

  async function boot() {
    K.applyTheme()
    bindShell()
    document.documentElement.lang = K.lang
    try {
      const boot = await root.kvist.bootstrap()
      state.protocolVersion = boot.protocolVersion
      applyNavigation(boot)
      await loadThread(state.workspace, state.activeSession)
      void refreshGit()
      void refreshConfig().catch((error) => K.toast(error.message, { kind: "warn" }))
      if (boot.initialGoal) composer.setText(boot.initialGoal)
    } catch (error) {
      K.toast(error.message, { kind: "error", ms: 9000 })
    }
    showView("thread")
  }

  // Test hooks for the end-to-end smoke (kept tiny and read-only).
  root.__kvist = { thread, state, showView, openPalette }
  void boot()
})(window)
