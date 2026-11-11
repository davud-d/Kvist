"use strict"
// Draws a Thread's items. Updates replace one node, and streaming text is
// coalesced to one render per frame.

;(function defineTimeline(root) {
  const K = (root.K = root.K || {})
  const { h } = K

  const READ_TYPES = new Set(["read_file", "read_multiple_files", "list_dir", "list_tree", "find_files", "search_code", "git_status", "git_diff"])
  const STICK_DISTANCE = 120

  // --- how a call reads ------------------------------------------------------------
  function commandText(args) {
    const command = args?.command
    if (Array.isArray(command)) return command.map((part) => (/[\s"'$`\\]/.test(part) ? JSON.stringify(part) : part)).join(" ")
    return typeof command === "string" ? command : ""
  }

  function describe(call) {
    const a = call.args || {}
    const path = call.path || a.path || ""
    switch (call.type) {
      case "read_file": return { icon: "file", verb: "Read", subject: path }
      case "read_multiple_files": return { icon: "file", verb: "Read", subject: `${(a.paths || []).length} files` }
      case "write_file": return { icon: "pencil", verb: "Wrote", subject: path }
      case "append_file": return { icon: "pencil", verb: "Appended to", subject: path }
      case "replace_in_file": return { icon: "pencil", verb: "Edited", subject: path }
      case "list_dir":
      case "list_tree": return { icon: "folder", verb: "Listed", subject: path || "." }
      case "find_files": return { icon: "search", verb: "Found files", subject: a.pattern || "" }
      case "search_code": return { icon: "search", verb: "Searched", subject: a.query || "" }
      case "git_status": return { icon: "branch", verb: "Checked git status", subject: "" }
      case "git_diff": return { icon: "branch", verb: "Read git diff", subject: "" }
      case "run_shell": return { icon: "terminal", verb: a.background ? "Started" : call.state === "running" ? "Running" : "Ran", subject: commandText(a) || call.description }
      case "job_output": return { icon: "bolt", verb: "Read job output", subject: a.job_id || "" }
      case "job_stop": return { icon: "bolt", verb: "Stopped job", subject: a.job_id || "" }
      case "web_search": return { icon: "globe", verb: "Searched the web", subject: a.query || "" }
      case "fetch_url": return { icon: "globe", verb: "Opened", subject: a.url || "" }
      case "delegate_task": return { icon: "bot", verb: "Delegated", subject: a.task || call.description }
      case "generate_image": return { icon: "image", verb: "Drew an image", subject: a.prompt || "" }
      case "ask_user": return { icon: "info", verb: "Asked you", subject: a.question || "" }
      case "wait": return { icon: "pause", verb: "Waited", subject: "" }
      case "skill_call":
      case "skills": return { icon: "sparkle", verb: "Skill", subject: [a.skill, a.tool].filter(Boolean).join(" · ") || call.description }
      case "memory": return { icon: "memory", verb: "Memory", subject: a.op || call.description }
      case "helper": return { icon: "bot", verb: "Helper", subject: call.description }
      case "sessions": return { icon: "list", verb: "Sessions", subject: call.description }
      default: return { icon: "bolt", verb: call.type.replaceAll("_", " "), subject: call.description }
    }
  }

  const isEdit = (call) => K.EDIT_ACTIONS.has(call.type)
  const isQuiet = (call) => call.state === "ok" && !isEdit(call)

  function groupSummary(group) {
    let explored = 0
    let commands = 0
    let web = 0
    let other = 0
    for (const call of group.calls) {
      if (READ_TYPES.has(call.type)) explored += 1
      else if (call.type === "run_shell") commands += 1
      else if (call.type === "web_search" || call.type === "fetch_url") web += 1
      else if (!isEdit(call)) other += 1
    }
    const parts = []
    if (explored) parts.push(`Explored ${explored}`)
    if (commands) parts.push(`Ran ${commands} command${commands > 1 ? "s" : ""}`)
    if (web) parts.push(`Browsed ${web}`)
    if (other) parts.push(`${other} more`)
    return parts.join(" · ") || "Worked"
  }

  function isOpen(call) {
    if (call.open !== undefined) return call.open
    if (call.state === "writing") return true
    return call.type === "run_shell" && call.state === "running" && Boolean(call.live)
  }

  function lastLines(text, count) {
    const lines = String(text || "").split("\n")
    return lines.length > count ? [`… ${lines.length - count} ${"lines above"}`, ...lines.slice(-count)].join("\n") : lines.join("\n")
  }

  // The icon at the start of a row: the call's kind, or its state when it is not simply done.
  function leadingMark(call, kindIcon) {
    if (call.state === "failed") return K.icon("x", 15)
    if (call.state === "stopped") return K.icon("pause", 15)
    if (call.state === "running" || call.state === "writing") return h("span", { class: "spinner small", "aria-label": "Running" })
    return K.icon(kindIcon, 15)
  }

  function callBody(call) {
    const body = h("div", { class: "call-body" })
    const a = call.args || {}
    if (call.type === "run_shell") {
      const out = call.state === "running" ? call.live : call.output
      body.append(h("pre", { class: "term" }, h("span", { class: "prompt" }, "$ "), commandText(a), out ? `\n${lastLines(out, 60)}` : ""))
      if (a.justification) body.append(h("div", { class: "call-note" }, a.justification))
      if (call.exitCode !== null && call.exitCode !== 0) body.append(h("div", { class: "call-note is-error" }, `Exit code ${call.exitCode}`))
      return body
    }
    if (isEdit(call)) {
      if (call.state === "writing" || (call.state === "running" && !call.patch)) {
        const text = Object.values(call.writing || {}).join("\n──\n")
        body.append(h("pre", { class: "code-live" }, lastLines(text, 40) || "…"))
        return body
      }
      const files = call.patch ? K.diff.parseUnifiedDiff(call.patch) : []
      if (files.length) body.append(K.diff.renderBody(files[0]))
      else body.append(h("div", { class: "call-note" }, call.summary || "No changes"))
      if (call.state === "failed" && call.output) body.append(h("pre", { class: "term is-error" }, call.output))
      return body
    }
    if (call.summary) body.append(h("div", { class: "call-note" }, call.summary))
    if (call.output) body.append(h("pre", { class: `term${call.state === "failed" ? " is-error" : ""}` }, lastLines(call.output, 40)))
    if (!call.summary && !call.output) body.append(h("div", { class: "call-note" }, call.description || ""))
    return body
  }

  function renderCall(call, rerender) {
    const info = describe(call)
    const open = isOpen(call)
    const counts = isEdit(call) && call.state === "ok" && (call.added !== null || call.removed !== null)
      ? h("span", { class: "diff-counts" }, h("span", { class: "plus" }, `+${call.added || 0}`), h("span", { class: "minus" }, `−${call.removed || 0}`))
      : null
    const head = h(
      "button",
      { class: "call-head", type: "button", "aria-expanded": String(open), title: info.subject },
      leadingMark(call, info.icon),
      h("span", { class: "call-verb" }, info.verb),
      h("span", { class: "call-subject" }, info.subject),
      counts,
    )
    head.addEventListener("click", () => {
      call.open = !isOpen(call)
      rerender()
    })
    const node = h("div", { class: `call is-${call.state}${isQuiet(call) ? " is-quiet" : ""}${open ? " is-open" : ""}`, "data-type": call.type }, head)
    if (open) node.append(callBody(call))
    return node
  }

  // --- item renderers ------------------------------------------------------------------
  function enhanceMarkdown(container) {
    for (const pre of container.querySelectorAll("pre")) {
      const code = pre.querySelector("code")
      const lang = /language-([\w+-]+)/.exec(code?.className || "")?.[1] || ""
      const wrapper = h("div", { class: "codeblock" })
      const head = h(
        "div",
        { class: "codeblock-head" },
        h("span", {}, lang),
        h("button", { class: "icon-btn small", type: "button", title: "Copy", onclick: () => K.copyText(pre.textContent || "") }, K.icon("copy", 13)),
      )
      pre.replaceWith(wrapper)
      wrapper.append(head, pre)
    }
  }

  function renderMarkdown(text) {
    const node = h("div", { class: "md" })
    node.innerHTML = root.kvist.renderMarkdown(text)
    enhanceMarkdown(node)
    return node
  }

  const RENDERERS = {
    user(item) {
      return h("div", { class: "msg user" }, h("div", { class: "bubble" }, item.text, item.images ? h("div", { class: "bubble-note" }, `${item.images} image(s) attached`) : null))
    },

    assistant(item) {
      const node = h("div", { class: `msg assistant${item.streaming ? " is-streaming" : ""}${item.skill ? " is-skill" : ""}` })
      node.append(renderMarkdown(item.text))
      if (!item.streaming) {
        node.append(h("div", { class: "msg-actions" }, h("button", { class: "icon-btn small", type: "button", title: "Copy answer", onclick: () => K.copyText(item.text) }, K.icon("copy", 14))))
      }
      return node
    },

    thinking(item, ctx) {
      const label = item.done
        ? (item.seconds > 0 ? `Thought for ${K.duration(item.seconds)}` : "Thought")
        : "Thinking…"
      const node = h("details", { class: `thinking${item.done ? "" : " is-live"}` }, h("summary", {}, K.icon("chevronRight", 13), h("span", { class: "thinking-label" }, label)))
      if (item.open) node.open = true
      node.addEventListener("toggle", () => { item.open = node.open })
      if (item.text) node.append(h("div", { class: "thinking-text" }, item.text))
      return node
    },

    work(item, ctx) {
      const quiet = item.calls.filter(isQuiet).length
      const collapsible = item.closed && quiet >= 3
      if (collapsible && item.collapsed === undefined) item.collapsed = true
      const node = h("div", { class: `work${collapsible && item.collapsed ? " is-collapsed" : ""}` })
      if (collapsible) {
        const toggle = h(
          "button",
          { class: "work-head", type: "button", "aria-expanded": String(!item.collapsed) },
          K.icon("chevronRight", 14),
          h("span", {}, groupSummary(item)),
        )
        toggle.addEventListener("click", () => {
          item.collapsed = !item.collapsed
          ctx.rerender(item)
        })
        node.append(toggle)
      }
      const rows = h("div", { class: "work-rows" })
      for (const call of item.calls) rows.append(renderCall(call, () => ctx.rerender(item)))
      node.append(rows)
      return node
    },

    plan(item) {
      return h("div", { class: "card plan-card" }, h("div", { class: "card-title" }, K.icon("list", 14), "Plan"), renderMarkdown(item.markdown))
    },

    notice(item) {
      const icon = item.level === "error" ? "alert" : item.level === "warn" ? "alert" : "info"
      return h("div", { class: `notice is-${item.level}` }, K.icon(icon, 14), h("span", {}, item.text))
    },

    error(item) {
      return h("div", { class: "card error-card" }, K.icon("alert", 15), h("span", {}, item.text))
    },

    summary(item, ctx) {
      if (item.live) {
        const seconds = item.start ? (Date.now() - item.start) / 1000 : 0
        return h("div", { class: "worked is-live" }, `Working for ${K.duration(seconds)}`)
      }
      const open = !ctx.thread.folded.has(item.turn)
      const node = h("button", { class: `worked${open ? " is-open" : ""}`, type: "button", "aria-expanded": String(open) }, `Worked for ${K.duration(item.seconds)} `, K.icon("chevronRight", 15))
      node.addEventListener("click", () => ctx.toggleFold(item.turn))
      return node
    },

    status(item) {
      return h("div", { class: `status-chip is-${item.tone}` }, item.text)
    },

    job(item) {
      const running = item.status === "running"
      const command = Array.isArray(item.command) ? item.command.join(" ") : String(item.command || "")
      const label = running ? "Running in background"
        : item.status === "exited" ? `Exited with code ${item.exitCode}`
          : item.status === "timed_out" ? "Timed out" : "Stopped"
      return h(
        "div",
        { class: `card job-card${running ? " is-running" : ""}` },
        h("div", { class: "card-title" }, running ? h("span", { class: "spinner" }) : K.icon(item.exitCode === 0 ? "check" : "bolt", 14), h("span", { class: "card-name" }, item.purpose || command), h("span", { class: "card-meta" }, label)),
        command ? h("pre", { class: "term" }, h("span", { class: "prompt" }, "$ "), command, item.output ? `\n${lastLines(item.output, 12)}` : "") : null,
      )
    },

    helper(item) {
      const running = ["starting", "running", "paused"].includes(item.state)
      const stop = running
        ? h("button", { class: "btn small", type: "button", onclick: () => root.kvist.stopHelper(item.agentId).catch((error) => K.toast(error.message, { kind: "error" })) }, "Stop")
        : null
      return h(
        "div",
        { class: "card helper-card" },
        h("div", { class: "card-title" }, K.icon("bot", 14), h("span", { class: "card-name" }, `${"Helper"} ${item.agentId}`), h("span", { class: "card-meta" }, item.state), stop),
        item.task ? h("div", { class: "card-text" }, item.task) : null,
        item.activity && running ? h("div", { class: "card-sub" }, item.activity) : null,
        item.summary && !running ? h("div", { class: "card-sub" }, item.summary) : null,
      )
    },

    artifact(item) {
      const show = h("button", { class: "btn small", type: "button", onclick: () => root.kvist.showArtifact(item.path).catch((error) => K.toast(error.message, { kind: "error" })) }, K.icon("folder", 13), "Show in folder")
      return h(
        "div",
        { class: "card artifact-card" },
        item.preview ? h("img", { class: "artifact-preview", src: item.preview, alt: item.caption || item.name }) : null,
        h("div", { class: "card-title" }, K.icon(item.mime.startsWith("image/") ? "image" : "file", 14), h("span", { class: "card-name" }, item.name), show),
        item.caption ? h("div", { class: "card-sub" }, item.caption) : null,
      )
    },

    image(item) {
      if (item.state === "failed") return h("div", { class: "notice is-warn" }, K.icon("alert", 14), h("span", {}, `Image failed: ${item.error}`))
      return h("div", { class: "notice is-info" }, h("span", { class: "spinner" }), h("span", {}, "Drawing an image…"))
    },

    screen(item) {
      if (item.state === "closed" || item.state === "unavailable") return h("div", { class: "notice is-info" }, K.icon("monitor", 14), h("span", {}, `${item.title || "Screen"} closed`))
      const open = item.url
        ? h("button", { class: "btn small", type: "button", onclick: () => root.kvist.openViewer(item.url).catch((error) => K.toast(error.message, { kind: "error" })) }, K.icon("external", 13), "Open in browser")
        : null
      return h(
        "div",
        { class: "card screen-card" },
        h("div", { class: "card-title" }, K.icon("monitor", 14), h("span", { class: "card-name" }, item.title || "Agent screen"), h("span", { class: "card-meta" }, item.state), open),
        item.streamUrl && item.state !== "starting" ? h("img", { class: "screen-preview", src: item.streamUrl, alt: "" }) : h("div", { class: "card-sub" }, "Starting…"),
      )
    },
  }

  // --- the Timeline ---------------------------------------------------------------------
  class Timeline {
    constructor({ scroller, inner, jump }) {
      this.scroller = scroller
      this.inner = inner
      this.jump = jump
      this.nodes = new Map()
      this.pending = new Set()
      this.frame = 0
      this.stick = true
      this.thread = null
      this.liveNode = h("div", { class: "live-line is-hidden" })
      this.liveParts = null
      scroller.addEventListener("scroll", () => {
        this.stick = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < STICK_DISTANCE
        jump.classList.toggle("is-hidden", this.stick)
      })
      jump.addEventListener("click", () => this.scrollToEnd(true))
    }

    attach(thread) {
      this.thread = thread
      thread.onChange = (item, mode, before) => this.change(item, mode, before)
    }

    clear() {
      this.nodes.clear()
      this.pending.clear()
      this.inner.replaceChildren(this.liveNode)
      this.stick = true
    }

    renderAll(scroll = true) {
      const keep = this.scroller.scrollTop
      this.clear()
      const fragment = document.createDocumentFragment()
      for (const item of this.thread.items) {
        const node = this.build(item)
        if (!node) continue
        this.nodes.set(item.id, node)
        fragment.append(node)
      }
      this.inner.insertBefore(fragment, this.liveNode)
      if (scroll) this.scrollToEnd(true)
      else this.scroller.scrollTop = keep
    }

    build(item) {
      const render = RENDERERS[item.kind]
      if (!render) return null
      const node = render(item, {
        rerender: (target) => this.schedule(target),
        thread: this.thread,
        toggleFold: (turn) => { this.thread.toggleFold(turn); this.renderAll(false) },
      })
      node.dataset.itemId = String(item.id)
      node.classList.toggle("is-folded", this.thread.isFolded(item))
      return node
    }

    change(item, mode, before) {
      if (mode === "status" || mode === "stats") return
      if (mode === "refold") {
        this.renderAll(false)
        return
      }
      if (mode === "remove") {
        this.nodes.get(item.id)?.remove()
        this.nodes.delete(item.id)
        return
      }
      if (mode === "add" || mode === "insert") {
        const node = this.build(item)
        if (!node) return
        this.nodes.set(item.id, node)
        const anchor = mode === "insert" ? this.nodes.get(before?.id) : null
        this.inner.insertBefore(node, anchor || this.liveNode)
        this.afterChange()
        return
      }
      this.schedule(item)
    }

    schedule(item) {
      this.pending.add(item)
      if (this.frame) return
      this.frame = root.requestAnimationFrame(() => this.flush())
    }

    flush() {
      this.frame = 0
      const items = [...this.pending]
      this.pending.clear()
      for (const item of items) {
        const old = this.nodes.get(item.id)
        if (!old || !old.isConnected) continue
        const node = this.build(item)
        if (!node) continue
        old.replaceWith(node)
        this.nodes.set(item.id, node)
      }
      this.afterChange()
    }

    afterChange() {
      if (this.stick) this.scrollToEnd(false)
    }

    scrollToEnd(force) {
      if (force) this.stick = true
      root.requestAnimationFrame(() => {
        this.scroller.scrollTop = this.scroller.scrollHeight
        this.jump.classList.add("is-hidden")
      })
    }

    // The tail line: what the agent is doing right now. Its nodes are built once
    // per run and only their text changes, so a click on a button is never lost
    // to the once-a-second refresh.
    setLive({ running, text, elapsed, actions }) {
      this.liveNode.classList.toggle("is-hidden", !running)
      if (!running) {
        this.liveParts = null
        this.liveNode.replaceChildren()
        return
      }
      if (!this.liveParts) {
        this.liveParts = { text: h("span", { class: "live-text" }), time: h("span", { class: "live-time" }) }
        this.liveNode.replaceChildren(...[h("span", { class: "spinner" }), this.liveParts.text, this.liveParts.time, actions].filter(Boolean))
      }
      this.liveParts.text.textContent = text
      this.liveParts.time.textContent = elapsed || ""
      this.afterChange()
    }
  }

  K.Timeline = Timeline
  K.describeCall = describe
})(typeof window !== "undefined" ? window : globalThis)
