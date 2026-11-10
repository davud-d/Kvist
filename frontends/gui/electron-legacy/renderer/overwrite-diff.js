// The overwrite approval's unified diff as side-by-side cells: removed lines left, added lines right.
// The local web client keeps the same parser in kvist_web/client/features/format.js.

const MAX_PAIRS = 30

export function parseUnifiedDiff(text) {
  const rows = []
  let oldNo = 0
  let newNo = 0
  let inHunk = false
  for (const line of String(text).replace(/\r\n?/g, "\n").split("\n")) {
    const header = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line)
    if (header) {
      oldNo = Number(header[1])
      newNo = Number(header[2])
      inHunk = true
    } else if (!inHunk) continue
    else if (line.startsWith("+")) rows.push({ kind: "add", text: line.slice(1), oldNo: null, newNo: newNo++ })
    else if (line.startsWith("-")) rows.push({ kind: "del", text: line.slice(1), oldNo: oldNo++, newNo: null })
    else if (line.startsWith(" ")) rows.push({ kind: "ctx", text: line.slice(1), oldNo: oldNo++, newNo: newNo++ })
  }
  return rows
}

export function pairRows(rows) {
  const pairs = []
  for (let i = 0; i < rows.length; ) {
    if (rows[i].kind === "ctx") {
      pairs.push({ left: rows[i], right: rows[i] })
      i++
      continue
    }
    const dels = []
    const adds = []
    for (; i < rows.length && rows[i].kind !== "ctx"; i++) (rows[i].kind === "del" ? dels : adds).push(rows[i])
    for (let k = 0; k < Math.max(dels.length, adds.length); k++) pairs.push({ left: dels[k] ?? null, right: adds[k] ?? null })
  }
  return pairs
}

/** Element for `diff`, or null when it has no hunks. Text goes in through textContent. */
export function renderOverwriteDiff(diff, createElement) {
  const rows = parseUnifiedDiff(diff)
  if (rows.length === 0) return null
  const pairs = pairRows(rows)
  const grid = createElement("div", "diff-split")
  const cell = (row, side) => {
    const kind = !row ? "empty" : row.kind === "del" ? "remove" : row.kind === "add" ? "add" : "ctx"
    const node = createElement("div", `diff-cell ${kind}`)
    if (row) {
      const no = side === "left" ? row.oldNo : row.newNo
      node.append(createElement("span", "diff-no", no === null ? "" : String(no)), createElement("span", "diff-text", row.text || " "))
    }
    return node
  }
  for (const pair of pairs.slice(0, MAX_PAIRS)) grid.append(cell(pair.left, "left"), cell(pair.right, "right"))
  if (pairs.length > MAX_PAIRS) grid.append(createElement("div", "diff-more", `… ${pairs.length - MAX_PAIRS} more lines`))
  return grid
}
