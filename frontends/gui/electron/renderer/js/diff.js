"use strict"
// Unified-diff parsing (pure, unit-tested in node) and its DOM rendering.

;(function defineDiff(root) {
  const K = (root.K = root.K || {})

  const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/
  const MAX_RENDERED_LINES = 600

  function stripPrefix(value) {
    if (value === "/dev/null") return value
    return value.replace(/^[ab]\//, "").replace(/\t.*$/, "")
  }

  // Parses both `git diff` output (several files, "diff --git" headers) and the
  // bare single-file patches Kvist attaches to an action_result ("--- a/x").
  function parseUnifiedDiff(text) {
    const files = []
    let file = null
    let hunk = null
    let oldNo = 0
    let newNo = 0

    function startFile(path) {
      file = { path, oldPath: path, status: "modified", added: 0, removed: 0, hunks: [], binary: false }
      files.push(file)
      hunk = null
    }

    for (const raw of String(text || "").split("\n")) {
      const git = /^diff --git a\/(.+) b\/(.+)$/.exec(raw)
      if (git) {
        startFile(git[2])
        file.oldPath = git[1]
        continue
      }
      if (raw.startsWith("--- ") && !hunk) {
        const path = stripPrefix(raw.slice(4))
        if (!file) startFile(path === "/dev/null" ? "" : path)
        if (path === "/dev/null") file.status = "added"
        else file.oldPath = path
        continue
      }
      if (raw.startsWith("+++ ") && file && !hunk) {
        const path = stripPrefix(raw.slice(4))
        if (path === "/dev/null") file.status = "deleted"
        else file.path = path
        continue
      }
      if (!file) continue
      if (raw.startsWith("new file mode")) { file.status = "added"; continue }
      if (raw.startsWith("deleted file mode")) { file.status = "deleted"; continue }
      if (raw.startsWith("rename from ")) { file.status = "renamed"; file.oldPath = raw.slice(12); continue }
      if (raw.startsWith("Binary files") || raw.startsWith("GIT binary patch")) { file.binary = true; continue }

      const header = HUNK.exec(raw)
      if (header) {
        hunk = { header: raw, lines: [] }
        file.hunks.push(hunk)
        oldNo = Number(header[1])
        newNo = Number(header[2])
        continue
      }
      if (!hunk) continue
      if (raw.startsWith("\\")) {
        hunk.lines.push({ type: "meta", text: raw })
      } else if (raw.startsWith("+")) {
        hunk.lines.push({ type: "add", text: raw.slice(1), newNo })
        newNo += 1
        file.added += 1
      } else if (raw.startsWith("-")) {
        hunk.lines.push({ type: "del", text: raw.slice(1), oldNo })
        oldNo += 1
        file.removed += 1
      } else if (raw.startsWith(" ")) {
        hunk.lines.push({ type: "ctx", text: raw.slice(1), oldNo, newNo })
        oldNo += 1
        newNo += 1
      }
    }
    return files.filter((item) => item.path || item.hunks.length)
  }

  function statusLabel(status) {
    return {
      added: "new",
      deleted: "deleted",
      renamed: "renamed",
    }[status] || ""
  }

  function counts(file) {
    return K.h(
      "span",
      { class: "diff-counts" },
      K.h("span", { class: "plus" }, `+${file.added}`),
      K.h("span", { class: "minus" }, `−${file.removed}`),
    )
  }

  function renderLines(file) {
    const body = K.h("div", { class: "diff-body" })
    let rendered = 0
    for (const hunk of file.hunks) {
      body.append(K.h("div", { class: "dl hunk" }, K.h("span", { class: "ln" }), K.h("span", { class: "ln" }), K.h("span", { class: "code" }, hunk.header)))
      for (const line of hunk.lines) {
        if (rendered >= MAX_RENDERED_LINES) {
          const more = K.h("button", { class: "diff-more", type: "button" }, "Show the rest of this file")
          more.addEventListener("click", () => {
            const full = renderLines({ ...file, __all: true })
            body.replaceWith(full)
          })
          if (!file.__all) {
            body.append(more)
            return body
          }
        }
        const sign = line.type === "add" ? "+" : line.type === "del" ? "−" : " "
        body.append(K.h(
          "div",
          { class: `dl ${line.type}` },
          K.h("span", { class: "ln" }, line.oldNo ?? ""),
          K.h("span", { class: "ln" }, line.newNo ?? ""),
          K.h("span", { class: "sign" }, line.type === "meta" ? "" : sign),
          K.h("span", { class: "code" }, line.text),
        ))
        rendered += 1
      }
    }
    return body
  }

  function renderBody(file) {
    if (file.binary) return K.h("div", { class: "diff-note" }, "Binary file")
    if (!file.hunks.length) return K.h("div", { class: "diff-note" }, "No textual changes")
    return renderLines(file)
  }

  // A collapsible card for one file. `open` decides the initial state.
  function renderDiffFile(file, { open = true, extra = null } = {}) {
    const card = K.h("section", { class: `diff-file${open ? " is-open" : ""}`, "data-path": file.path })
    const head = K.h(
      "button",
      { class: "diff-head", type: "button", "aria-expanded": String(open) },
      K.icon("chevronRight", 14),
      K.h("span", { class: "diff-path", title: file.path }, file.path),
      statusLabel(file.status) ? K.h("span", { class: "tag" }, statusLabel(file.status)) : null,
      counts(file),
    )
    let bodyNode = null
    const ensureBody = () => {
      if (bodyNode) return
      bodyNode = renderBody(file)
      card.append(bodyNode)
    }
    head.addEventListener("click", () => {
      const next = !card.classList.contains("is-open")
      card.classList.toggle("is-open", next)
      head.setAttribute("aria-expanded", String(next))
      if (next) ensureBody()
    })
    card.append(extra ? K.h("div", { class: "diff-head-row" }, head, extra) : head)
    if (open) ensureBody()
    return card
  }

  K.diff = { parseUnifiedDiff, renderDiffFile, renderBody, counts }

  if (typeof module === "object" && module.exports) module.exports = { parseUnifiedDiff }
})(typeof window !== "undefined" ? window : globalThis)
