import { Box, Text } from "ink"
import type { ReactNode } from "react"

import { replaceMath } from "../math.js"
import { color, currentSkin } from "./theme.js"

const INLINE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\[[^\]\n]+\]\([^)\s]+\))|(\*[^*\s][^*\n]*\*)/g

export function renderInline(text: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let last = 0
  let key = 0
  for (const match of text.matchAll(INLINE)) {
    const index = match.index ?? 0
    if (index > last) nodes.push(text.slice(last, index))
    const token = match[0]
    if (match[1]) nodes.push(<Text key={key++} color={color.code}>{token.slice(1, -1)}</Text>)
    else if (match[2]) nodes.push(<Text key={key++} bold>{token.slice(2, -2)}</Text>)
    else if (match[3]) {
      const [, label, url] = /\[([^\]]+)\]\(([^)]+)\)/.exec(token) ?? []
      nodes.push(<Text key={key++}>{label}<Text color={color.dim}> ({url})</Text></Text>)
    } else nodes.push(<Text key={key++} italic>{token.slice(1, -1)}</Text>)
    last = index + token.length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

export type Block =
  | { kind: "code"; lang: string; lines: string[] }
  | { kind: "heading"; level: number; text: string }
  | { kind: "bullet"; marker: string; text: string; indent: number }
  | { kind: "quote"; text: string }
  | { kind: "rule" }
  | { kind: "para"; text: string }
  | { kind: "blank" }

export function parseBlocks(source: string): Block[] {
  const blocks: Block[] = []
  const lines = replaceMath(source.replace(/\r\n/g, "\n")).split("\n")
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? ""
    const fence = /^\s*```(\S*)/.exec(line)
    if (fence) {
      const code: string[] = []
      for (i++; i < lines.length && !/^\s*```/.test(lines[i] ?? ""); i++) code.push(lines[i] ?? "")
      blocks.push({ kind: "code", lang: fence[1] ?? "", lines: code })
      continue
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    const bullet = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/.exec(line)
    if (heading) blocks.push({ kind: "heading", level: (heading[1] ?? "#").length, text: heading[2] ?? "" })
    else if (bullet) blocks.push({ kind: "bullet", marker: /\d/.test(bullet[2] ?? "") ? (bullet[2] ?? "") : "•", text: bullet[3] ?? "", indent: Math.floor((bullet[1] ?? "").length / 2) })
    else if (/^>\s?/.test(line)) blocks.push({ kind: "quote", text: line.replace(/^>\s?/, "") })
    else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) blocks.push({ kind: "rule" })
    else if (!line.trim()) blocks.push({ kind: "blank" })
    else blocks.push({ kind: "para", text: line })
  }
  return collapseBlanks(blocks)
}

function collapseBlanks(blocks: Block[]): Block[] {
  const out: Block[] = []
  for (const block of blocks) {
    const prev = out.at(-1)
    if (block.kind === "blank" && (!prev || prev.kind === "blank")) continue
    out.push(block)
  }
  if (out.at(-1)?.kind === "blank") out.pop()
  return out
}

function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case "code":
      // Claude Code sets a code block off by colour alone, with no frame or language label.
      if (currentSkin() === "claude") {
        return (
          <Box flexDirection="column" paddingLeft={2}>
            {block.lines.map((line, i) => <Text key={i} color={color.code}>{line || " "}</Text>)}
          </Box>
        )
      }
      return (
        <Box flexDirection="column" borderStyle="round" borderColor={color.faint} paddingX={1}>
          {block.lang ? <Text color={color.dim}>{block.lang}</Text> : null}
          {block.lines.map((line, i) => <Text key={i} color={color.code}>{line || " "}</Text>)}
        </Box>
      )
    case "heading":
      return <Text bold color={block.level <= 2 ? color.accent : undefined}>{renderInline(block.text)}</Text>
    case "bullet":
      return (
        <Box paddingLeft={block.indent * 2}>
          <Text color={color.dim}>{block.marker} </Text>
          <Box flexShrink={1}><Text>{renderInline(block.text)}</Text></Box>
        </Box>
      )
    case "quote":
      return <Text color={color.dim}>│ {renderInline(block.text)}</Text>
    case "rule":
      return <Text color={color.faint}>{"─".repeat(24)}</Text>
    case "blank":
      return <Text> </Text>
    case "para":
      return <Text>{renderInline(block.text)}</Text>
  }
}

export function Markdown({ text }: { text: string }) {
  return (
    <Box flexDirection="column">
      {parseBlocks(text).map((block, i) => <BlockView key={i} block={block} />)}
    </Box>
  )
}
