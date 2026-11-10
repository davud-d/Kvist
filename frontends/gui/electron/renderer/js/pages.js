"use strict"
// Full-page views that replace the thread: Skills, Memory and Settings.

;(function definePages(root) {
  const K = (root.K = root.K || {})
  const { h, t } = K

  function pageHeader(title, subtitle, ...actions) {
    return h("header", { class: "page-head" }, h("div", {}, h("h1", {}, title), subtitle ? h("p", { class: "page-sub" }, subtitle) : null), h("div", { class: "page-actions" }, actions))
  }

  function loading() {
    return h("div", { class: "panel-empty" }, h("span", { class: "spinner" }))
  }

  function failure(message) {
    return h("div", { class: "card error-card" }, K.icon("alert", 15), h("span", {}, message))
  }

  // --- Skills ------------------------------------------------------------------------------------
  async function skills(page) {
    page.replaceChildren(pageHeader(t("Skills", "Навыки"), t("Packages that give Kvist new tools: a browser, an app screen, design checks and more.", "Пакеты, которые дают Kvist новые инструменты: браузер, экран приложений, проверка дизайна и другое.")), loading())
    let result
    try {
      result = await root.kvist.skills()
    } catch (error) {
      page.replaceChildren(pageHeader(t("Skills", "Навыки")), failure(error.message))
      return
    }
    const grid = h("div", { class: "card-grid" })
    for (const skill of result.skills || []) {
      const chips = (skill.capabilities || []).slice(0, 6).map((capability) => h("span", { class: "chip-static" }, capability))
      grid.append(h(
        "article",
        { class: `skill-card${skill.available ? "" : " is-unavailable"}` },
        h("div", { class: "skill-top" }, K.icon("sparkle", 16), h("h3", {}, skill.name), h("span", { class: "card-meta" }, `v${skill.version}`)),
        h("p", { class: "skill-summary" }, skill.summary),
        h("div", { class: "chip-row" }, chips),
        h(
          "div",
          { class: "skill-foot" },
          h("span", { class: `badge ${skill.available ? "is-ok" : "is-warn"}` }, skill.available ? t("Available", "Доступен") : skill.reason || t("Unavailable", "Недоступен")),
          skill.approved_here ? h("span", { class: "badge is-ok" }, t("Approved here", "Разрешён здесь")) : h("span", { class: "badge" }, t("Asks when used", "Спросит при запуске")),
          skill.source?.origin ? h("span", { class: "badge" }, skill.source.origin) : null,
        ),
      ))
    }
    page.replaceChildren(
      pageHeader(t("Skills", "Навыки"), t("Packages that give Kvist new tools: a browser, an app screen, design checks and more.", "Пакеты, которые дают Kvist новые инструменты: браузер, экран приложений, проверка дизайна и другое.")),
      (result.skills || []).length ? grid : h("div", { class: "panel-empty" }, t("No skills installed.", "Навыков нет.")),
      h("p", { class: "page-foot" }, t("Install, approve or remove skills with ", "Установка, разрешение и удаление навыков: "), h("code", {}, "kvist --skill-op …"), t(" (see docs/SKILLS.md).", " (см. docs/SKILLS.md).")),
    )
  }

  // --- Memory -------------------------------------------------------------------------------------
  async function memory(page) {
    const title = t("Memory", "Память")
    const subtitle = t("What Kvist remembers about you and your projects. Everything here is yours to read and delete.", "Что Kvist помнит о тебе и твоих проектах. Всё это можно читать и удалять.")
    page.replaceChildren(pageHeader(title, subtitle), loading())
    let data
    try {
      data = await root.kvist.memory()
    } catch (error) {
      page.replaceChildren(pageHeader(title), failure(error.message))
      return
    }
    const stores = data.status?.stores || {}
    const status = h(
      "div",
      { class: "stat-row" },
      h("div", { class: "stat" }, h("b", {}, stores.user?.total ?? 0), h("span", {}, t("about you", "о тебе"))),
      h("div", { class: "stat" }, h("b", {}, stores.workspace?.total ?? 0), h("span", {}, t("about this project", "об этом проекте"))),
      h("div", { class: "stat" }, h("b", {}, data.status?.paused ? t("paused", "пауза") : t("on", "включена")), h("span", {}, t("memory", "память"))),
    )
    const list = h("div", { class: "memory-list" })
    const render = () => {
      K.clear(list)
      const records = data.records.filter((record) => record.status === "active")
      if (!records.length) list.append(h("div", { class: "panel-empty" }, t("Nothing remembered yet.", "Пока ничего не запомнено.")))
      for (const record of records) {
        const forget = h("button", { class: "btn small", type: "button" }, K.icon("trash", 13), t("Forget", "Забыть"))
        forget.addEventListener("click", async () => {
          if (!(await K.confirm({ title: t("Forget this?", "Забыть это?"), text: record.content, confirmLabel: t("Forget", "Забыть"), danger: true }))) return
          try {
            await root.kvist.memoryForget(record.id)
            data.records = data.records.filter((entry) => entry.id !== record.id)
            render()
          } catch (error) {
            K.toast(error.message, { kind: "error" })
          }
        })
        list.append(h(
          "article",
          { class: "memory-card" },
          h("p", { class: "memory-text" }, record.content),
          h("div", { class: "memory-foot" }, h("span", { class: "badge" }, record.scope === "user" ? t("about you", "о тебе") : t("project", "проект")), h("span", { class: "badge" }, record.kind), record.topic ? h("span", { class: "badge" }, record.topic) : null, h("span", { class: "grow" }), forget),
        ))
      }
    }
    render()
    page.replaceChildren(pageHeader(title, subtitle), status, list)
  }

  // --- Settings -----------------------------------------------------------------------------------
  const SECTIONS = [
    { id: "general", icon: "sliders", label: () => t("General", "Общие") },
    { id: "models", icon: "key", label: () => t("Models & providers", "Модели и провайдеры") },
    { id: "behavior", icon: "bolt", label: () => t("Agent behavior", "Поведение агента") },
    { id: "integrations", icon: "plug", label: () => t("Integrations", "Интеграции") },
    { id: "data", icon: "shield", label: () => t("Data & about", "Данные и о программе") },
  ]

  function settingRow(label, description, control) {
    return h("div", { class: "setting" }, h("div", { class: "setting-text" }, h("div", { class: "setting-label" }, label), description ? h("div", { class: "setting-desc" }, description) : null), h("div", { class: "setting-control" }, control))
  }

  function select(options, value, onChange) {
    const node = h("select", { class: "field-input" })
    for (const [optionValue, label] of options) node.append(new Option(label, optionValue))
    node.value = value
    node.addEventListener("change", () => onChange(node.value))
    return node
  }

  function toggle(on, onChange) {
    const node = h("button", { class: `switch${on ? " is-on" : ""}`, type: "button", role: "switch", "aria-checked": String(on) }, h("span", { class: "switch-knob" }))
    node.addEventListener("click", () => {
      const next = node.getAttribute("aria-checked") !== "true"
      node.setAttribute("aria-checked", String(next))
      node.classList.toggle("is-on", next)
      onChange(next)
    })
    return node
  }

  function generalSection() {
    const theme = K.store.get("theme", "system")
    return h(
      "div",
      { class: "settings-block" },
      settingRow(t("Language", "Язык"), t("The interface language. Kvist answers in the language you write in.", "Язык интерфейса. Kvist отвечает на том языке, на котором ты пишешь."), select([["en", "English"], ["ru", "Русский"]], K.lang, (value) => { K.store.set("lang", value); root.location.reload() })),
      settingRow(t("Theme", "Тема"), "", select([["system", t("System", "Системная")], ["light", t("Light", "Светлая")], ["dark", t("Dark", "Тёмная")]], theme, (value) => { K.store.set("theme", value); K.applyTheme() })),
      settingRow(t("Show agent debug messages", "Показывать отладку агента"), t("Adds the core's diagnostic notes to the thread.", "Добавляет в тред диагностические заметки ядра."), toggle(K.store.get("debug") === "1", (on) => K.store.set("debug", on ? "1" : "0"))),
    )
  }

  async function modelsSection(ctx) {
    const box = h("div", { class: "settings-block" }, loading())
    let info
    try {
      info = await ctx.refreshConfig()
    } catch (error) {
      box.replaceChildren(failure(error.message))
      return box
    }
    let moreOpen = null // null = decide from the data: open when nothing is connected yet
    const redraw = async () => {
      info = await ctx.refreshConfig()
      box.replaceChildren(...build())
    }
    const build = () => {
      const nodes = []
      nodes.push(h("p", { class: "page-sub" }, t(`Saved default: ${info.saved_provider || "none"}. Pick a provider and models per task from the box under the message field.`, `Сохранённый по умолчанию: ${info.saved_provider || "нет"}. Провайдера и модели для задачи выбирай в панели под полем ввода.`)))
      const all = [...(info.providers || []), ...(info.custom_providers || [])]
      const connected = all.filter((provider) => provider.has_credentials)
      const rest = all.filter((provider) => !provider.has_credentials)
      nodes.push(h("h3", { class: "group-title" }, t(`Connected (${connected.length})`, `Подключены (${connected.length})`)), ...connected.map(providerRow))
      if (rest.length) {
        const more = h("details", { class: "more-providers" }, h("summary", {}, t(`Not connected (${rest.length})`, `Не подключены (${rest.length})`)), ...rest.map(providerRow))
        more.open = moreOpen ?? connected.length === 0
        more.addEventListener("toggle", () => { moreOpen = more.open })
        nodes.push(more)
      }
      nodes.push(h("div", { class: "settings-actions" }, h("button", { class: "btn", type: "button", onclick: () => ctx.addProvider(redraw) }, K.icon("plus", 14), t("Add a custom provider", "Добавить свой провайдер"))))
      return nodes
    }

    function providerRow(provider) {
      const ready = provider.has_credentials
      const status = h("span", { class: `badge ${ready ? "is-ok" : "is-warn"}` }, ready ? (provider.auth_type === "oauth" ? t("Signed in", "Вход выполнен") : t("Key saved", "Ключ сохранён")) : (provider.auth_type === "oauth" ? t("Not signed in", "Нет входа") : t("No key", "Нет ключа")))
      const actions = h("div", { class: "row-actions" })
      if (provider.auth_type === "oauth") {
        const button = h("button", { class: "btn small", type: "button" }, ready ? t("Sign out", "Выйти") : t("Sign in with ChatGPT", "Войти через ChatGPT"))
        button.addEventListener("click", async () => {
          button.disabled = true
          button.textContent = t("Waiting for the browser…", "Жду браузер…")
          try {
            await (ready ? root.kvist.chatgptLogout() : root.kvist.chatgptLogin())
            await redraw()
          } catch (error) {
            K.toast(error.message, { kind: "error" })
            await redraw()
          }
        })
        actions.append(button)
      } else if (provider.custom) {
        actions.append(h("button", { class: "btn small", type: "button", onclick: () => deleteCustom(provider) }, K.icon("trash", 13), t("Delete", "Удалить")))
      } else {
        actions.append(h("button", { class: "btn small", type: "button", onclick: () => keyDialog(provider) }, K.icon("key", 13), ready ? t("Replace key", "Заменить ключ") : t("Add key", "Добавить ключ")))
      }
      const models = provider.saved_models?.smart || provider.defaults?.smart || ""
      return h("div", { class: "provider-row" }, h("div", { class: "provider-main" }, h("div", { class: "provider-name" }, provider.label), models ? h("div", { class: "provider-models" }, models) : null), status, actions)
    }

    async function deleteCustom(provider) {
      if (!(await K.confirm({ title: t("Delete this provider?", "Удалить провайдера?"), text: provider.label, confirmLabel: t("Delete", "Удалить"), danger: true }))) return
      try {
        await root.kvist.deleteCustomProvider(provider.id)
        await redraw()
      } catch (error) {
        K.toast(error.message, { kind: "error" })
      }
    }

    function keyDialog(provider) {
      const key = h("input", { class: "field-input", type: "password", autocomplete: "off", placeholder: "API key", "aria-label": "API key" })
      const model = provider.requires_manual_models ? h("input", { class: "field-input", placeholder: t("Model id used for every role", "Id модели для всех ролей") }) : null
      const error = h("div", { class: "field-hint is-warn" })
      K.modal({
        title: t(`${provider.label}: API key`, `${provider.label}: ключ API`),
        body: h("div", { class: "form" }, h("p", { class: "modal-text" }, t("The key is stored in ~/.kvist/config.json and also becomes the saved default provider.", "Ключ хранится в ~/.kvist/config.json, а провайдер становится сохранённым по умолчанию.")), h("label", { class: "field" }, h("span", { class: "field-label" }, t("API key", "Ключ API")), key), model ? h("label", { class: "field" }, h("span", { class: "field-label" }, t("Model", "Модель")), model) : null, error),
        actions: [
          { label: t("Cancel", "Отмена") },
          {
            label: t("Save", "Сохранить"),
            kind: "primary",
            keepOpen: true,
            onClick: async (close) => {
              error.textContent = ""
              try {
                await root.kvist.setProvider({ provider: provider.id, api_key: key.value.trim(), model: model ? model.value.trim() : "" })
                close()
                await redraw()
              } catch (failureError) {
                error.textContent = failureError.message
              }
            },
          },
        ],
      })
    }

    box.replaceChildren(...build())
    return box
  }

  async function behaviorSection() {
    const box = h("div", { class: "settings-block" }, loading())
    let settings
    try {
      settings = (await root.kvist.getApiSettings()).settings || []
    } catch (error) {
      box.replaceChildren(failure(error.message))
      return box
    }
    const values = {}
    const rows = settings.map((setting) => {
      values[setting.key] = setting.value
      let control
      if (setting.kind === "bool") {
        control = toggle(setting.value === true, (on) => { values[setting.key] = on })
      } else {
        control = h("input", { class: "field-input narrow", type: "number", step: setting.kind === "float" ? "0.1" : "1", min: setting.minimum ?? null, max: setting.maximum ?? null, value: setting.zero_label && setting.value === 0 ? "" : String(setting.value), placeholder: setting.zero_label || "" })
        control.addEventListener("input", () => {
          const raw = control.value.trim()
          if (raw === "") values[setting.key] = setting.zero_label ? 0 : setting.default
          else values[setting.key] = setting.kind === "float" ? parseFloat(raw) : parseInt(raw, 10)
        })
      }
      return settingRow(setting.label, `${setting.description}${setting.unit ? ` (${setting.unit})` : ""}`, control)
    })
    const save = h("button", { class: "btn primary", type: "button" }, t("Save", "Сохранить"))
    const message = h("span", { class: "field-hint" })
    save.addEventListener("click", async () => {
      save.disabled = true
      message.textContent = ""
      try {
        await root.kvist.updateApiSettings(values)
        message.textContent = t("Saved. It applies to the next task.", "Сохранено. Применится к следующей задаче.")
      } catch (error) {
        message.textContent = error.message
        message.classList.add("is-warn")
      } finally {
        save.disabled = false
      }
    })
    box.replaceChildren(...rows, h("div", { class: "settings-actions" }, save, message))
    return box
  }

  async function integrationsSection() {
    const box = h("div", { class: "settings-block" }, loading())
    try {
      const [mcp, connections] = await Promise.all([root.kvist.mcpServers(), root.kvist.connections()])
      const list = (title, entries, empty, hint) => h("section", { class: "integration" }, h("h3", {}, title), entries.length ? entries.map((entry) => h("div", { class: "provider-row" }, h("div", { class: "provider-main" }, h("div", { class: "provider-name" }, entry.name || entry.id || JSON.stringify(entry)), entry.base_url || entry.command ? h("div", { class: "provider-models" }, entry.base_url || [entry.command, ...(entry.args || [])].join(" ")) : null))) : h("div", { class: "panel-empty small" }, empty), h("p", { class: "page-foot" }, hint))
      box.replaceChildren(
        list(t("MCP servers", "MCP-серверы"), mcp.servers || [], t("No MCP servers connected.", "MCP-серверов нет."), [t("Add one with ", "Добавить: "), h("code", {}, "kvist --mcp-op add"), "."]),
        list(t("Service connections", "Подключения к сервисам"), connections.connections || [], t("No connections saved.", "Подключений нет."), [t("Skills use these by name. Add with ", "Навыки используют их по имени. Добавить: "), h("code", {}, "kvist --connection-op add"), "."]),
      )
    } catch (error) {
      box.replaceChildren(failure(error.message))
    }
    return box
  }

  function dataSection(ctx) {
    const reset = h("button", { class: "btn danger", type: "button", disabled: ctx.isRunning() }, K.icon("trash", 14), t("Reset all settings…", "Сбросить все настройки…"))
    reset.addEventListener("click", async () => {
      if (!(await K.confirm({ title: t("Reset everything?", "Сбросить всё?"), text: t("This wipes ~/.kvist/config.json: every saved API key, custom provider and preference. It cannot be undone.", "Это удалит ~/.kvist/config.json: все ключи API, свои провайдеры и настройки. Отменить нельзя."), confirmLabel: t("Continue", "Продолжить"), danger: true }))) return
      const input = h("input", { class: "field-input", placeholder: "reset", "aria-label": t("Type reset", "Введи reset") })
      K.modal({
        title: t("Final confirmation", "Последнее подтверждение"),
        body: h("div", { class: "form" }, h("p", { class: "modal-text" }, t("Type “reset” to wipe the configuration.", "Введи «reset», чтобы стереть конфигурацию.")), input),
        actions: [
          { label: t("Cancel", "Отмена") },
          {
            label: t("Wipe everything", "Стереть всё"),
            kind: "danger",
            keepOpen: true,
            onClick: async (close) => {
              if (input.value.trim().toLowerCase() !== "reset") return
              try {
                await root.kvist.fullReset()
                K.store.set("run", "{}")
                ctx.onReset()
                close()
                K.toast(t("Configuration wiped.", "Конфигурация стёрта."))
              } catch (error) {
                K.toast(error.message, { kind: "error" })
              }
            },
          },
        ],
      })
    })
    const shortcuts = [
      ["Enter", t("send", "отправить")], ["Shift+Enter", t("new line", "новая строка")], ["Esc", t("pause the agent", "пауза агента")],
      ["Ctrl+N", t("new thread", "новый тред")], ["Ctrl+K", t("search and commands", "поиск и команды")], ["Ctrl+B", t("toggle the sidebar", "боковая панель")],
      ["Ctrl+J", t("commands drawer", "панель команд")], ["Ctrl+Shift+R", t("review panel", "панель Review")], ["Ctrl+,", t("settings", "настройки")],
    ]
    return h(
      "div",
      { class: "settings-block" },
      settingRow(t("Reset configuration", "Сброс конфигурации"), t("Wipes saved keys, custom providers and preferences.", "Удаляет ключи, свои провайдеры и настройки."), reset),
      h("section", { class: "integration" }, h("h3", {}, t("About", "О программе")), h("p", { class: "page-foot" }, `Kvist · ${t("protocol", "протокол")} v${ctx.protocolVersion} · ${ctx.workspace}`)),
      h("section", { class: "integration" }, h("h3", {}, t("Keyboard", "Клавиши")), h("div", { class: "kbd-grid" }, shortcuts.map(([key, label]) => h("div", { class: "kbd-row" }, h("kbd", {}, key), h("span", {}, label))))),
    )
  }

  async function settings(page, ctx, sectionId) {
    const id = SECTIONS.some((section) => section.id === sectionId) ? sectionId : "general"
    const nav = h("nav", { class: "settings-nav", "aria-label": t("Settings sections", "Разделы настроек") })
    for (const section of SECTIONS) {
      const button = h("button", { class: `settings-tab${section.id === id ? " is-active" : ""}`, type: "button" }, K.icon(section.icon, 15), section.label())
      button.addEventListener("click", () => ctx.openSettings(section.id))
      nav.append(button)
    }
    const content = h("div", { class: "settings-content" }, loading())
    page.replaceChildren(pageHeader(t("Settings", "Настройки")), h("div", { class: "settings-layout" }, nav, content))
    const builders = {
      general: () => generalSection(),
      models: () => modelsSection(ctx),
      behavior: () => behaviorSection(),
      integrations: () => integrationsSection(),
      data: () => dataSection(ctx),
    }
    const section = await builders[id]()
    content.replaceChildren(h("h2", { class: "settings-title" }, SECTIONS.find((entry) => entry.id === id).label()), section)
  }

  // --- the custom provider form ---------------------------------------------------------------------------
  function customProviderDialog(onDone) {
    const field = (label, control, hint) => h("label", { class: "field" }, h("span", { class: "field-label" }, label), control, hint ? h("span", { class: "field-hint" }, hint) : null)
    const name = h("input", { class: "field-input", placeholder: "my-gateway", autocomplete: "off" })
    const label = h("input", { class: "field-input", placeholder: t("Display name (optional)", "Название (необязательно)") })
    const protocol = h("select", { class: "field-input" }, h("option", { value: "openai" }, "OpenAI-compatible"), h("option", { value: "anthropic" }, "Anthropic-compatible"))
    const baseUrl = h("input", { class: "field-input", placeholder: "https://api.example.com/v1", autocomplete: "off" })
    const apiKey = h("input", { class: "field-input", type: "password", autocomplete: "off", placeholder: "API key" })
    const fast = h("input", { class: "field-input", placeholder: t("fast model id", "id быстрой модели") })
    const smart = h("input", { class: "field-input", placeholder: t("smart model id (empty = same)", "id умной модели (пусто = та же)") })
    const streaming = h("input", { type: "checkbox", checked: true })
    const error = h("div", { class: "field-hint is-warn" })
    K.modal({
      title: t("Add a custom provider", "Добавить свой провайдер"),
      wide: true,
      body: h("div", { class: "form" }, field(t("Name", "Имя"), name), field(t("Label", "Подпись"), label), field(t("Protocol", "Протокол"), protocol), field("Base URL", baseUrl), field(t("API key", "Ключ API"), apiKey), field(t("Fast model", "Быстрая модель"), fast), field(t("Smart model", "Умная модель"), smart), h("label", { class: "check" }, streaming, t("Stream answers", "Потоковые ответы")), error),
      actions: [
        { label: t("Cancel", "Отмена") },
        {
          label: t("Create", "Создать"),
          kind: "primary",
          keepOpen: true,
          onClick: async (close) => {
            error.textContent = ""
            const fastId = fast.value.trim()
            const smartId = smart.value.trim() || fastId
            try {
              const result = await root.kvist.addCustomProvider({ name: name.value.trim(), label: label.value.trim(), protocol: protocol.value, base_url: baseUrl.value.trim(), api_key: apiKey.value.trim(), streaming: streaming.checked, models: { fast: fastId, smart: smartId } })
              close()
              onDone?.(result)
            } catch (failureError) {
              error.textContent = failureError.message
            }
          },
        },
      ],
    })
  }

  K.pages = { skills, memory, settings, customProviderDialog }
})(typeof window !== "undefined" ? window : globalThis)
