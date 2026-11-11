"use strict"
// Small shared helpers: DOM builder, icons, EN/RU strings, toasts, modals, popovers.

;(function defineUtil(root) {
  const K = (root.K = root.K || {})

  // --- per-viewer storage: the page must work when it is blocked ---------------
  K.store = {
    get(key, fallback = "") {
      try {
        const value = root.localStorage.getItem(`kvist.${key}`)
        return value === null ? fallback : value
      } catch {
        return fallback
      }
    },
    set(key, value) {
      try {
        root.localStorage.setItem(`kvist.${key}`, String(value))
      } catch {
        // Storage is a convenience only.
      }
    },
  }

  // --- DOM ----------------------------------------------------------------------
  function append(parent, child) {
    if (child === null || child === undefined || child === false) return
    if (Array.isArray(child)) {
      for (const item of child) append(parent, item)
      return
    }
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)))
  }

  // h("button", {class: "x", onclick: fn, title: "t"}, "text", node, [nodes])
  K.h = function h(tag, attrs, ...children) {
    const node = document.createElement(tag)
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue
      if (key === "class") node.className = value
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value)
      else if (key === "text") node.textContent = value
      else if (value === true) node.setAttribute(key, "")
      else node.setAttribute(key, String(value))
    }
    append(node, children)
    return node
  }

  K.clear = (node) => { node.replaceChildren() }

  // --- icons --------------------------------------------------------------------
  const ICONS = {
    sidebar: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>',
    home: '<path d="M4 11l8-7 8 7v8a1 1 0 01-1 1h-4v-6H9v6H5a1 1 0 01-1-1z"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6"/>',
    arrowLeft: '<path d="M19 12H5M12 19l-7-7 7-7"/>',
    laptop: '<rect x="5" y="5" width="14" height="10" rx="2"/><path d="M3 19h18"/>',
    hand: '<path d="M8 12V6a1.5 1.5 0 013 0v5M11 11V4.5a1.5 1.5 0 013 0V11M14 11V6a1.5 1.5 0 013 0v7a6 6 0 01-6 6h-1a5 5 0 01-4-2l-2-3a1.5 1.5 0 012.3-1.8L8 14"/>',
    folderOpen: '<path d="M3 8a2 2 0 012-2h4l2 2h7a2 2 0 012 2v1H7l-3 8V8z"/><path d="M4 19l3-8h14l-3 8z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    compose: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    sparkle: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 16v4M17 18h4"/>',
    memory: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    terminal: '<path d="M4 17l6-5-6-5"/><path d="M12 19h8"/>',
    review: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M6 8.5v5a4 4 0 004 4h2"/><path d="M18 15.5v-5a4 4 0 00-4-4h-2"/>',
    folder: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
    folderPlus: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><path d="M12 11v5M9.5 13.5h5"/>',
    sliders: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    chevronRight: '<path d="M9 6l6 6-6 6"/>',
    chevronDown: '<path d="M6 9l6 6 6-6"/>',
    check: '<path d="M5 12l5 5L20 7"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    arrowUp: '<path d="M12 19V5M5 12l7-7 7 7"/>',
    arrowDown: '<path d="M12 5v14M5 12l7 7 7-7"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2.5" fill="currentColor"/>',
    paperclip: '<path d="M21 11l-9 9a5 5 0 01-7-7l9-9a3.5 3.5 0 015 5l-9 9a2 2 0 01-3-3l8-8"/>',
    branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M18 10c0 4-6 3-12 7"/>',
    file: '<path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z"/><path d="M14 3v5h5"/>',
    pause: '<path d="M8 5v14M16 5v14"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
    bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5"/>',
    monitor: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    bot: '<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 8V4M9 13h.01M15 13h.01"/>',
    copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 012-2h9"/>',
    pencil: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.7"/><path d="M21 16l-5-5-9 9"/>',
    key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 8l3 3"/>',
    plug: '<path d="M9 3v5M15 3v5M6 8h12v3a6 6 0 01-12 0zM12 17v4"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    alert: '<path d="M12 4l10 17H2z"/><path d="M12 10v5M12 18h.01"/>',
    refresh: '<path d="M20 11a8 8 0 00-14.5-4M4 4v4h4M4 13a8 8 0 0014.5 4M20 20v-4h-4"/>',
    undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>',
  }

  K.icon = function icon(name, size = 16) {
    const span = document.createElement("span")
    span.className = "icon"
    span.setAttribute("aria-hidden", "true")
    span.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`
    return span
  }

  // --- formatting ---------------------------------------------------------------
  K.timeAgo = function timeAgo(epochSeconds, now = Date.now() / 1000) {
    const seconds = Math.max(0, now - Number(epochSeconds || 0))
    if (!epochSeconds || seconds < 60) return "now"
    const minutes = Math.floor(seconds / 60)
    if (minutes < 60) return `${minutes}m`
    const hours = Math.floor(minutes / 60)
    if (hours < 24) return `${hours}h`
    const days = Math.floor(hours / 24)
    if (days < 30) return `${days}d`
    return `${Math.floor(days / 30)}mo`
  }

  K.duration = function duration(seconds) {
    const total = Math.max(0, Math.round(seconds))
    if (total < 60) return `${total}s`
    const minutes = Math.floor(total / 60)
    const rest = total % 60
    return `${minutes}m ${rest}s`
  }

  K.baseName = (value) => String(value || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() || String(value || "")

  K.formatTokens = (value) => {
    const number = Number(value) || 0
    if (number >= 1e6) return `${(number / 1e6).toFixed(1)}M`
    if (number >= 1e3) return `${(number / 1e3).toFixed(number >= 1e4 ? 0 : 1)}k`
    return String(number)
  }

  // --- toasts -------------------------------------------------------------------
  K.toast = function toast(text, { kind = "info", ms = 4500 } = {}) {
    const host = document.getElementById("toasts")
    if (!host) return
    const node = K.h("div", { class: `toast is-${kind}`, role: "status" }, text)
    host.append(node)
    root.setTimeout(() => node.remove(), ms)
  }

  // --- modals -------------------------------------------------------------------
  // actions: [{label, kind: "primary"|"danger"|"", onClick(close) -> maybe promise, keepOpen}]
  K.modal = function modal({ title, body, actions = [], wide = false, onClose }) {
    const host = document.getElementById("modals")
    const previousFocus = document.activeElement
    const backdrop = K.h("div", { class: "modal-backdrop" })
    const dialog = K.h("div", { class: `modal${wide ? " is-wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title })

    function close() {
      backdrop.remove()
      document.removeEventListener("keydown", onKey, true)
      if (previousFocus instanceof HTMLElement) previousFocus.focus()
      if (onClose) onClose()
    }
    function onKey(event) {
      if (event.key !== "Escape") return
      event.preventDefault()
      event.stopPropagation()
      close()
    }

    const buttons = actions.map((action) => {
      const button = K.h("button", { class: `btn ${action.kind ? `is-${action.kind}` : ""}`, type: "button" }, action.label)
      button.addEventListener("click", async () => {
        if (button.disabled) return
        button.disabled = true
        try {
          await action.onClick?.(close)
        } finally {
          button.disabled = false
        }
        if (!action.keepOpen) close()
      })
      return button
    })

    dialog.append(
      K.h("div", { class: "modal-head" }, K.h("h2", {}, title), K.h("button", { class: "icon-btn", type: "button", "aria-label": "Close", onclick: close }, K.icon("x"))),
      K.h("div", { class: "modal-body" }, body),
      buttons.length ? K.h("div", { class: "modal-actions" }, buttons) : null,
    )
    backdrop.append(dialog)
    backdrop.addEventListener("mousedown", (event) => { if (event.target === backdrop) close() })
    document.addEventListener("keydown", onKey, true)
    host.append(backdrop)
    const focusTarget = dialog.querySelector("input, textarea, select") || buttons.at(-1) || dialog
    root.requestAnimationFrame(() => focusTarget.focus?.())
    return { close, dialog, buttons }
  }

  // In-DOM confirmation: window.confirm is a silent no-op in Electron on Wayland.
  K.confirm = function confirm({ title, text, confirmLabel, danger = false }) {
    return new Promise((resolve) => {
      let settled = false
      const finish = (value) => { if (!settled) { settled = true; resolve(value) } }
      K.modal({
        title,
        body: K.h("p", { class: "modal-text" }, text),
        actions: [
          { label: "Cancel", onClick: () => finish(false) },
          { label: confirmLabel || "Continue", kind: danger ? "danger" : "primary", onClick: () => finish(true) },
        ],
        onClose: () => finish(false),
      })
    })
  }

  // --- popovers -----------------------------------------------------------------
  let openPopover = null

  K.closePopover = function closePopover() {
    if (!openPopover) return
    const { node, cleanup } = openPopover
    openPopover = null
    node.remove()
    cleanup()
  }

  // Anchored floating panel. placement: "above" | "below"; align: "start" | "end".
  K.popover = function popover(anchor, content, { placement = "below", align = "start", className = "" } = {}) {
    K.closePopover()
    const node = K.h("div", { class: `popover ${className}`, role: "menu" }, content)
    document.body.append(node)

    const rect = anchor.getBoundingClientRect()
    const width = node.offsetWidth
    const height = node.offsetHeight
    const margin = 8
    let left = align === "end" ? rect.right - width : rect.left
    left = Math.max(margin, Math.min(left, root.innerWidth - width - margin))
    let top = placement === "above" ? rect.top - height - 6 : rect.bottom + 6
    if (top < margin) top = Math.min(rect.bottom + 6, root.innerHeight - height - margin)
    if (top + height > root.innerHeight - margin) top = Math.max(margin, root.innerHeight - height - margin)
    node.style.left = `${left}px`
    node.style.top = `${top}px`

    const onDown = (event) => {
      if (!node.contains(event.target) && !anchor.contains(event.target)) K.closePopover()
    }
    const onKey = (event) => {
      if (event.key !== "Escape") return
      event.stopPropagation()
      K.closePopover()
    }
    document.addEventListener("mousedown", onDown, true)
    document.addEventListener("keydown", onKey, true)
    openPopover = {
      node,
      cleanup: () => {
        document.removeEventListener("mousedown", onDown, true)
        document.removeEventListener("keydown", onKey, true)
      },
    }
    return node
  }

  // A menu item row for popovers. With `description` it is the two-line form
  // (title over muted text); `checked` shows a tick; `warn` colours it orange.
  K.menuItem = function menuItem({ icon, iconNode, label, hint, description, active = false, checked = false, danger = false, warn = false, keepOpen = false, onClick, disabled = false }) {
    const mark = active || checked ? K.icon("check", 15) : null
    const body = description
      ? K.h("span", { class: "menu-texts" }, K.h("span", { class: "menu-label" }, label), K.h("span", { class: "menu-hint" }, description))
      : [K.h("span", { class: "menu-label" }, label), hint ? K.h("span", { class: "menu-hint" }, hint) : K.h("span", { class: "grow" })]
    return K.h(
      "button",
      { class: `menu-item${description ? " is-two" : ""}${danger ? " is-danger" : ""}${warn ? " is-warn" : ""}`, type: "button", role: "menuitem", disabled, onclick: () => { if (!keepOpen) K.closePopover(); onClick?.() } },
      icon ? K.icon(icon, 16) : iconNode || K.h("span", { class: "icon-gap" }),
      body,
      mark,
    )
  }

  K.copyText = async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text)
      K.toast("Copied", { ms: 1600 })
    } catch {
      K.toast("Could not copy", { kind: "error" })
    }
  }

  if (typeof module === "object" && module.exports) module.exports = K
})(typeof window !== "undefined" ? window : globalThis)
