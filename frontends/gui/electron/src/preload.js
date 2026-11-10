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

// Electron prefixes a main-process error with "Error invoking remote method ...";
// the person should read only the message the core produced.
const IPC_PREFIX = /^Error invoking remote method '[^']+': (?:Error: )?/
const invoke = (channel, payload) => ipcRenderer.invoke(channel, payload).catch((error) => {
  throw new Error(String(error?.message || error).replace(IPC_PREFIX, ""))
})

contextBridge.exposeInMainWorld("kvist", {
  // projects and sessions ("threads" in the UI)
  bootstrap: () => invoke("kvist:bootstrap"),
  navigation: () => invoke("kvist:navigation"),
  chooseWorkspace: () => invoke("kvist:choose-workspace"),
  activateProject: (workspace) => invoke("kvist:activate-project", workspace),
  removeProject: (workspace) => invoke("kvist:remove-project", workspace),
  newSession: (workspace) => invoke("kvist:new-session", workspace),
  switchSession: (workspace, name) => invoke("kvist:switch-session", { workspace, name }),
  deleteSession: (workspace, name) => invoke("kvist:delete-session", { workspace, name }),
  sessionHistory: (workspace, name, before) => invoke(
    "kvist:session-history",
    { workspace, name, before },
  ),
  inputHistory: () => invoke("kvist:input-history"),
  inputHistoryAdd: (text) => invoke("kvist:input-history-add", text),

  // the running task
  start: (goal, workspace, options) => invoke("kvist:start", { goal, workspace, options }),
  resume: (workspace, options) => invoke("kvist:resume", { workspace, options }),
  respond: (payload) => invoke("kvist:respond", payload),
  stop: () => invoke("kvist:stop"),
  pause: () => invoke("kvist:pause"),
  stopAll: () => invoke("kvist:stop-all"),
  takeover: () => invoke("kvist:takeover"),
  stopHelper: (agentId) => invoke("kvist:stop-helper", { agentId }),
  openViewer: (url) => invoke("kvist:open-viewer", { url }),
  showArtifact: (path) => invoke("kvist:show-artifact", { path }),

  // configuration
  configInfo: () => invoke("kvist:config-info"),
  addCustomProvider: (payload) => invoke("kvist:add-custom-provider", payload),
  deleteCustomProvider: (name) => invoke("kvist:delete-custom-provider", { name }),
  setProvider: (payload) => invoke("kvist:set-provider", payload),
  listModels: (payload) => invoke("kvist:list-models", payload),
  chatgptLogin: () => invoke("kvist:chatgpt-login"),
  chatgptLogout: () => invoke("kvist:chatgpt-logout"),
  getApiSettings: () => invoke("kvist:get-api-settings"),
  updateApiSettings: (payload) => invoke("kvist:update-api-settings", payload),
  fullReset: () => invoke("kvist:full-reset"),
  changes: () => invoke("kvist:changes"),
  revert: () => invoke("kvist:revert"),

  // catalogs
  skills: () => invoke("kvist:skills"),
  memory: () => invoke("kvist:memory"),
  memoryForget: (id) => invoke("kvist:memory-forget", { id }),
  mcpServers: () => invoke("kvist:mcp"),
  connections: () => invoke("kvist:connections"),

  // the workspace
  gitInfo: () => invoke("kvist:git-info"),
  gitDiff: () => invoke("kvist:git-diff"),
  gitCommit: (message, paths) => invoke("kvist:git-commit", { message, paths }),
  commitMessage: (paths, recentSubjects) => invoke("kvist:commit-message", { paths, recentSubjects }),
  listFiles: (query) => invoke("kvist:list-files", query),
  openProjectFolder: () => invoke("kvist:open-project-folder"),
  setTheme: (theme) => invoke("kvist:set-theme", theme),

  renderMarkdown,
  onEvent: (listener) => subscribe("kvist:event", listener),
  onExit: (listener) => subscribe("kvist:exit", listener),
  onProtocolError: (listener) => subscribe("kvist:protocol-error", listener),
  onProcessError: (listener) => subscribe("kvist:process-error", listener),
})
