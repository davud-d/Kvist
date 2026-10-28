import { Calculator, Code, FileDiff, FileSpreadsheet, FlaskConical, FolderTree, Globe, GitPullRequest, Image, Repeat, ScanText, Table, type LucideIcon } from "lucide-preact"
import { useState } from "preact/hooks"

import type { SessionItem } from "../src/messages"
import { starterGroups, type StarterIcon } from "./homeStarters"
import { KvistMark } from "./mark"
import { ago } from "./sessions"

const ICONS: Record<StarterIcon, LucideIcon> = {
  explain: ScanText,
  review: GitPullRequest,
  tests: FlaskConical,
  collect: FileSpreadsheet,
  compare: FileDiff,
  sort: FolderTree,
  routine: Repeat,
  clean: Table,
  fetch: Globe,
  site: Code,
  tool: Calculator,
  design: Image,
}

const RECENT = 3
const GROUP_KEY = "kvist.home.group"

// Browser storage can be missing or throw in a JCEF page loaded from a string; the tab is only a convenience.
function savedGroup(): string {
  try {
    return localStorage.getItem(GROUP_KEY) ?? ""
  } catch {
    return ""
  }
}

function saveGroup(id: string): void {
  try {
    localStorage.setItem(GROUP_KEY, id)
  } catch {
    // Nothing to do: the choice just is not remembered.
  }
}

interface Props {
  russian: boolean
  project: string
  provider: string
  sessions: SessionItem[]
  disabled: boolean
  pick: (prompt: string) => void
  open: (name: string) => void
}

export function Home({ russian, project, provider, sessions, disabled, pick, open }: Props) {
  const t = (en: string, ru: string) => (russian ? ru : en)
  const groups = starterGroups(russian)
  const [groupId, setGroupId] = useState(() => groups.find((group) => group.id === savedGroup())?.id ?? groups[0].id)
  const [allSessions, setAllSessions] = useState(false)
  const group = groups.find((item) => item.id === groupId) ?? groups[0]
  const shown = allSessions ? sessions : sessions.slice(0, RECENT)

  return (
    <section class="home" aria-label={t("Start", "Начало")}>
      <KvistMark size={40} />
      <h1 class="home-title">{t("What do you need done?", "Что нужно сделать?")}</h1>
      <p class="home-lead">
        {project
          ? t(`Describe the result in your own words. Kvist works with the files in ${project}.`, `Опишите результат своими словами. Kvist работает с файлами папки ${project}.`)
          : t("Describe the result in your own words.", "Опишите результат своими словами.")}
      </p>

      <div class="home-tabs" role="tablist">
        {groups.map((item) => (
          <button
            key={item.id}
            role="tab"
            aria-selected={item.id === group.id}
            class={`home-tab ${item.id === group.id ? "active" : ""}`}
            onClick={() => { setGroupId(item.id); saveGroup(item.id) }}
          >{item.label}</button>
        ))}
      </div>
      <ul class="home-starters" role="tabpanel">
        {group.starters.map((starter) => {
          const Glyph = ICONS[starter.icon]
          return (
            <li key={starter.title}>
              <button class="home-starter" disabled={disabled} onClick={() => pick(starter.prompt)}>
                <span class="home-starter-glyph"><Glyph size={18} strokeWidth={1.6} /></span>
                <span class="home-starter-text">
                  <span class="home-starter-title">{starter.title}</span>
                  <span class="home-starter-hint">{starter.hint}</span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>

      {sessions.length > 0 ? (
        <div class="home-recent">
          <h2 class="home-subtitle">{t("Pick up where you left off", "Продолжить с того места")}</h2>
          <ul>
            {shown.map((item) => (
              <li key={item.name}>
                <button class="home-session" disabled={disabled} onClick={() => open(item.name)}>
                  <span class="home-session-title">{item.title}</span>
                  <span class="home-session-time">{item.updated_at ? ago(item.updated_at, Date.now() / 1000, russian) : ""}</span>
                </button>
              </li>
            ))}
          </ul>
          {sessions.length > RECENT ? (
            <button class="more" onClick={() => setAllSessions(!allSessions)}>
              {allSessions ? t("Show fewer", "Свернуть") : t(`Show all ${sessions.length}`, `Показать все (${sessions.length})`)}
            </button>
          ) : null}
        </div>
      ) : null}

      <p class="home-promise">
        {t(
          "Kvist asks before risky commands, never opens .env files or keys, and every change to your files can be undone with /revert.",
          "Kvist спрашивает перед рискованными командами, не открывает файлы .env и ключи, а любое изменение ваших файлов можно отменить командой /revert.",
        )}
        {provider ? " " + t(`The files it reads are sent to ${provider}.`, `Прочитанные файлы отправляются в ${provider}.`) : ""}
      </p>
    </section>
  )
}
