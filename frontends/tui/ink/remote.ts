import type { RemoteLinkState } from "./protocol.js"

// The link to a core on a server while it is not simply up (UI_API.md section 8g).
export interface RemoteLink {
  state: RemoteLinkState
  host: string
  reason: string
  retryIn: number | null
}

// The core asking a frontend to end itself so the launcher starts it again somewhere else (UI_API.md section 8g):
// `effects.relaunch` of a `command_result`. `switch` is the line to leave for the launcher; the rest says where to.
export interface Relaunch {
  switch: string
  label: string
  machine: string | null
  host: string | null
  folder: string | null
  session: string | null
}

export function relaunchOf(effects: Record<string, unknown> | undefined): Relaunch | null {
  const ask = effects?.relaunch
  if (typeof ask !== "object" || ask === null) return null
  const fields = ask as Record<string, unknown>
  if (typeof fields.switch !== "string" || !fields.switch) return null
  const text = (value: unknown) => (typeof value === "string" && value ? value : null)
  return {
    switch: fields.switch,
    label: text(fields.label) ?? fields.switch,
    machine: text(fields.machine),
    host: text(fields.host),
    folder: text(fields.folder),
    session: text(fields.session),
  }
}

// What takes the input's place while the link is down. The proxy already wrote `reason` in the
// person's language; only the countdown is ours.
export function remoteNotice(link: RemoteLink, russian: boolean): string {
  const parts = [link.reason || link.host]
  if (link.state === "reconnecting" && link.retryIn !== null) {
    parts.push(russian ? `повтор через ${Math.round(link.retryIn)} с` : `retrying in ${Math.round(link.retryIn)} s`)
  }
  return parts.join(" · ")
}
