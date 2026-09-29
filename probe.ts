// Asks the terminal what it can draw, once, before Ink takes over the input. The queries are
// standard: `CSI c` (primary device attributes; a terminal that speaks Sixel lists attribute 4)
// and the window-size reports of XTWINOPS, from which the size of a cell in pixels follows. Every
// terminal answers `CSI c`, and it is sent last, so its reply tells that the others were answered
// or are not going to be; a terminal that says nothing at all is given up on after `timeoutMs`.
export interface Probe {
  sixel: boolean
  // Size of one cell in pixels, when the terminal said.
  cell: { width: number; height: number } | null
}

const QUERY = "\x1b[16t\x1b[14t\x1b[18t\x1b[c"
const ATTRIBUTES = /\x1b\[\?([\d;]*)c/
const CELL_SIZE = /\x1b\[6;(\d+);(\d+)t/
const WINDOW_PIXELS = /\x1b\[4;(\d+);(\d+)t/
const WINDOW_CELLS = /\x1b\[8;(\d+);(\d+)t/

export function parseProbe(reply: string): Probe {
  const sixel = (ATTRIBUTES.exec(reply)?.[1] ?? "").split(";").includes("4")
  const direct = CELL_SIZE.exec(reply)
  if (direct) return { sixel, cell: { height: Number(direct[1]), width: Number(direct[2]) } }
  const pixels = WINDOW_PIXELS.exec(reply)
  const cells = WINDOW_CELLS.exec(reply)
  if (pixels && cells && Number(cells[1]) > 0 && Number(cells[2]) > 0) {
    return { sixel, cell: { height: Math.floor(Number(pixels[1]) / Number(cells[1])), width: Math.floor(Number(pixels[2]) / Number(cells[2])) } }
  }
  return { sixel, cell: null }
}

export function probeTerminal(stdin: NodeJS.ReadStream, stdout: NodeJS.WriteStream, timeoutMs = 300): Promise<Probe> {
  if (!stdin.isTTY || !stdout.isTTY) return Promise.resolve({ sixel: false, cell: null })
  return new Promise((resolve) => {
    let reply = ""
    const finish = () => {
      clearTimeout(timer)
      stdin.off("data", onData)
      stdin.pause()
      stdin.setRawMode(false)
      resolve(parseProbe(reply))
    }
    const onData = (chunk: Buffer | string) => {
      reply += chunk.toString()
      if (ATTRIBUTES.test(reply)) finish()
    }
    const timer = setTimeout(finish, timeoutMs)
    stdin.setRawMode(true)
    stdin.resume()
    stdin.on("data", onData)
    stdout.write(QUERY)
  })
}
