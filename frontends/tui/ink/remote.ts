import type { RemoteLinkState } from "./protocol.js"

// The link to a core on a server while it is not simply up (UI_API.md section 8g).
export interface RemoteLink {
  state: RemoteLinkState
  host: string
  reason: string
  retryIn: number | null
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
