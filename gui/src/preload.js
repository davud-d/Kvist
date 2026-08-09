"use strict"

const { contextBridge, ipcRenderer } = require("electron")
const { marked } = require("marked")
const sanitizeHtml = require("sanitize-html")

marked.use({ gfm: true, breaks: true })

function renderMarkdown(markdown) {
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

function subscribe(channel, listener) {
  const wrapped = (_event, payload) => listener(payload)
  ipcRenderer.on(channel, wrapped)
  return () => ipcRenderer.removeListener(channel, wrapped)
}

contextBridge.exposeInMainWorld("kvist", {
  bootstrap: () => ipcRenderer.invoke("kvist:bootstrap"),
  chooseWorkspace: () => ipcRenderer.invoke("kvist:choose-workspace"),
  start: (goal, workspace, options) => ipcRenderer.invoke(
    "kvist:start",
    { goal, workspace, options },
  ),
  respond: (payload) => ipcRenderer.invoke("kvist:respond", payload),
  stop: () => ipcRenderer.invoke("kvist:stop"),
  renderMarkdown,
  onEvent: (listener) => subscribe("kvist:event", listener),
  onExit: (listener) => subscribe("kvist:exit", listener),
  onProtocolError: (listener) => subscribe("kvist:protocol-error", listener),
  onProcessError: (listener) => subscribe("kvist:process-error", listener),
})
