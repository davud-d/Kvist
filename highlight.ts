// Syntax highlighting for the diff and file previews. highlight.js does the
// tokenizing; this turns its HTML into rows of coloured segments, which is what a
// terminal row is made of. Only the languages listed here are bundled.
import hljs from "highlight.js/lib/core"
import bash from "highlight.js/lib/languages/bash"
import c from "highlight.js/lib/languages/c"
import cpp from "highlight.js/lib/languages/cpp"
import css from "highlight.js/lib/languages/css"
import go from "highlight.js/lib/languages/go"
import ini from "highlight.js/lib/languages/ini"
import java from "highlight.js/lib/languages/java"
import javascript from "highlight.js/lib/languages/javascript"
import json from "highlight.js/lib/languages/json"
import kotlin from "highlight.js/lib/languages/kotlin"
import markdown from "highlight.js/lib/languages/markdown"
import python from "highlight.js/lib/languages/python"
import rust from "highlight.js/lib/languages/rust"
import sql from "highlight.js/lib/languages/sql"
import typescript from "highlight.js/lib/languages/typescript"
import xml from "highlight.js/lib/languages/xml"
import yaml from "highlight.js/lib/languages/yaml"

const REGISTERED = { bash, c, cpp, css, go, ini, java, javascript, json, kotlin, markdown, python, rust, sql, typescript, xml, yaml }
for (const [name, language] of Object.entries(REGISTERED)) hljs.registerLanguage(name, language)

const BY_EXTENSION: Record<string, keyof typeof REGISTERED> = {
  ts: "typescript", tsx: "typescript", mts: "typescript", cts: "typescript",
  js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
  py: "python", json: "json", sh: "bash", bash: "bash", zsh: "bash", css: "css",
  html: "xml", htm: "xml", xml: "xml", svg: "xml", md: "markdown", yml: "yaml", yaml: "yaml",
  go: "go", rs: "rust", java: "java", kt: "kotlin", kts: "kotlin",
  c: "c", h: "c", cpp: "cpp", cc: "cpp", hpp: "cpp", sql: "sql", toml: "ini", ini: "ini", cfg: "ini",
}

export interface Segment {
  text: string
  // The highlight.js class of the token, without its "hljs-" prefix ("keyword", "title.function_"), or null for plain text.
  scope: string | null
}

export function languageOf(path: string): string | null {
  const extension = /\.([A-Za-z0-9]+)$/.exec(path)?.[1]?.toLowerCase()
  return (extension && BY_EXTENSION[extension]) || null
}

const ENTITIES: Record<string, string> = { "&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"', "&#x27;": "'" }
const TOKEN = /<span class="([^"]*)">|<\/span>|([^<]+)/g

// `code` as rows of segments. An unknown language, or a failure inside the highlighter, gives plain rows.
export function highlightLines(code: string, language: string | null): Segment[][] {
  const plain = () => code.split("\n").map((line) => [{ text: line, scope: null }])
  if (!language) return plain()
  let html: string
  try {
    html = hljs.highlight(code, { language, ignoreIllegals: true }).value
  } catch {
    // highlight.js throws for a language that is not registered; plain rows are the right answer then.
    return plain()
  }
  const rows: Segment[][] = [[]]
  const open: (string | null)[] = []
  for (const match of html.matchAll(TOKEN)) {
    if (match[1] !== undefined) {
      open.push(match[1].replace(/hljs-/g, "").trim().replace(/\s+/g, ".") || null)
      continue
    }
    if (match[2] === undefined) {
      open.pop()
      continue
    }
    const text = match[2].replace(/&(?:lt|gt|amp|quot|#x27);/g, (entity) => ENTITIES[entity] ?? entity)
    const scope = open.at(-1) ?? null
    text.split("\n").forEach((part, index) => {
      if (index > 0) rows.push([])
      if (part) rows.at(-1)!.push({ text: part, scope })
    })
  }
  return rows
}

// Exactly `width` cells of the row: cut with an ellipsis when too long, padded with spaces when short, so
// a background painted behind it reaches the edge. Counted in code points, like format.fit.
export function fitSegments(segments: readonly Segment[], width: number): Segment[] {
  if (width <= 0) return []
  const out: Segment[] = []
  let used = 0
  const chars = segments.flatMap((segment) => [...segment.text.replace(/\t/g, "    ")].map((text) => ({ text, scope: segment.scope })))
  const cut = chars.length > width
  for (const char of cut ? chars.slice(0, width - 1) : chars) {
    const last = out.at(-1)
    if (last && last.scope === char.scope) last.text += char.text
    else out.push({ text: char.text, scope: char.scope })
    used++
  }
  if (cut) out.push({ text: "…", scope: null })
  else if (used < width) out.push({ text: " ".repeat(width - used), scope: null })
  return out
}
