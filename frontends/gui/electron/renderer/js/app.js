"use strict"
// Wires everything together: navigation, the running task, approvals, views.

;(function startApp(root) {
  const K = root.K
  const { h } = K
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
    skills: null,
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
    strip: $("strip"),
    reviewButton: $("rail-review"),
    drawerButton: $("rail-commands"),
    commitButton: $("commit-btn"),
    panelThreads: $("panel-threads"),
    panelSettings: $("panel-settings"),
    settingsNav: $("settings-nav"),
    settingsSearch: $("settings-search"),
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
    chooseProject: () => openProjectMenu($("attach-btn")),
    skillList: () => loadSkills(),
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
    if (thread.items.some((entry) => entry.kind === "user") || item?.has_checkpoint) return item?.title || state.lastTitle || "New thread"
    return "New thread"
  }

  function renderChrome() {
    sidebar.render({ projects: state.projects, workspace: state.workspace, activeSession: state.activeSession, running: state.running, awaiting: state.requests.length > 0 })
    const inThread = state.view === "thread"
    const title = inThread ? threadTitle() : { skills: "Skills", memory: "Memory", settings: "Settings" }[state.view]
    els.title.textContent = title
    els.project.textContent = ""
    document.title = `${title} · Kvist`
    const hasThread = inThread && thread.items.length > 0
    $("thread-title-icon").classList.toggle("is-hidden", !hasThread)
    els.title.classList.toggle("is-hidden", !hasThread && inThread)
    $("rail-threads").classList.toggle("has-dot", state.requests.length > 0)
    renderStrip()
    renderStateBars()
    renderEmpty()
    updateReviewButton()
  }

  function renderStateBars() {
    const closed = Boolean(thread.closedNotice || state.closedNotice)
    const text = (thread.closedNotice && thread.closedNotice.trim()) || (state.closedNotice && state.closedNotice.trim()) || ""
    els.closedBar.classList.toggle("is-hidden", !closed)
    els.closedBar.replaceChildren(closed ? h("span", {}, text || "This conversation is closed.") : "", closed ? h("button", { class: "btn small", type: "button", onclick: () => void newThread(state.workspace) }, "New thread") : "")
    composer.setLocked(closed || state.requests.length > 0)
    const resumable = state.hasCheckpoint && !state.running && !closed && state.requests.length === 0
    els.resumeBar.classList.toggle("is-hidden", !resumable)
    if (resumable) {
      els.resumeBar.replaceChildren(
        K.icon("pause", 14),
        h("span", {}, "This thread has an unfinished task."),
        h("button", { class: "btn small primary", type: "button", onclick: () => void resumeTask() }, "Resume"),
      )
    }
  }

  // The row above the message box: which project and where it runs, like Codex's
  // "Choose project / This computer" strip, plus the branch when it is a repository.
  function renderStrip() {
    const git = state.git
    const project = h("button", { class: "strip-item is-on", type: "button", title: state.workspace }, K.icon("folder", 16), K.baseName(state.workspace) || "Choose a project", K.icon("chevronDown", 13))
    project.addEventListener("click", () => openProjectMenu(project))
    const where = h("button", { class: "strip-item is-on", type: "button" }, K.icon("laptop", 16), "This computer", K.icon("chevronDown", 13))
    where.addEventListener("click", () => {
      const menu = h("div", { class: "menu" }, h("div", { class: "menu-title" }, "Work in"))
      menu.append(
        K.menuItem({ icon: "laptop", label: "This computer", checked: true }),
        K.menuItem({ icon: "globe", label: "Remote server", description: "Not available in this app. Use the terminal app with --remote.", disabled: true }),
      )
      K.popover(where, menu, { placement: "above" })
    })
    const parts = [project, where]
    if (git?.isRepo) {
      parts.push(h("span", { class: "strip-item", title: "Current branch" }, K.icon("branch", 16), git.branch))
      if (git.files.length) parts.push(h("span", { class: "strip-item is-dirty" }, `${git.files.length} changed`))
    }
    els.strip.replaceChildren(...parts)
    els.commitButton.disabled = !git?.isRepo || !git.files.length || state.running
  }

  // Project picker: a search box over the remembered projects, then "New project".
  function openProjectMenu(anchor) {
    const search = h("input", { class: "palette-input", placeholder: "Search projects", "aria-label": "Search projects" })
    const list = h("div", { class: "menu" })
    const draw = () => {
      K.clear(list)
      const needle = search.value.trim().toLowerCase()
      for (const project of state.projects.filter((entry) => !needle || K.baseName(entry.path).toLowerCase().includes(needle))) {
        list.append(K.menuItem({ icon: "folder", label: K.baseName(project.path), hint: "", checked: project.path === state.workspace, disabled: !project.available, onClick: () => void openProject(project.path) }))
      }
      list.append(K.menuItem({ icon: "plus", label: "New project", onClick: () => void addProject() }))
    }
    search.addEventListener("input", draw)
    draw()
    const node = K.popover(anchor, h("div", {}, search, list), { placement: "above" })
    node.querySelector("input").style.height = "40px"
    search.focus()
  }

  async function loadSkills() {
    if (!state.skills) state.skills = (await root.kvist.skills()).skills || []
    return state.skills
  }

  function updateReviewButton() {
    const changes = thread.changes()
    const sum = K.totals(K.mergeFiles(changes.flatMap((entry) => entry.patches)))
    els.reviewButton.classList.toggle("has-dot", changes.length > 0)
    renderSummaryCard(sum, changes.length)
  }

  // Codex's floating card at the top right of a thread: what this thread changed and ran.
  function renderSummaryCard(sum, files) {
    let node = $("summary-card")
    const commands = thread.shellCalls().length
    const show = state.view === "thread" && (files > 0 || commands > 0)
    els.threadView.classList.toggle("has-card", show)
    if (!show) { node?.remove(); return }
    if (!node) {
      node = h("aside", { id: "summary-card", class: "summary-card", "aria-label": "Thread summary" })
      els.threadView.append(node)
    }
    const rows = [h("div", { class: "sc-title" }, K.baseName(state.workspace))]
    if (files > 0) {
      rows.push(h("button", { class: "sc-row", type: "button", onclick: () => { review.tab = "agent"; review.setOpen(true) } }, K.icon("review", 16), h("span", { class: "sc-label" }, "Changes"), K.diff.counts(sum)))
    }
    if (commands > 0) {
      rows.push(h("button", { class: "sc-row", type: "button", onclick: () => drawer.setOpen(true) }, K.icon("terminal", 16), h("span", { class: "sc-label" }, "Commands"), h("span", { class: "card-meta" }, String(commands))))
    }
    node.replaceChildren(...rows)
  }

  async function refreshGit() {
    try {
      state.git = await root.kvist.gitInfo()
    } catch {
      state.git = null
    }
    renderStrip()
  }

  function renderEmpty() {
    const empty = state.view === "thread" && !thread.items.length
    els.empty.classList.toggle("is-hidden", !empty)
    $("timeline").classList.toggle("is-hidden", empty)
    if (!empty) return
    const dirty = state.git?.isRepo ? state.git.files.length : 0
    els.empty.replaceChildren(...[
      K.icon("terminal", 52),
      h("h1", {}, "What should we build in ", h("span", { class: "project-word" }, K.baseName(state.workspace) || "…"), "?"),
      dirty ? h("button", { class: "strip-item", type: "button", onclick: () => composer.setText("Review my uncommitted changes. Do not edit anything.") }, K.icon("review", 16), `Review ${dirty} uncommitted change${dirty > 1 ? "s" : ""}`) : null,
    ].filter(Boolean))
    els.empty.firstElementChild.classList.add("empty-mark")
  }

  // --- views ----------------------------------------------------------------------------------------
  function showView(view, section) {
    state.view = view
    if (section) state.settingsSection = section
    els.threadView.classList.toggle("is-hidden", view !== "thread")
    els.page.classList.toggle("is-hidden", view === "thread")
    for (const button of document.querySelectorAll("[data-nav]")) button.classList.toggle("is-active", button.dataset.nav === view)
    $("topbar-actions").classList.toggle("is-hidden", view !== "thread")
    const settings = view === "settings"
    els.panelThreads.classList.toggle("is-hidden", settings)
    els.panelSettings.classList.toggle("is-hidden", !settings)
    if (settings) renderSettingsNav()
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
      approvalMode: () => composer.run.approvalMode,
      setApprovalMode: (mode) => { composer.run.approvalMode = mode; composer.saveRun() },
      protocolVersion: state.protocolVersion,
      workspace: state.workspace,
    }
    if (view === "skills") void K.pages.skills(els.page)
    else if (view === "memory") void K.pages.memory(els.page)
    else void K.pages.settings(els.page, ctx, state.settingsSection)
  }

  // Settings replace the thread lists in the side panel with their own sections.
  function renderSettingsNav() {
    const needle = els.settingsSearch.value.trim().toLowerCase()
    K.clear(els.settingsNav)
    for (const section of K.pages.SECTIONS.filter((entry) => !needle || entry.label.toLowerCase().includes(needle))) {
      const row = h("button", { class: `panel-row${section.id === state.settingsSection ? " is-active" : ""}`, type: "button" }, K.icon(section.icon, 17), h("span", {}, section.label))
      row.addEventListener("click", () => showView("settings", section.id))
      els.settingsNav.append(row)
    }
  }

  // --- threads ----------------------------------------------------------------------------------------
  function requireIdle() {
    if (!state.running) return true
    K.toast("Finish or stop the current task first.", { kind: "warn" })
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
      if (payload.has_more) thread.items.unshift({ id: 0, kind: "notice", level: "info", text: "Older messages of this thread are not shown." })
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
    if (!(await K.confirm({ title: "Delete this thread?", text: title, confirmLabel: "Delete", danger: true }))) return
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
      K.toast("This is the current project. Open another one first.", { kind: "warn" })
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
    pause_requested: () => "Pausing: waiting for the current step to finish (Stop ends it now)…",
    paused: () => "Paused",
    cancelling: () => "Stopping the agent and everything it started…",
    awaiting_input: () => "Waiting for your answer",
  }

  const liveActions = h("span", { class: "live-actions" },
    h("button", { class: "btn small", type: "button", onclick: () => void pauseTask() }, K.icon("pause", 13), "Pause"),
    h("button", { class: "btn small", type: "button", onclick: () => void stopTask() }, K.icon("stop", 13), "Stop"),
  )

  function updateLive() {
    if (!state.running) {
      timeline.setLive({ running: false })
      return
    }
    const text = TASK_STATE_TEXT[state.taskState]?.() || thread.status.text || "Working…"
    const seconds = Math.floor((Date.now() - state.startedAt) / 1000)
    timeline.setLive({ running: true, text, elapsed: K.duration(seconds), actions: liveActions })
    timeline.liveNode.classList.toggle("is-waiting", state.taskState === "awaiting_input")
  }
  root.setInterval(() => {
    if (!state.running) return
    updateLive()
    if (thread.turn?.header?.live) timeline.schedule(thread.turn.header)
  }, 1000)

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
        notifyIfAway("Kvist", event.title || "Kvist needs your answer")
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
    notifyIfAway("Kvist finished", body)
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
    if (request.timeout_seconds) panel.append(h("div", { class: "request-hint" }, `Unanswered in ${Math.round(request.timeout_seconds)} s counts as “no”.`))

    const actions = h("div", { class: "request-actions" })
    let focus = null
    if (request.kind === "confirm") {
      const deny = h("button", { class: "btn", type: "button", onclick: () => void answer(request, { approved: false }) }, "Deny")
      const allow = h("button", { class: "btn primary", type: "button", onclick: () => void answer(request, { approved: true }) }, "Allow")
      actions.append(deny)
      if (request.remember_pattern) actions.append(h("button", { class: "btn", type: "button", title: request.remember_pattern, onclick: () => void answer(request, { approved: true, remember: true }) }, "Always allow"))
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
      actions.append(h("button", { class: "btn", type: "button", onclick: () => void answer(request, { value: null }) }, "Cancel"))
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
      actions.append(h("button", { class: "btn", type: "button", onclick: () => void answer(request, { value: null }) }, "Cancel"), h("button", { class: "btn primary", type: "button", onclick: submit }, "Send"))
      focus = input
    }
    panel.append(actions)
    els.requestHost.append(panel)
    if (state.requests.length > 1) panel.append(h("div", { class: "request-hint" }, `${state.requests.length - 1} more waiting`))
    root.requestAnimationFrame(() => focus?.focus())
    timeline.scrollToEnd(false)
  }

  // --- review / commit / revert -----------------------------------------------------------------------------------------------
  async function confirmRevert() {
    if (!requireIdle()) return
    const ok = await K.confirm({
      title: "Revert the agent's changes?",
      text: "Restores every file the agent changed (in all threads of this project) to its recorded baseline. Your own later edits are kept.",
      confirmLabel: "Revert",
      danger: true,
    })
    if (!ok) return
    try {
      const result = await root.kvist.revert()
      const changed = (result.reverted || []).filter((item) => item.action !== "skipped")
      K.toast(changed.length ? `Reverted ${changed.length} file(s).` : "Nothing needed reverting.")
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
      { name: "new", icon: "compose", label: "Start a new thread", run: () => void newThread(state.workspace) },
      { name: "resume", icon: "undo", label: "Resume the unfinished task", run: () => (state.hasCheckpoint ? void resumeTask() : K.toast("Nothing to resume here.")) },
      { name: "review", icon: "review", label: "Open the review panel", run: () => review.setOpen(true) },
      { name: "commit", icon: "branch", label: "Commit the working tree", run: () => void commit() },
      { name: "commands", icon: "terminal", label: "Show the commands drawer", run: () => drawer.setOpen(true) },
      { name: "skills", icon: "sparkle", label: "Browse skills", run: () => showView("skills") },
      { name: "memory", icon: "memory", label: "Browse memory", run: () => showView("memory") },
      { name: "folder", icon: "folder", label: "Open the project folder", run: () => void root.kvist.openProjectFolder().catch((error) => K.toast(error.message, { kind: "error" })) },
      { name: "settings", icon: "sliders", label: "Open settings", run: () => showView("settings") },
    ]
  }

  // Search, like Codex's: threads (Alt+1…9 open the first nine), then quick actions.
  function openPalette() {
    const input = h("input", { class: "palette-input", placeholder: "Search threads", "aria-label": "Search threads" })
    const list = h("div", { class: "palette-list", role: "listbox" })
    let entries = []
    let index = 0
    let modal

    const recentThreads = () => state.projects
      .flatMap((project) => (project.status?.session_items || []).map((item) => ({ project, item })))
      .sort((a, b) => Number(b.item.updated_at) - Number(a.item.updated_at))

    const collect = (query) => {
      const needle = query.trim().toLowerCase()
      const threads = recentThreads()
        .filter(({ project, item }) => !needle || `${item.title || item.name} ${K.baseName(project.path)}`.toLowerCase().includes(needle))
        .slice(0, 12)
        .map(({ project, item }, position) => ({ group: "Threads", label: item.title || item.name, hint: K.baseName(project.path), key: position < 9 ? `Alt+${position + 1}` : "", run: () => void openThread(project.path, item.name) }))
      const actions = [
        { icon: "compose", label: "New thread", key: "Ctrl+N", run: () => void newThread(state.workspace) },
        { icon: "folder", label: "Open a folder", key: "Ctrl+O", run: () => void addProject() },
        { icon: "sparkle", label: "Skills", run: () => showView("skills") },
        { icon: "memory", label: "Memory", run: () => showView("memory") },
        { icon: "gear", label: "Settings", key: "Ctrl+,", run: () => showView("settings") },
      ].filter((action) => !needle || action.label.toLowerCase().includes(needle)).map((action) => ({ ...action, group: "Quick actions" }))
      return [...threads, ...actions]
    }

    const draw = () => {
      K.clear(list)
      let group = ""
      entries.forEach((entry, position) => {
        if (entry.group !== group) {
          group = entry.group
          list.append(h("div", { class: "palette-group" }, group))
        }
        const row = h("button", { class: `palette-row${position === index ? " is-active" : ""}`, type: "button", role: "option" },
          entry.icon ? K.icon(entry.icon, 16) : null,
          h("span", { class: "palette-label" }, entry.label),
          entry.hint ? h("span", { class: "palette-hint" }, entry.hint) : null,
          entry.key ? h("span", { class: "palette-key" }, entry.key) : null,
        )
        row.addEventListener("click", () => { modal.close(); entry.run() })
        list.append(row)
      })
      if (!entries.length) list.append(h("div", { class: "panel-empty small" }, "Nothing found"))
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
    modal = K.modal({ title: "Search", body: h("div", { class: "palette" }, input, list), wide: true })
    modal.dialog.classList.add("is-palette")
    draw()
  }

  // --- shell wiring ---------------------------------------------------------------------------------------------------------------------
  function togglePanel() {
    const collapsed = els.app.classList.toggle("panel-collapsed")
    K.store.set("panel", collapsed ? "0" : "1")
  }

  function openNthThread(n) {
    const items = state.projects
      .flatMap((project) => (project.status?.session_items || []).map((item) => ({ project, item })))
      .sort((a, b) => Number(b.item.updated_at) - Number(a.item.updated_at))
    const entry = items[n - 1]
    if (entry) void openThread(entry.project.path, entry.item.name)
  }

  function bindShell() {
    $("rail-threads").addEventListener("click", () => showView("thread"))
    $("rail-skills").addEventListener("click", () => showView("skills"))
    $("rail-memory").addEventListener("click", () => showView("memory"))
    $("rail-settings").addEventListener("click", () => showView("settings"))
    $("settings-back").addEventListener("click", () => showView("thread"))
    els.settingsSearch.addEventListener("input", renderSettingsNav)
    $("nav-new").addEventListener("click", () => void newThread(state.workspace))
    $("nav-search").addEventListener("click", openPalette)
    $("add-project").addEventListener("click", () => void addProject())
    $("workspace-switch").addEventListener("click", () => openProjectMenu($("workspace-switch")))
    $("panel-toggle").addEventListener("click", togglePanel)
    els.reviewButton.addEventListener("click", () => review.toggle())
    els.drawerButton.addEventListener("click", () => drawer.toggle())
    els.commitButton.addEventListener("click", () => void commit())
    $("open-folder").addEventListener("click", () => void root.kvist.openProjectFolder().catch((error) => K.toast(error.message, { kind: "error" })))
    $("drawer-close").addEventListener("click", () => drawer.setOpen(false))
    $("review-close").addEventListener("click", () => review.setOpen(false))
    $("setup-open").addEventListener("click", () => showView("settings", "models"))
    if (K.store.get("panel", "1") === "0") els.app.classList.add("panel-collapsed")

    document.addEventListener("keydown", (event) => {
      const ctrl = event.ctrlKey || event.metaKey
      const key = event.key.toLowerCase()
      if (ctrl && key === "n") { event.preventDefault(); void newThread(state.workspace) }
      else if (ctrl && key === "k") { event.preventDefault(); openPalette() }
      else if (ctrl && key === "o") { event.preventDefault(); void addProject() }
      else if (ctrl && key === "b") { event.preventDefault(); togglePanel() }
      else if (ctrl && key === "j") { event.preventDefault(); drawer.toggle() }
      else if (ctrl && event.shiftKey && key === "d") { event.preventDefault(); review.toggle() }
      else if (ctrl && key === ",") { event.preventDefault(); showView("settings") }
      else if (event.altKey && /^[1-9]$/.test(event.key)) { event.preventDefault(); openNthThread(Number(event.key)) }
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
