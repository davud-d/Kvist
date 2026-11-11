"use strict"
// Full-page views that replace the thread: Skills, Memory and Settings.

;(function definePages(root) {
  const K = (root.K = root.K || {})
  const { h } = K

  function pageHeader(title, subtitle, ...actions) {
    return h("header", { class: "page-head" }, h("div", {}, h("h1", {}, title), subtitle ? h("p", { class: "page-sub" }, subtitle) : null), h("div", { class: "page-actions" }, actions))
  }

  const loading = () => h("div", { class: "panel-empty" }, h("span", { class: "spinner" }))
  const failure = (message) => h("div", { class: "card error-card" }, K.icon("alert", 15), h("span", {}, message))
  const heading = (text) => h("h2", { class: "group-heading" }, text)
  const card = (...rows) => h("div", { class: "group-card" }, rows)

  // --- Skills ------------------------------------------------------------------------------------
  function itemRow(skill) {
    const side = h("div", { class: "item-side" },
      skill.approved_here ? h("span", { class: "badge is-ok" }, "Approved here") : null,
      !skill.available ? h("span", { class: "badge is-warn" }, skill.reason || "Unavailable") : null,
    )
    return h("div", { class: `item-row${skill.available ? "" : " is-dim"}`, title: (skill.capabilities || []).join(", ") },
      h("span", { class: "item-icon" }, K.icon("sparkle", 20)),
      h("div", { class: "item-main" }, h("div", { class: "item-name" }, skill.name), h("div", { class: "item-desc" }, skill.summary)),
      side,
    )
  }

  async function skills(page) {
    page.classList.add("is-wide")
    const title = "Skills"
    const subtitle = "Packages that give Kvist new tools: a browser, an app screen, design checks and more."
    page.replaceChildren(pageHeader(title, subtitle), loading())
    let result
    try {
      result = await root.kvist.skills()
    } catch (error) {
      page.replaceChildren(pageHeader(title), failure(error.message))
      return
    }
    const all = result.skills || []
    const ready = all.filter((skill) => skill.available)
    const blocked = all.filter((skill) => !skill.available)
    page.replaceChildren(
      pageHeader(title, subtitle),
      heading(`Installed (${ready.length})`),
      ready.length ? h("div", { class: "item-grid" }, ready.map(itemRow)) : h("div", { class: "panel-empty" }, "No skills installed."),
      blocked.length ? [heading(`Unavailable (${blocked.length})`), h("div", { class: "item-grid" }, blocked.map(itemRow))] : null,
      h("p", { class: "page-foot" }, "Install, approve or remove skills with ", h("code", {}, "kvist --skill-op …"), " (see docs/SKILLS.md)."),
    )
  }

  // --- Memory -------------------------------------------------------------------------------------
  async function memory(page) {
    page.classList.remove("is-wide")
    const title = "Memory"
    const subtitle = "What Kvist remembers about you and your projects. Everything here is yours to read and delete."
    page.replaceChildren(pageHeader(title, subtitle), loading())
    let data
    try {
      data = await root.kvist.memory()
    } catch (error) {
      page.replaceChildren(pageHeader(title), failure(error.message))
      return
    }
    const stores = data.status?.stores || {}
    const stats = h("div", { class: "stat-row" },
      h("div", { class: "stat" }, h("b", {}, stores.user?.total ?? 0), h("span", {}, "about you")),
      h("div", { class: "stat" }, h("b", {}, stores.workspace?.total ?? 0), h("span", {}, "about this project")),
      h("div", { class: "stat" }, h("b", {}, data.status?.paused ? "paused" : "on"), h("span", {}, "memory")),
    )
    const list = h("div", { class: "group-card" })
    const render = () => {
      K.clear(list)
      const records = data.records.filter((record) => record.status === "active")
      if (!records.length) list.append(h("div", { class: "panel-empty" }, "Nothing remembered yet."))
      for (const record of records) {
        const forget = h("button", { class: "btn small", type: "button" }, K.icon("trash", 13), "Forget")
        forget.addEventListener("click", async () => {
          if (!(await K.confirm({ title: "Forget this?", text: record.content, confirmLabel: "Forget", danger: true }))) return
          try {
            await root.kvist.memoryForget(record.id)
            data.records = data.records.filter((entry) => entry.id !== record.id)
            render()
          } catch (error) {
            K.toast(error.message, { kind: "error" })
          }
        })
        list.append(h("article", { class: "memory-card" },
          h("p", { class: "memory-text" }, record.content),
          h("div", { class: "memory-foot" }, h("span", { class: "badge" }, record.scope === "user" ? "about you" : "project"), h("span", { class: "badge" }, record.kind), record.topic ? h("span", { class: "badge" }, record.topic) : null, h("span", { class: "grow" }), forget),
        ))
      }
    }
    render()
    page.replaceChildren(pageHeader(title, subtitle), stats, list)
  }

  // --- Settings -----------------------------------------------------------------------------------
  const SECTIONS = [
    { id: "general", icon: "gear", label: "General" },
    { id: "models", icon: "key", label: "Models & providers" },
    { id: "behavior", icon: "bolt", label: "Agent behavior" },
    { id: "integrations", icon: "plug", label: "Integrations" },
    { id: "data", icon: "shield", label: "Data & about" },
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

  function generalSection(ctx) {
    return h("div", {},
      heading("Permissions"),
      card(settingRow("Ask before risky commands", "Kvist asks you before running a risky command or overwriting a file. Off: risky commands are refused without asking.", toggle(ctx.approvalMode() === "ask", (on) => ctx.setApprovalMode(on ? "ask" : "deny")))),
      heading("Appearance"),
      card(settingRow("Theme", "", select([["system", "System"], ["light", "Light"], ["dark", "Dark"]], K.store.get("theme", "system"), (value) => { K.store.set("theme", value); K.applyTheme() }))),
      heading("Diagnostics"),
      card(settingRow("Show agent debug messages", "Adds the core's diagnostic notes to the thread.", toggle(K.store.get("debug") === "1", (on) => K.store.set("debug", on ? "1" : "0")))),
    )
  }

  async function modelsSection(ctx) {
    const box = h("div", {}, loading())
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

    // A provider is a parent row (logo, name, status, action); its models are the children.
    const expanded = new Set()

    function modelsOf(provider) {
      const roleOf = (value) => ["fast", "smart"].filter((role) => (provider.saved_models?.[role] || provider.defaults?.[role]) === value)
      const listed = provider.options?.length
        ? provider.options.map((option) => ({ value: option.value, label: option.label || option.value, description: option.description || "", icon: option.icon || provider.model_icons?.[option.value] || "" }))
        : [...new Set([provider.defaults?.fast, provider.defaults?.smart, provider.saved_models?.fast, provider.saved_models?.smart].filter(Boolean))]
          .map((value) => ({ value, label: value, description: "", icon: provider.model_icons?.[value] || "" }))
      return listed.map((model) => ({ ...model, roles: roleOf(model.value) }))
    }

    function modelRow(provider, model) {
      return h("div", { class: "model-row" },
        K.brand(model.icon || provider.icon, model.label, 22),
        h("div", { class: "model-text" },
          h("div", { class: "model-label" }, model.label),
          model.label !== model.value ? h("div", { class: "model-id" }, model.value) : null,
          model.description ? h("div", { class: "model-desc" }, model.description) : null,
        ),
        model.roles.map((role) => h("span", { class: "badge" }, role === "fast" ? "fast model" : "smart model")),
      )
    }

    function providerRow(provider) {
      const ready = provider.has_credentials
      const status = h("span", { class: `badge ${ready ? "is-ok" : "is-warn"}` }, ready ? (provider.auth_type === "oauth" ? "Signed in" : "Key saved") : (provider.auth_type === "oauth" ? "Not signed in" : "No key"))
      const actions = h("div", { class: "item-side" })
      if (provider.auth_type === "oauth") {
        const button = h("button", { class: "btn small", type: "button" }, ready ? "Sign out" : "Sign in with ChatGPT")
        button.addEventListener("click", async () => {
          button.disabled = true
          button.textContent = "Waiting for the browser…"
          try {
            await (ready ? root.kvist.chatgptLogout() : root.kvist.chatgptLogin())
          } catch (error) {
            K.toast(error.message, { kind: "error" })
          }
          await redraw()
        })
        actions.append(button)
      } else if (provider.custom) {
        actions.append(h("button", { class: "btn small", type: "button", onclick: () => deleteCustom(provider) }, K.icon("trash", 13), "Delete"))
      } else {
        actions.append(h("button", { class: "btn small", type: "button", onclick: () => keyDialog(provider) }, K.icon("key", 13), ready ? "Replace key" : "Add key"))
      }
      const models = modelsOf(provider)
      const open = expanded.has(provider.id)
      const toggleButton = h("button", { class: "provider-toggle", type: "button", "aria-expanded": String(open), "aria-label": `Models of ${provider.label}`, title: "Models" }, K.icon("chevronRight", 15))
      toggleButton.addEventListener("click", () => {
        if (expanded.has(provider.id)) expanded.delete(provider.id)
        else expanded.add(provider.id)
        box.replaceChildren(...build())
      })
      const summary = models.length ? `${models.length} model${models.length > 1 ? "s" : ""}` : provider.custom ? provider.base_url || "" : "Models are set per task"
      const row = h("div", { class: "provider-row" },
        toggleButton,
        h("span", { class: "provider-tile" }, K.brand(provider.icon, provider.label, 24)),
        h("div", { class: "provider-main" }, h("div", { class: "provider-name" }, provider.label), h("div", { class: "provider-models" }, summary)),
        status,
        actions,
      )
      const wrapper = h("div", { class: `provider${open ? " is-open" : ""}` }, row)
      if (open) {
        wrapper.append(h("div", { class: "provider-models-list" }, models.length ? models.map((model) => modelRow(provider, model)) : h("div", { class: "model-empty" }, "No models listed. Type the ids you want in the box under the message field.")))
      }
      return wrapper
    }

    const build = () => {
      const all = [...(info.providers || []), ...(info.custom_providers || [])]
      const connected = all.filter((provider) => provider.has_credentials)
      const rest = all.filter((provider) => !provider.has_credentials)
      const nodes = [h("p", { class: "page-sub" }, `Saved default: ${info.saved_provider || "none"}. Pick a provider, models and role for a task in the box under the message field.`)]
      nodes.push(heading(`Connected (${connected.length})`), card(...connected.map(providerRow)))
      if (rest.length) {
        const more = h("details", { class: "more-providers" }, h("summary", {}, `Not connected (${rest.length})`), card(...rest.map(providerRow)))
        more.open = moreOpen ?? connected.length === 0
        more.addEventListener("toggle", () => { moreOpen = more.open })
        nodes.push(more)
      }
      nodes.push(h("div", { class: "settings-actions" }, h("button", { class: "btn", type: "button", onclick: () => ctx.addProvider(redraw) }, K.icon("plus", 14), "Add a custom provider")))
      return nodes
    }

    async function deleteCustom(provider) {
      if (!(await K.confirm({ title: "Delete this provider?", text: provider.label, confirmLabel: "Delete", danger: true }))) return
      try {
        await root.kvist.deleteCustomProvider(provider.id)
        await redraw()
      } catch (error) {
        K.toast(error.message, { kind: "error" })
      }
    }

    function keyDialog(provider) {
      const key = h("input", { class: "field-input", type: "password", autocomplete: "off", placeholder: "API key", "aria-label": "API key" })
      const model = provider.requires_manual_models ? h("input", { class: "field-input", placeholder: "Model id used for every role" }) : null
      const error = h("div", { class: "field-hint is-warn" })
      K.modal({
        title: `${provider.label}: API key`,
        body: h("div", { class: "form" }, h("p", { class: "modal-text" }, "The key is stored in ~/.kvist/config.json and also becomes the saved default provider."), h("label", { class: "field" }, h("span", { class: "field-label" }, "API key"), key), model ? h("label", { class: "field" }, h("span", { class: "field-label" }, "Model"), model) : null, error),
        actions: [
          { label: "Cancel" },
          {
            label: "Save",
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
    const box = h("div", {}, loading())
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
    const save = h("button", { class: "btn primary", type: "button" }, "Save")
    const message = h("span", { class: "field-hint" })
    save.addEventListener("click", async () => {
      save.disabled = true
      message.textContent = ""
      message.classList.remove("is-warn")
      try {
        await root.kvist.updateApiSettings(values)
        message.textContent = "Saved. It applies to the next task."
      } catch (error) {
        message.textContent = error.message
        message.classList.add("is-warn")
      } finally {
        save.disabled = false
      }
    })
    box.replaceChildren(card(...rows), h("div", { class: "settings-actions" }, save, message))
    return box
  }

  async function integrationsSection() {
    const box = h("div", {}, loading())
    try {
      const [mcp, connections] = await Promise.all([root.kvist.mcpServers(), root.kvist.connections()])
      const list = (title, entries, empty, hint) => h("div", {},
        heading(title),
        entries.length
          ? card(...entries.map((entry) => h("div", { class: "provider-row" }, h("div", { class: "provider-main" }, h("div", { class: "provider-name" }, entry.name || entry.id || JSON.stringify(entry)), entry.base_url || entry.command ? h("div", { class: "provider-models" }, entry.base_url || [entry.command, ...(entry.args || [])].join(" ")) : null))))
          : card(h("div", { class: "panel-empty small" }, empty)),
        h("p", { class: "page-foot" }, hint),
      )
      box.replaceChildren(
        list("MCP servers", mcp.servers || [], "No MCP servers connected.", ["Add one with ", h("code", {}, "kvist --mcp-op add"), "."]),
        list("Service connections", connections.connections || [], "No connections saved.", ["Skills use these by name. Add with ", h("code", {}, "kvist --connection-op add"), "."]),
      )
    } catch (error) {
      box.replaceChildren(failure(error.message))
    }
    return box
  }

  function dataSection(ctx) {
    const reset = h("button", { class: "btn danger", type: "button", disabled: ctx.isRunning() }, K.icon("trash", 14), "Reset all settings…")
    reset.addEventListener("click", async () => {
      if (!(await K.confirm({ title: "Reset everything?", text: "This wipes ~/.kvist/config.json: every saved API key, custom provider and preference. It cannot be undone.", confirmLabel: "Continue", danger: true }))) return
      const input = h("input", { class: "field-input", placeholder: "reset", "aria-label": "Type reset" })
      K.modal({
        title: "Final confirmation",
        body: h("div", { class: "form" }, h("p", { class: "modal-text" }, "Type “reset” to wipe the configuration."), input),
        actions: [
          { label: "Cancel" },
          {
            label: "Wipe everything",
            kind: "danger",
            keepOpen: true,
            onClick: async (close) => {
              if (input.value.trim().toLowerCase() !== "reset") return
              try {
                await root.kvist.fullReset()
                K.store.set("run", "{}")
                ctx.onReset()
                close()
                K.toast("Configuration wiped.")
              } catch (error) {
                K.toast(error.message, { kind: "error" })
              }
            },
          },
        ],
      })
    })
    const shortcuts = [
      ["Enter", "send"], ["Shift+Enter", "new line"], ["Esc", "pause the agent"],
      ["Ctrl+N", "new thread"], ["Ctrl+K", "search"], ["Ctrl+O", "add a project folder"],
      ["Alt+1…9", "open a recent thread"], ["Ctrl+B", "side panel"], ["Ctrl+J", "commands drawer"],
      ["Ctrl+Shift+D", "review panel"], ["Ctrl+,", "settings"],
    ]
    return h("div", {},
      card(settingRow("Reset configuration", "Wipes saved keys, custom providers and preferences.", reset)),
      heading("About"),
      card(h("div", { class: "setting" }, h("div", { class: "setting-desc" }, `Kvist · protocol v${ctx.protocolVersion} · ${ctx.workspace}`))),
      heading("Keyboard"),
      card(h("div", { class: "kbd-grid" }, shortcuts.map(([key, label]) => h("div", { class: "kbd-row" }, h("kbd", {}, key), h("span", {}, label))))),
    )
  }

  // Draws one settings section into the page; the section list lives in the side panel.
  async function settings(page, ctx, sectionId) {
    page.classList.remove("is-wide")
    const id = SECTIONS.some((section) => section.id === sectionId) ? sectionId : "general"
    const label = SECTIONS.find((entry) => entry.id === id).label
    page.replaceChildren(pageHeader(label), loading())
    const builders = {
      general: () => generalSection(ctx),
      models: () => modelsSection(ctx),
      behavior: () => behaviorSection(),
      integrations: () => integrationsSection(),
      data: () => dataSection(ctx),
    }
    const section = await builders[id]()
    page.replaceChildren(pageHeader(label), section)
  }

  // --- the custom provider form ---------------------------------------------------------------------------
  function customProviderDialog(onDone) {
    const field = (label, control, hint) => h("label", { class: "field" }, h("span", { class: "field-label" }, label), control, hint ? h("span", { class: "field-hint" }, hint) : null)
    const name = h("input", { class: "field-input", placeholder: "my-gateway", autocomplete: "off" })
    const label = h("input", { class: "field-input", placeholder: "Display name (optional)" })
    const protocol = h("select", { class: "field-input" }, h("option", { value: "openai" }, "OpenAI-compatible"), h("option", { value: "anthropic" }, "Anthropic-compatible"))
    const baseUrl = h("input", { class: "field-input", placeholder: "https://api.example.com/v1", autocomplete: "off" })
    const apiKey = h("input", { class: "field-input", type: "password", autocomplete: "off", placeholder: "API key" })
    const fast = h("input", { class: "field-input", placeholder: "fast model id" })
    const smart = h("input", { class: "field-input", placeholder: "smart model id (empty = same)" })
    const streaming = h("input", { type: "checkbox", checked: true })
    const error = h("div", { class: "field-hint is-warn" })
    K.modal({
      title: "Add a custom provider",
      wide: true,
      body: h("div", { class: "form" }, field("Name", name), field("Label", label), field("Protocol", protocol), field("Base URL", baseUrl), field("API key", apiKey), field("Fast model", fast), field("Smart model", smart), h("label", { class: "check" }, streaming, "Stream answers"), error),
      actions: [
        { label: "Cancel" },
        {
          label: "Create",
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

  K.pages = { skills, memory, settings, customProviderDialog, SECTIONS }
})(typeof window !== "undefined" ? window : globalThis)
