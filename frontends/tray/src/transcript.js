"use strict"

const { marked } = require("marked")
const sanitizeHtml = require("sanitize-html")

const ALLOWED_TAGS = [
  "p", "br", "strong", "em", "code", "pre", "ul", "ol", "li", "blockquote",
  "h1", "h2", "h3", "h4", "a", "table", "thead", "tbody", "tr", "th", "td", "hr", "del",
]

function renderMarkdown(text) {
  return sanitizeHtml(marked.parse(text, { async: false }), {
    allowedTags: ALLOWED_TAGS,
    allowedAttributes: { a: ["href"] },
    allowedSchemes: ["http", "https", "mailto"],
    transformTags: { a: sanitizeHtml.simpleTransform("a", { rel: "noreferrer" }) },
  })
}

/**
 * Turn `--session-op history` events into display items for the read-only chat.
 *
 * Items: `user` and `error` carry plain `text`; `assistant` carries sanitized
 * `html`; `step` is one muted line. Thinking, usage and status events are not
 * shown. An answer that repeats the previous one (a streamed reply followed by
 * its `final`) is shown once.
 */
function buildTranscript(entries) {
  const items = []
  let lastAnswer = ""
  const answer = (text) => {
    if (!text || text === lastAnswer) return
    lastAnswer = text
    items.push({ kind: "assistant", html: renderMarkdown(text) })
  }
  for (const { event } of entries) {
    switch (event.type) {
      case "user":
        items.push({ kind: "user", text: event.resumed ? "(resumed)" : event.text })
        break
      case "step":
        answer(event.user_message)
        items.push({ kind: "step", text: event.action_description || event.action_type })
        break
      case "action_result":
        if (!event.ok) items.push({ kind: "error", text: event.summary })
        break
      case "stream_commit":
        answer(event.text)
        break
      case "message":
        answer(event.markdown)
        break
      case "plan":
        answer(event.markdown)
        break
      case "final":
        answer(event.message)
        break
      case "error":
        items.push({ kind: "error", text: event.message })
        break
      default:
        break
    }
  }
  return items
}

module.exports = { buildTranscript, renderMarkdown }
