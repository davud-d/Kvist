import { useState } from "preact/hooks"

import type { SessionItem } from "../src/messages"

const RECENT = 3

export function ago(seconds: number, now = Date.now() / 1000): string {
  const minutes = Math.floor((now - seconds) / 60)
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "min" : "mins"} ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`
  const days = Math.floor(hours / 24)
  return `${days} ${days === 1 ? "day" : "days"} ago`
}

interface Props {
  items: SessionItem[]
  disabled: boolean
  open: (name: string) => void
}

export function RecentSessions({ items, disabled, open }: Props) {
  const [all, setAll] = useState(false)
  if (items.length === 0) return null
  const shown = all ? items : items.slice(0, RECENT)
  return (
    <section class="recent">
      <div class="section-title">Recent sessions</div>
      {shown.map((item) => (
        <button key={item.name} class="session-row" disabled={disabled} onClick={() => open(item.name)}>
          <span class="session-title">{item.title}</span>
          <span class="session-time">{item.updated_at ? ago(item.updated_at) : ""}</span>
        </button>
      ))}
      {items.length > RECENT ? (
        <button class="more" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show ${items.length - RECENT} more`}
        </button>
      ) : null}
    </section>
  )
}
