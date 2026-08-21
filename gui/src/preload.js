"use strict"

const { contextBridge, ipcRenderer } = require("electron")

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;")
}

// Markdown is presentation-only. A missing or incompatible optional renderer
// must not prevent the IPC bridge from being exposed: without the bridge the
// app cannot bootstrap a workspace or start Kvist at all.
let renderMarkdown = (markdown) => escapeHtml(markdown).replaceAll("\n", "<br>")
try {
  const { marked } = require("marked")
  const sanitizeHtml = require("sanitize-html")
  marked.use({ gfm: true, breaks: true })
  renderMarkdown = (markdown) => {
    const rendered = marked.parse(String(markdown ?? ""), { async: false })
    return sanitizeHtml(rendered, {
      allowedTags: [
        "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4",
        "h5", "h6", "hr", "li", "ol", "p", "pre", "strong", "table", "tbody",
        "td", "th", "thead", "tr", "ul",
      ],
      allowedAttributes: {
        a: ["href", "title", "target", "rel"],
        code: ["class"],
      },
      allowedSchemes: ["http", "https", "mailto"],
      transformTags: {
        a: (tagName, attribs) => ({
          tagName,
          attribs: { ...attribs, target: "_blank", rel: "noreferrer noopener" },
        }),
      },
    })
  }
} catch (error) {
  console.warn(`Kvist markdown renderer unavailable; using plain text: ${error.message}`)
}

function subscribe(channel, listener) {
  const wrapped = (_event, payload) => listener(payload)
  ipcRenderer.on(channel, wrapped)
  return () => ipcRenderer.removeListener(channel, wrapped)
}

contextBridge.exposeInMainWorld("kvist", {
  bootstrap: () => ipcRenderer.invoke("kvist:bootstrap"),
  navigation: () => ipcRenderer.invoke("kvist:navigation"),
  configInfo: () => ipcRenderer.invoke("kvist:config-info"),
  chooseWorkspace: () => ipcRenderer.invoke("kvist:choose-workspace"),
  activateProject: (workspace) => ipcRenderer.invoke("kvist:activate-project", workspace),
  newSession: (workspace) => ipcRenderer.invoke("kvist:new-session", workspace),
  switchSession: (workspace, name) => ipcRenderer.invoke(
    "kvist:switch-session",
    { workspace, name },
  ),
  start: (goal, workspace, options) => ipcRenderer.invoke(
    "kvist:start",
    { goal, workspace, options },
  ),
  resume: (workspace, options) => ipcRenderer.invoke("kvist:resume", { workspace, options }),
  deleteSession: (workspace, name) => ipcRenderer.invoke(
    "kvist:delete-session",
    { workspace, name },
  ),
  changes: () => ipcRenderer.invoke("kvist:changes"),
  revert: () => ipcRenderer.invoke("kvist:revert"),
  respond: (payload) => ipcRenderer.invoke("kvist:respond", payload),
  stop: () => ipcRenderer.invoke("kvist:stop"),
  renderMarkdown,
  onEvent: (listener) => subscribe("kvist:event", listener),
  onExit: (listener) => subscribe("kvist:exit", listener),
  onProtocolError: (listener) => subscribe("kvist:protocol-error", listener),
  onProcessError: (listener) => subscribe("kvist:process-error", listener),
})
