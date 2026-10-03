const listeners = new Set()
export const state = {
  navigation: { servers: [], inbox: [] }, selected: null, events: [], eventSeq: 0,
  liveCursor: 0, liveEpoch: "", inspectorTab: "activity", paletteMode: "commands", diffReport: "",
}
export function update(change) { change(state); for (const listener of listeners) listener(state) }
export function subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener) }
export function serverOf(id) { return state.navigation.servers.find(item => item.server.id === id) }
export function sessionOf(selection = state.selected) {
  if (!selection) return null
  const server = serverOf(selection.serverId)
  const workspace = server?.workspaces.find(item => item.path === selection.workspace)
  return workspace?.sessions?.session_items.find(item => item.name === selection.session) || null
}
