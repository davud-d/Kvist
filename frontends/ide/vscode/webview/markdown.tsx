import MarkdownIt from "markdown-it"
import { useMemo } from "preact/hooks"

import { post } from "./host"

// Raw HTML stays off: the text comes from a model and from files it read.
const renderer = new MarkdownIt({ html: false, linkify: true })

function openLink(event: MouseEvent): void {
  const link = (event.target as HTMLElement).closest("a")
  if (!link) return
  event.preventDefault()
  const url = link.getAttribute("href") ?? ""
  if (/^https?:\/\//i.test(url)) post({ type: "openLink", url })
}

export function Markdown({ text }: { text: string }) {
  const html = useMemo(() => renderer.render(text), [text])
  return <div class="markdown" onClick={openLink} dangerouslySetInnerHTML={{ __html: html }} />
}
