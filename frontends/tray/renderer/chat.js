"use strict"

const sessions = document.getElementById("sessions")
const transcript = document.getElementById("transcript")
const hint = document.getElementById("hint")
const goButton = document.getElementById("go")
const disableButton = document.getElementById("disable")

let current = null

function element(tag, className, text) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

function renderSessions(view) {
  sessions.replaceChildren(
    ...view.cores.map((core) => {
      const chip = element("button", "chip")
      chip.dataset.state = core.state
      chip.setAttribute("aria-pressed", String(core.id === view.selectedId))
      chip.append(
        element("span", "dot"),
        element("span", "name", `${core.title} · ${core.session}`),
        element("span", "state", view.strings[core.state === "running" ? "stateRunning" : "stateDone"]),
      )
      chip.addEventListener("click", () => window.kvistTray.select(core.id))
      return chip
    }),
  )
  sessions.hidden = view.cores.length < 2
}

function renderItem(item) {
  if (item.kind === "assistant") {
    // `html` is sanitized by the main process (sanitize-html allowlist).
    const node = element("div", "assistant")
    node.innerHTML = item.html
    return node
  }
  return element("div", item.kind, item.text)
}

function renderTranscript(view) {
  if (!view.cores.length || view.ended) {
    const empty = element("div", "empty")
    empty.append(
      element("strong", "", view.ended ? view.strings.sessionEnded : view.strings.emptyTitle),
      element("span", "", view.strings.emptyHint),
    )
    transcript.replaceChildren(empty)
    return
  }
  const nearBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 40
  transcript.replaceChildren(...view.transcript.map(renderItem))
  if (nearBottom) transcript.scrollTop = transcript.scrollHeight
}

function renderFooter(view) {
  hint.replaceChildren(element("div", "", view.strings.readOnly))
  if (view.notice) {
    const notice = element("div", "", view.notice)
    notice.id = "notice"
    hint.append(notice)
  }
  goButton.textContent = view.strings.goToTerminal
  goButton.disabled = !view.selectedId
  disableButton.textContent = view.strings.disable
}

window.kvistTray.onView((view) => {
  current = view
  renderSessions(view)
  renderTranscript(view)
  renderFooter(view)
})

goButton.addEventListener("click", () => {
  if (current && current.selectedId) window.kvistTray.focusTerminal(current.selectedId)
})
disableButton.addEventListener("click", () => window.kvistTray.disable())
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") window.kvistTray.hide()
})
window.kvistTray.onOpen((origin) => {
  // The panel unfolds from the point of the bar's icon: the animation starts as a dot there.
  document.body.style.setProperty("--ox", `${origin.x}px`)
  document.body.style.setProperty("--oy", `${origin.y}px`)
  document.body.classList.remove("unfold")
  void document.body.offsetWidth
  document.body.classList.add("unfold")
})
window.kvistTray.ready()
