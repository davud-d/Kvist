import { useEffect } from "preact/hooks"

import { EFFORTS } from "../../../tui/ink/commands"
import { findProvider } from "../src/messages"
import type { ConfigInfo } from "../src/messages"
import { post } from "./host"
import { Icon } from "./icons"
import { KvistMark } from "./mark"
import { Row, SandboxRows, Segmented, VariableRows } from "./settings"

const ROLES = ["auto", "coder", "reader", "devops", "chat", "universal"]

interface Props {
  russian: boolean
  config: ConfigInfo | null
  provider: string
  model: string
  role: string
  effort: string
  environment: Record<string, string> | null
  busy: boolean
  run: (name: string, args?: string[]) => void
  applyEnvironment: (environment: Record<string, string>) => void
  close: () => void
}

// The editor page's settings: what is set right now is on the screen, and each choice says in plain words
// what it does, so nobody has to open a dialog to learn the current value or guess what "devops" means.
export function SettingsPage({ russian, config, provider, model, role, effort, environment, busy, run, applyEnvironment, close }: Props) {
  const t = (en: string, ru: string) => (russian ? ru : en)
  useEffect(() => {
    post({ type: "getConfig" })
    post({ type: "getEnvironment" })
  }, [])
  const current = config && findProvider(config, provider)

  const roleLabels: Record<string, string> = {
    auto: t("Auto", "Авто"), coder: t("Code", "Код"), reader: t("Read-only", "Чтение"), devops: "DevOps", chat: t("Chat", "Чат"), universal: t("Universal", "Универсал"),
  }
  const roleHints: Record<string, string> = {
    auto: t("Kvist picks the mode for each request.", "Kvist сам выбирает режим для каждой задачи."),
    coder: t("Writes and changes code and files.", "Пишет и меняет код и файлы."),
    reader: t("Reads and reports; changes nothing.", "Читает и рассказывает; ничего не меняет."),
    devops: t("Builds, servers and deployment.", "Сборка, серверы и развёртывание."),
    chat: t("Just a conversation; no files are touched.", "Только разговор; файлы не затрагиваются."),
    universal: t("One mode for everything.", "Один режим для всего."),
  }
  const effortLabels: Record<string, string> = {
    false: t("Off", "Выкл"), low: t("Low", "Низкая"), medium: t("Medium", "Средняя"), high: t("High", "Высокая"), xhigh: t("Extra", "Очень"), max: t("Max", "Макс"),
  }
  const keyState = !current ? "—"
    : current.auth_type === "oauth" ? (current.has_credentials ? t("Signed in", "Вход выполнен") : t("Not signed in", "Вход не выполнен"))
    : current.has_credentials ? t("Saved", "Сохранён") : t("Missing", "Не задан")

  const sections = [
    { id: "connection", label: t("Connection", "Подключение") },
    { id: "behavior", label: t("Behavior", "Поведение") },
    { id: "safety", label: t("Safety", "Безопасность") },
    { id: "advanced", label: t("Advanced", "Дополнительно") },
  ]
  const jump = (id: string) => {
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    document.getElementById(`prefs-${id}`)?.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" })
  }

  return (
    <div class="app workspace-app">
      <header class="header">
        <button class="icon" title={t("Back to chat", "Назад в чат")} onClick={close}><Icon name="back" /></button>
        <div class="brand"><KvistMark size={22} still /><span class="brand-name">{t("Settings", "Настройки")}</span></div>
      </header>
      <div class="prefs">
        <nav class="prefs-nav" aria-label={t("Sections", "Разделы")}>
          {sections.map((section) => <button key={section.id} onClick={() => jump(section.id)}>{section.label}</button>)}
        </nav>
        <div class="prefs-body">
          {busy ? <p class="prefs-busy">{t("Changes are available when the agent is idle.", "Менять настройки можно, когда агент свободен.")}</p> : null}

          <section id="prefs-connection">
            <h2>{t("Connection", "Подключение")}</h2>
            <div class="group">
              <Row title={t("Provider", "Провайдер")} hint={current && !current.has_credentials ? (current.auth_type === "oauth" ? t("Continue with ChatGPT", "Войдите через ChatGPT") : t("API key is missing", "Не задан ключ API")) : undefined}>
                <span class="row-value">{current?.label ?? (provider || t("Not set", "Не выбран"))}</span>
                <button class="btn" disabled={busy} onClick={() => run("provider")}>{t("Change", "Сменить")}</button>
              </Row>
              <Row title={t("Model", "Модель")} hint={t("Starts on the fast model and moves to the smart one when a step fails or stalls.", "Начинает на быстрой модели и переходит на умную, если шаг не получается.")}>
                <span class="row-value">{model || "—"}</span>
                <button class="btn" disabled={busy} onClick={() => run("models")}>{t("Choose", "Выбрать")}</button>
              </Row>
              <Row title={t("API key", "Ключ API")}>
                <span class="row-value">{keyState}</span>
                <button class="btn" disabled={busy} onClick={() => run("change-api")}>{t("Change", "Сменить")}</button>
              </Row>
              {current?.custom ? (
                <Row title={t("Custom provider", "Свой провайдер")}>
                  <button class="btn" disabled={busy} onClick={() => run("strime")}>{t("Streaming", "Потоковая передача")}</button>
                  <button class="btn danger" disabled={busy} onClick={() => run("del-provider")}>{t("Delete", "Удалить")}</button>
                </Row>
              ) : null}
            </div>
          </section>

          <section id="prefs-behavior">
            <h2>{t("Behavior", "Поведение")}</h2>
            <div class="group">
              <div class="row stack">
                <div class="row-title">{t("Mode", "Режим")}</div>
                <Segmented options={ROLES} value={role} labels={roleLabels} disabled={busy} pick={(value) => run("role", [value])} />
                <div class="row-hint">{roleHints[role] ?? ""}</div>
              </div>
              <div class="row stack">
                <div class="row-title">{t("Thinking depth", "Глубина размышлений")}</div>
                <Segmented options={EFFORTS} value={effort} labels={effortLabels} disabled={busy} pick={(value) => run("effort", [value])} />
                <div class="row-hint">{t("Deeper thinking gives better answers on hard tasks, but is slower and uses more tokens.", "Глубокие размышления дают лучшие ответы на сложных задачах, но медленнее и тратят больше токенов.")}</div>
              </div>
              <Row title={t("Style", "Стиль")} hint={t("How the agent works and talks", "Как агент работает и разговаривает")}>
                <button class="btn" disabled={busy} onClick={() => run("style")}>{t("Choose", "Выбрать")}</button>
              </Row>
            </div>
          </section>

          <section id="prefs-safety">
            <h2>{t("Safety", "Безопасность")}</h2>
            <div class="group">
              {environment ? <SandboxRows environment={environment} busy={busy} apply={applyEnvironment} russian={russian} plain /> : null}
              <Row title={t("Changes to your files", "Изменения в ваших файлах")} hint={t("Kvist records every file it changes. Revert puts them back as they were.", "Kvist записывает каждый изменённый файл. «Отменить» вернёт их как были.")}>
                <button class="btn" disabled={busy} onClick={() => run("changes")}>{t("Show", "Показать")}</button>
                <button class="btn" disabled={busy} onClick={() => run("revert")}>{t("Revert", "Отменить")}</button>
              </Row>
            </div>
          </section>

          <section id="prefs-advanced">
            <h2>{t("Advanced", "Дополнительно")}</h2>
            <div class="group">
              <Row title={t("Requests", "Запросы")} hint={t("Pacing, timeouts, context window", "Интервалы, таймауты, окно контекста")}>
                <button class="btn" disabled={busy} onClick={() => run("settings-api")}>{t("Open", "Открыть")}</button>
              </Row>
              {environment ? <VariableRows environment={environment} busy={busy} apply={applyEnvironment} russian={russian} /> : null}
              <Row title={t("Reset everything", "Сбросить всё")} hint={t("Erases every saved provider and key", "Удаляет всех сохранённых провайдеров и ключи")}>
                <button class="btn danger" disabled={busy} onClick={() => run("full-reset")}>{t("Reset", "Сбросить")}</button>
              </Row>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
