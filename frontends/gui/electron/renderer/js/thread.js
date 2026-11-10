"use strict"
// The thread model: turns protocol events (live, or replayed from a session's
// journal) into the items the timeline draws. No DOM here, so it runs in node.

;(function defineThread(root) {
  const K = (root.K = root.K || {})

  const EDIT_ACTIONS = new Set(["write_file", "append_file", "replace_in_file"])
  // Calls that end the turn with words for the person; the words arrive as events.
  const SPEECH_ACTIONS = new Set(["final_answer", "report_to_user", "refuse", "end_session"])
  const SHELL_ACTIONS = new Set(["run_shell"])

  class Thread {
    constructor() {
      this.onChange = null
      this.reset()
    }

    reset() {
      this.items = []
      this.nextId = 1
      this.turn = null
      this.turnCount = 0
      this.calls = new Map() // call_id -> call
      this.callsByStep = new Map()
      this.jobs = new Map()
      this.helpers = new Map()
      this.status = { text: "", error: false }
      this.taskState = "idle"
      this.closedNotice = ""
      this.role = ""
      this.stats = this.freshStats()
      this.silent = false
    }

    freshStats() {
      return { steps: 0, tokensIn: 0, tokensOut: 0, contextTokens: 0, contextLimit: 0, costUsd: 0, costKnown: true, reasoningSeconds: 0 }
    }

    // --- item plumbing ------------------------------------------------------------
    emit(item, mode = "update", before = null) {
      if (!this.silent && this.onChange) this.onChange(item, mode, before)
    }

    push(item) {
      item.id = this.nextId
      this.nextId += 1
      this.items.push(item)
      this.emit(item, "add")
      return item
    }

    insertBefore(item, before) {
      const index = before ? this.items.indexOf(before) : -1
      if (index === -1) return this.push(item)
      item.id = this.nextId
      this.nextId += 1
      this.items.splice(index, 0, item)
      this.emit(item, "insert", before)
      return item
    }

    remove(item) {
      const index = this.items.indexOf(item)
      if (index === -1) return
      this.items.splice(index, 1)
      this.emit(item, "remove")
    }

    last() {
      return this.items.at(-1) || null
    }

    find(predicate) {
      for (let index = this.items.length - 1; index >= 0; index -= 1) {
        if (predicate(this.items[index])) return this.items[index]
      }
      return null
    }

    // --- turns ---------------------------------------------------------------------
    beginTurn(ts) {
      this.turnCount += 1
      this.turn = { start: ts ?? null, end: null, ended: false, firstIndex: this.items.length, calls: 0, group: null }
    }

    ensureTurn(ts) {
      if (!this.turn || this.turn.ended) this.beginTurn(ts)
      return this.turn
    }

    addUser(text, { ts = null, resumed = false, woken = false, images = 0 } = {}) {
      this.beginTurn(ts)
      this.turn.firstIndex = this.items.length
      return this.push({ kind: "user", text: String(text || ""), resumed, woken, images })
    }

    endTurn(ts) {
      const turn = this.turn
      if (!turn || turn.ended) return
      turn.ended = true
      turn.end = ts ?? null
      for (const call of this.calls.values()) {
        if (call.state === "running" || call.state === "writing") {
          call.state = "stopped"
          this.emit(call.group, "update")
        }
      }
      for (const item of this.items) {
        if (item.kind === "thinking" && !item.done) {
          item.done = true
          this.emit(item)
        }
        if (item.kind === "assistant" && item.streaming) {
          item.streaming = false
          this.emit(item)
        }
      }
      this.closeGroups()
      const seconds = turn.start !== null && turn.end !== null ? turn.end - turn.start : this.liveSeconds(turn)
      if (turn.calls > 0 && seconds >= 1) {
        const summary = { kind: "summary", seconds, calls: turn.calls }
        const lastAnswer = this.find((item) => item.kind === "assistant" && this.items.indexOf(item) >= turn.firstIndex)
        if (lastAnswer && this.last() === lastAnswer) this.insertBefore(summary, lastAnswer)
        else this.push(summary)
      }
    }

    liveSeconds(turn) {
      return turn.liveStart ? (Date.now() - turn.liveStart) / 1000 : 0
    }

    markLiveStart() {
      if (this.turn && !this.turn.liveStart) this.turn.liveStart = Date.now()
    }

    closeGroups() {
      for (const item of this.items) {
        if (item.kind === "work" && !item.closed) {
          item.closed = true
          this.emit(item)
        }
      }
    }

    // --- the work group -------------------------------------------------------------
    groupFor(batchId, ts) {
      const turn = this.ensureTurn(ts)
      const last = this.last()
      if (last && last.kind === "work" && !last.closed && (!batchId || !last.batchId || last.batchId === batchId)) {
        if (batchId) last.batchId = batchId
        return last
      }
      const group = this.push({ kind: "work", batchId: batchId || "", calls: [], closed: false })
      turn.group = group
      return group
    }

    newCall(group, fields) {
      const call = {
        callId: "", step: 0, type: "", args: {}, description: "", state: "running", summary: "", output: "", live: "",
        exitCode: null, path: "", patch: "", added: null, removed: null, writing: {}, group,
        ...fields,
      }
      group.calls.push(call)
      if (call.callId) this.calls.set(call.callId, call)
      if (call.step) this.callsByStep.set(call.step, call)
      return call
    }

    findCall(event) {
      if (event.call_id && this.calls.has(event.call_id)) return this.calls.get(event.call_id)
      return this.callsByStep.get(event.step) || null
    }

    // --- events ---------------------------------------------------------------------
    apply(event, ts = null) {
      const handler = HANDLERS[event.type]
      if (handler) handler.call(this, event, ts)
    }

    applyAll(events) {
      const wasSilent = this.silent
      this.silent = true
      for (const record of events) this.apply(record.event, record.ts ?? null)
      this.silent = wasSilent
    }

    // Everything the review panel needs, aggregated over the thread's edits.
    changes() {
      const files = new Map()
      for (const item of this.items) {
        if (item.kind !== "work") continue
        for (const call of item.calls) {
          if (!EDIT_ACTIONS.has(call.type) || call.state !== "ok" || !call.patch) continue
          const entry = files.get(call.path) || { path: call.path, patches: [], added: 0, removed: 0 }
          entry.patches.push(call.patch)
          entry.added += call.added || 0
          entry.removed += call.removed || 0
          files.set(call.path, entry)
        }
      }
      return [...files.values()]
    }

    shellCalls() {
      const result = []
      for (const item of this.items) {
        if (item.kind !== "work") continue
        for (const call of item.calls) if (SHELL_ACTIONS.has(call.type)) result.push(call)
      }
      return result
    }

    activeJobs() {
      return [...this.jobs.values()]
    }
  }

  function lastAnswer(thread) {
    const turn = thread.turn
    const start = turn ? turn.firstIndex : 0
    for (let index = thread.items.length - 1; index >= start; index -= 1) {
      if (thread.items[index].kind === "assistant") return thread.items[index]
    }
    return null
  }

  function lastAnswerText(thread) {
    return lastAnswer(thread)?.text.trim() || ""
  }

  function addAnswer(thread, text, extra = {}) {
    const clean = String(text || "")
    if (!clean.trim()) return null
    thread.closeGroups()
    return thread.push({ kind: "assistant", text: clean, streaming: false, streamId: "", ...extra })
  }

  const HANDLERS = {
    user(event, ts) {
      this.beginTurn(ts)
      this.turn.firstIndex = this.items.length
      if (event.woken) this.push({ kind: "notice", level: "info", text: K.t("Background work finished; Kvist continued on its own.", "Фоновая работа закончилась; Kvist продолжил сам.") })
      else this.push({ kind: "user", text: event.text || "", resumed: Boolean(event.resumed), woken: false, images: 0 })
    },

    route(event) {
      this.role = event.role || ""
    },

    status(event) {
      this.status = { text: event.user_message || event.status || "", error: Boolean(event.is_error) }
      this.emit({ kind: "status-line" }, "status")
    },

    live_start(_event, ts) {
      this.ensureTurn(ts)
      this.markLiveStart()
    },

    task_state(event) {
      this.taskState = event.state || "idle"
      this.emit({ kind: "status-line" }, "status")
    },

    plan(event) {
      this.closeGroups()
      this.push({ kind: "plan", markdown: event.markdown || "" })
    },

    sandbox_notice(event) {
      this.push({ kind: "notice", level: "info", text: event.note || "" })
    },

    notice(event) {
      this.push({ kind: "notice", level: event.level || "info", text: event.text || "" })
    },

    error(event, ts) {
      this.push({ kind: "error", text: event.message || K.t("Kvist reported an error.", "Kvist сообщил об ошибке.") })
      this.endTurn(ts)
    },

    stream_start() {},

    stream_chunk(event) {
      updateStream(this, event, false)
    },

    stream_commit(event, ts) {
      updateStream(this, event, true, ts)
    },

    reasoning_start(event, ts) {
      this.ensureTurn(ts)
      const existing = this.find((item) => item.kind === "thinking" && item.streamId === event.stream_id)
      if (existing) return
      this.closeGroups()
      this.push({ kind: "thinking", streamId: event.stream_id, text: "", seconds: 0, done: false })
    },

    reasoning_chunk(event) {
      const item = this.find((entry) => entry.kind === "thinking" && entry.streamId === event.stream_id)
        || this.push({ kind: "thinking", streamId: event.stream_id, text: "", seconds: 0, done: false })
      item.text = event.text || ""
      this.emit(item)
    },

    reasoning_commit(event) {
      const seconds = Number(event.duration_seconds) || 0
      let item = this.find((entry) => entry.kind === "thinking" && entry.streamId === event.stream_id)
      if (!item) item = this.push({ kind: "thinking", streamId: event.stream_id, text: "", seconds: 0, done: false })
      item.done = true
      item.seconds = seconds
      this.stats.reasoningSeconds += seconds
      this.emit(item)
    },

    message(event) {
      if (event.source === "notice") {
        this.push({ kind: "notice", level: "info", text: event.markdown || "" })
        return
      }
      if (String(event.markdown || "").trim() === lastAnswerText(this)) return
      addAnswer(this, event.markdown, { skill: event.source === "skill" })
    },

    final(event, ts) {
      const message = String(event.message || "").trim()
      const shown = lastAnswer(this)
      // The runtime may append a line to the answer that already streamed (a
      // clean-up note); that is the same message, not a second one.
      if (shown && message.startsWith(shown.text.trim())) {
        if (message !== shown.text.trim()) {
          shown.text = message
          this.emit(shown)
        }
      } else if (message && message !== lastAnswerText(this)) {
        addAnswer(this, event.message)
      }
      if (event.closed) this.closedNotice = this.closedNotice || " "
      else if (event.paused) this.push({ kind: "status", tone: "muted", text: K.t("Paused. Write a correction or “continue”.", "Пауза. Напиши поправку или «продолжай».") })
      else if (event.outcome === "incomplete") this.push({ kind: "status", tone: "warn", text: K.t("Finished, but not done.", "Закончил, но не доделал.") })
      this.endTurn(ts)
    },

    turn_end(_event, ts) {
      this.endTurn(ts)
    },

    session_ended(event) {
      this.closedNotice = event.closed_notice || " "
    },

    step(event, ts) {
      const turn = this.ensureTurn(ts)
      this.stats.steps = Math.max(this.stats.steps, Number(event.step) || 0)
      if (SPEECH_ACTIONS.has(event.action_type)) {
        if (event.user_message && event.user_message.trim() !== lastAnswerText(this)) addAnswer(this, event.user_message)
        return
      }
      const existing = this.findCall(event)
      let call = existing && existing.state === "writing" ? existing : null
      if (event.user_message && String(event.user_message).trim() && !call) {
        if (event.user_message.trim() !== lastAnswerText(this)) addAnswer(this, event.user_message)
      }
      const group = call ? call.group : this.groupFor(event.batch_id, ts)
      const fields = {
        callId: event.call_id || "",
        step: Number(event.step) || 0,
        type: event.action_type || "",
        args: event.action_args || {},
        description: event.action_description || "",
        state: "running",
      }
      if (call) {
        Object.assign(call, fields)
        if (call.callId) this.calls.set(call.callId, call)
        if (call.step) this.callsByStep.set(call.step, call)
      } else {
        call = this.newCall(group, fields)
        turn.calls += 1
      }
      this.emit(group)
    },

    call_begin(event, ts) {
      const turn = this.ensureTurn(ts)
      if (this.calls.has(event.call_id)) return
      const group = this.groupFor("", ts)
      this.newCall(group, {
        callId: event.call_id,
        type: event.tool || "",
        description: event.description || "",
        path: event.path || "",
        state: "writing",
      })
      turn.calls += 1
      this.emit(group)
    },

    call_stream(event) {
      const call = this.calls.get(event.call_id)
      if (!call) return
      const field = event.field || "content"
      const current = call.writing[field] || ""
      // A delta at line 0 restarts the field only once text has passed line 0.
      const restart = Number(event.line) === 0 && current.includes("\n")
      call.writing[field] = (restart ? "" : current) + String(event.text || "")
      if (event.path) call.path = event.path
      this.emit(call.group)
    },

    action_output(event) {
      const call = this.findCall(event)
      if (!call) return
      call.live = event.output || ""
      this.emit(call.group)
    },

    action_result(event) {
      const call = this.findCall(event)
      if (!call) return
      call.state = event.ok ? "ok" : "failed"
      call.summary = event.summary || ""
      call.output = event.output || ""
      call.live = ""
      call.exitCode = event.exit_code ?? null
      if (event.path) call.path = event.path
      call.patch = event.patch || ""
      call.added = event.lines_added ?? null
      call.removed = event.lines_removed ?? null
      this.emit(call.group)
    },

    job_started(event, ts) {
      this.ensureTurn(ts)
      this.closeGroups()
      const job = { kind: "job", jobId: event.job_id, command: event.command || [], purpose: event.purpose || "", status: "running", output: "", exitCode: null }
      this.jobs.set(job.jobId, job)
      this.push(job)
    },

    job_output(event) {
      const job = this.jobs.get(event.job_id)
      if (!job) return
      job.output = event.output || ""
      this.emit(job)
    },

    job_finished(event) {
      let job = this.jobs.get(event.job_id) || this.find((item) => item.kind === "job" && item.jobId === event.job_id)
      if (!job) job = this.push({ kind: "job", jobId: event.job_id, command: [], purpose: "", status: "", output: "", exitCode: null })
      job.status = event.status || "exited"
      job.exitCode = event.exit_code ?? null
      job.output = event.output || job.output
      this.jobs.delete(event.job_id)
      this.emit(job)
    },

    skill(event) {
      const verb = event.state === "detached" ? K.t("detached", "отключён") : K.t("attached", "подключён")
      this.push({ kind: "notice", level: "info", text: K.t(`Skill “${event.name || event.skill}” ${verb}.`, `Навык «${event.name || event.skill}» ${verb}.`) })
    },

    helper(event) {
      let item = this.helpers.get(event.agent_id)
      if (!item) {
        item = this.push({ kind: "helper", agentId: event.agent_id, state: "", task: "", summary: "", session: "", activity: "" })
        this.helpers.set(event.agent_id, item)
      }
      item.state = event.state || item.state
      item.task = event.task || item.task
      item.summary = event.summary || item.summary
      item.session = event.session || item.session
      this.emit(item)
    },

    helper_activity(event) {
      const item = this.helpers.get(event.agent_id)
      if (!item) return
      if (event.phase === "step") item.activity = event.label || ""
      this.emit(item)
    },

    artifact(event) {
      this.closeGroups()
      this.push({ kind: "artifact", name: event.name || "", mime: event.mime || "", path: event.path || "", caption: event.caption || "", skill: event.skill || "", preview: event.preview || "" })
    },

    image_job(event) {
      let item = this.find((entry) => entry.kind === "image" && entry.imageId === event.image_id)
      if (!item) item = this.push({ kind: "image", imageId: event.image_id, state: "", name: "", prompt: "", error: "" })
      Object.assign(item, { state: event.state || "", name: event.name || item.name, prompt: event.prompt || item.prompt, error: event.error || "" })
      this.emit(item)
    },

    screen(event) {
      let item = this.find((entry) => entry.kind === "screen" && entry.screenId === event.screen_id)
      if (!item) item = this.push({ kind: "screen", screenId: event.screen_id, title: "", state: "", url: "", streamUrl: "" })
      Object.assign(item, { title: event.title || event.skill || "", state: event.state || "", url: event.url || "", streamUrl: event.stream_url || "" })
      this.emit(item)
    },

    usage(event) {
      this.stats.tokensIn += Number(event.input_tokens) || 0
      this.stats.tokensOut += Number(event.output_tokens) || 0
      this.stats.contextTokens = Number(event.context_tokens) || this.stats.contextTokens
      this.stats.contextLimit = Number(event.context_limit) || this.stats.contextLimit
      if (typeof event.cost_usd === "number") this.stats.costUsd += event.cost_usd
      else this.stats.costKnown = false
      this.emit({ kind: "stats" }, "stats")
    },

    context(event) {
      this.stats.contextTokens = Number(event.context_tokens) || 0
      this.stats.contextLimit = Number(event.context_limit) || this.stats.contextLimit
      this.emit({ kind: "stats" }, "stats")
    },
  }

  function updateStream(thread, event, committed, ts) {
    const text = String(event.text || "")
    let item = thread.find((entry) => entry.kind === "assistant" && entry.streamId === event.stream_id)
    if (!item) {
      // Empty text: nothing to draw yet (and an empty commit is end_session's hidden tag).
      if (!text.trim()) return
      thread.ensureTurn(ts)
      thread.closeGroups()
      item = thread.push({ kind: "assistant", text, streaming: !committed, streamId: event.stream_id })
      return
    }
    item.text = text
    item.streaming = !committed
    if (committed && !text.trim()) thread.remove(item)
    else thread.emit(item)
  }

  K.Thread = Thread
  K.EDIT_ACTIONS = EDIT_ACTIONS

  if (typeof module === "object" && module.exports) module.exports = { Thread, EDIT_ACTIONS }
})(typeof window !== "undefined" ? window : globalThis)
