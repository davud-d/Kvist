import { useState } from "preact/hooks"

import type { SessionItem } from "../src/messages"

const RECENT = 3

export function ago(seconds: number, now = Date.now() / 1000, russian = false): string {
  const minutes = Math.floor((now - seconds) / 60)
  if (russian) return agoRu(minutes)
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "min" : "mins"} ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`
  const days = Math.floor(hours / 24)
  return `${days} ${days === 1 ? "day" : "days"} ago`
}

// Russian picks the noun form from the number: 1 минуту, 2 минуты, 5 минут.
function plural(n: number, one: string, few: string, many: string): string {
  const last = n % 10
  if (n % 100 >= 11 && n % 100 <= 14) return many
  if (last === 1) return one
  return last >= 2 && last <= 4 ? few : many
}

function agoRu(minutes: number): string {
  if (minutes < 1) return "только что"
  if (minutes < 60) return `${minutes} ${plural(minutes, "минуту", "минуты", "минут")} назад`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} ${plural(hours, "час", "часа", "часов")} назад`
  const days = Math.floor(hours / 24)
  return `${days} ${plural(days, "день", "дня", "дней")} назад`
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
