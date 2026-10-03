import { state, update } from "/assets/state/store.js"

const nav = document.querySelector("#navigation")
const inboxCount = document.querySelector("#inbox-count")
const activeServer = document.querySelector("#active-server-label")

function el(tag, cls, text) { const node = document.createElement(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node }
export function renderNavigation(onSelect) {
  nav.replaceChildren()
  inboxCount.textContent = String(state.navigation.inbox.length)
  for (const entry of state.navigation.servers) {
    const section = el("section", "nav-server")
    const head = el("div", "nav-server-head")
    head.append(el("span", `connection-dot ${entry.connection.state}`), el("span", "", entry.server.name))
    section.append(head)
    for (const workspace of entry.workspaces) {
      const workspaceHead = el("div", "nav-workspace-head", workspace.path.split(/[\\/]/).filter(Boolean).at(-1) || workspace.path)
      workspaceHead.title = workspace.path; section.append(workspaceHead)
      const sessions = workspace.sessions?.session_items || []
      if (!sessions.length) section.append(el("div", "nav-session", "No sessions"))
      for (const session of sessions) {
        const button = el("button", "nav-session")
        const selected = state.selected && state.selected.serverId === entry.server.id && state.selected.workspace === workspace.path && state.selected.session === session.name
        if (selected) button.classList.add("active")
        const dot = el("span", `status-dot ${session.execution || "idle"}${session.unread ? " unread" : ""}`)
        button.append(dot, el("span", "nav-title", session.title || session.name))
        if (session.unread) button.append(el("span", "badge", "new"))
        button.onclick = () => onSelect(entry.server.id, workspace.path, session.name)
        section.append(button)
      }
    }
    nav.append(section)
  }
  if (state.selected) activeServer.textContent = state.navigation.servers.find(s => s.server.id === state.selected.serverId)?.server.name || "local"
}

export function paletteItems() {
  const items = []
  for (const entry of state.navigation.servers) for (const workspace of entry.workspaces) {
    items.push({ type: "workspace", label: workspace.path.split(/[\\/]/).at(-1), detail: entry.server.name, selection: { serverId: entry.server.id, workspace: workspace.path } })
    for (const session of workspace.sessions?.session_items || []) items.push({ type: "session", label: session.title, detail: `${entry.server.name} / ${workspace.path.split(/[\\/]/).at(-1)}`, selection: { serverId: entry.server.id, workspace: workspace.path, session: session.name } })
  }
  return items
}

export function inboxItems() {
  return (state.navigation.inbox || []).map(item => ({
    type: "inbox",
    label: item.title || item.session,
    detail: `${item.server_name || item.server_id} / ${item.workspace.split(/[\\/]/).filter(Boolean).at(-1) || item.workspace} · ${item.state || "unread"}`,
    selection: { serverId: item.server_id, workspace: item.workspace, session: item.session },
  }))
}
