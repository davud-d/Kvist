/**
 * vt.mjs — the subset of xterm that a Kvist recording actually uses.
 *
 * Kvist draws through prompt_toolkit (absolute cursor moves, SGR, erase) and
 * through rich, which opens the alternate screen for approval panels. That is
 * the whole surface: no mouse, no scrollback queries, no character sets. What
 * is implemented here is what appears in the casts, nothing more.
 *
 * Colours are never kept as raw ANSI. Every colour — indexed, 256-colour or
 * truecolour — is resolved to one of 16 palette slots, so the page paints the
 * replay from --ansi-0..15 and repainting tokens.css repaints the terminal.
 */

export const DEFAULT_FG = -1;
export const DEFAULT_BG = -2;

const XTERM_256 = buildXterm256();

export class Screen {
  constructor(cols, rows, palette) {
    this.cols = cols;
    this.rows = rows;
    this.palette = palette;            // 16 [r,g,b] entries, from tokens.css
    this.main = blank(cols, rows);
    this.alt = blank(cols, rows);
    this.grid = this.main;
    this.altActive = false;
    this.x = 0;
    this.y = 0;
    this.saved = null;
    this.fg = DEFAULT_FG;
    this.bg = DEFAULT_BG;
    this.bold = false;
    this.inverse = false;
    this.top = 0;
    this.bottom = rows - 1;
    this.wrapPending = false;
    this.parser = { state: "text", buffer: "" };
  }

  /* -- attributes -------------------------------------------------------- */

  cell(ch) {
    const fg = this.inverse ? this.bg : this.fg;
    const bg = this.inverse ? this.fg : this.bg;
    return { ch, fg, bg, bold: this.bold };
  }

  /* -- writing ----------------------------------------------------------- */

  put(ch) {
    if (this.wrapPending) {
      this.x = 0;
      this.lineFeed();
      this.wrapPending = false;
    }
    if (this.x >= this.cols) return;
    this.grid[this.y][this.x] = this.cell(ch);
    this.x += 1;
    if (this.x >= this.cols) {
      this.x = this.cols - 1;
      this.wrapPending = true;
    }
  }

  lineFeed() {
    if (this.y === this.bottom) this.scrollUp(1);
    else if (this.y < this.rows - 1) this.y += 1;
  }

  scrollUp(n) {
    for (let i = 0; i < n; i += 1) {
      this.grid.splice(this.top, 1);
      this.grid.splice(this.bottom, 0, blankRow(this.cols));
    }
  }

  scrollDown(n) {
    for (let i = 0; i < n; i += 1) {
      this.grid.splice(this.bottom, 1);
      this.grid.splice(this.top, 0, blankRow(this.cols));
    }
  }

  erase(row, from, to) {
    for (let x = from; x <= to && x < this.cols; x += 1) {
      this.grid[row][x] = { ch: " ", fg: DEFAULT_FG, bg: this.bg, bold: false };
    }
  }

  /* -- feeding ----------------------------------------------------------- */

  write(text) {
    for (const ch of text) this.step(ch);
  }

  step(ch) {
    const p = this.parser;
    if (p.state === "esc") return this.escape(ch);
    if (p.state === "csi") return this.csi(ch);
    if (p.state === "osc") return this.osc(ch);
    if (p.state === "charset") { p.state = "text"; return; }

    const code = ch.codePointAt(0);
    if (ch === "\x1b") { p.state = "esc"; p.buffer = ""; return; }
    if (ch === "\r") { this.x = 0; this.wrapPending = false; return; }
    if (ch === "\n") { this.lineFeed(); this.wrapPending = false; return; }
    if (ch === "\b") { this.x = Math.max(0, this.x - 1); this.wrapPending = false; return; }
    if (ch === "\t") { this.x = Math.min(this.cols - 1, (Math.floor(this.x / 8) + 1) * 8); return; }
    if (code < 0x20 || code === 0x7f) return;
    this.put(ch);
  }

  escape(ch) {
    const p = this.parser;
    if (ch === "[") { p.state = "csi"; p.buffer = ""; return; }
    if (ch === "]") { p.state = "osc"; p.buffer = ""; return; }
    if (ch === "(" || ch === ")" || ch === "*" || ch === "+") { p.state = "charset"; return; }
    p.state = "text";
    if (ch === "7") { this.saved = { x: this.x, y: this.y, fg: this.fg, bg: this.bg, bold: this.bold, inverse: this.inverse }; return; }
    if (ch === "8" && this.saved) { Object.assign(this, this.saved); return; }
    if (ch === "M") { if (this.y === this.top) this.scrollDown(1); else this.y = Math.max(0, this.y - 1); return; }
    if (ch === "D") { this.lineFeed(); return; }
    if (ch === "E") { this.x = 0; this.lineFeed(); return; }
    if (ch === "c") { this.reset(); return; }
  }

  osc(ch) {
    const p = this.parser;
    if (ch === "\x07") { p.state = "text"; p.buffer = ""; return; }
    if (ch === "\\" && p.buffer.endsWith("\x1b")) { p.state = "text"; p.buffer = ""; return; }
    p.buffer += ch;
  }

  csi(ch) {
    const p = this.parser;
    const code = ch.codePointAt(0);
    if (code >= 0x40 && code <= 0x7e) {
      p.state = "text";
      this.dispatch(ch, p.buffer);
      p.buffer = "";
      return;
    }
    p.buffer += ch;
  }

  dispatch(final, raw) {
    const priv = raw.startsWith("?");
    const body = priv ? raw.slice(1) : raw;
    const args = body.split(";").map((s) => (s === "" ? 0 : parseInt(s, 10) || 0));
    const a0 = args[0] ?? 0;

    if (priv) return this.privateMode(final, args);

    switch (final) {
      case "A": this.y = Math.max(this.top, this.y - Math.max(1, a0)); break;
      case "B": this.y = Math.min(this.bottom, this.y + Math.max(1, a0)); break;
      case "C": this.x = Math.min(this.cols - 1, this.x + Math.max(1, a0)); this.wrapPending = false; break;
      case "D": this.x = Math.max(0, this.x - Math.max(1, a0)); this.wrapPending = false; break;
      case "E": this.x = 0; this.y = Math.min(this.bottom, this.y + Math.max(1, a0)); break;
      case "F": this.x = 0; this.y = Math.max(this.top, this.y - Math.max(1, a0)); break;
      case "G": case "`": this.x = clamp(Math.max(1, a0) - 1, 0, this.cols - 1); this.wrapPending = false; break;
      case "d": this.y = clamp(Math.max(1, a0) - 1, 0, this.rows - 1); break;
      case "H": case "f":
        this.y = clamp(Math.max(1, args[0] || 1) - 1, 0, this.rows - 1);
        this.x = clamp(Math.max(1, args[1] || 1) - 1, 0, this.cols - 1);
        this.wrapPending = false;
        break;
      case "J": this.eraseDisplay(a0); break;
      case "K": this.eraseLine(a0); break;
      case "L": for (let i = 0; i < Math.max(1, a0); i += 1) { this.grid.splice(this.bottom, 1); this.grid.splice(this.y, 0, blankRow(this.cols)); } break;
      case "M": for (let i = 0; i < Math.max(1, a0); i += 1) { this.grid.splice(this.y, 1); this.grid.splice(this.bottom, 0, blankRow(this.cols)); } break;
      case "P": {
        const n = Math.max(1, a0);
        const row = this.grid[this.y];
        row.splice(this.x, n);
        while (row.length < this.cols) row.push(emptyCell());
        break;
      }
      case "@": {
        const n = Math.max(1, a0);
        const row = this.grid[this.y];
        for (let i = 0; i < n; i += 1) row.splice(this.x, 0, emptyCell());
        row.length = this.cols;
        break;
      }
      case "X": this.erase(this.y, this.x, this.x + Math.max(1, a0) - 1); break;
      case "S": this.scrollUp(Math.max(1, a0)); break;
      case "T": this.scrollDown(Math.max(1, a0)); break;
      case "r":
        this.top = clamp((args[0] || 1) - 1, 0, this.rows - 1);
        this.bottom = clamp((args[1] || this.rows) - 1, this.top, this.rows - 1);
        this.x = 0; this.y = this.top;
        break;
      case "s": this.saved = { x: this.x, y: this.y, fg: this.fg, bg: this.bg, bold: this.bold, inverse: this.inverse }; break;
      case "u": if (this.saved) Object.assign(this, this.saved); break;
      case "m": this.sgr(body === "" ? [0] : args); break;
      default: break;
    }
  }

  privateMode(final, args) {
    if (final !== "h" && final !== "l") return;
    const on = final === "h";
    for (const arg of args) {
      if (arg === 1049 || arg === 47 || arg === 1047) this.setAlt(on);
    }
  }

  setAlt(on) {
    if (on === this.altActive) return;
    this.altActive = on;
    if (on) {
      this.alt = blank(this.cols, this.rows);
      this.grid = this.alt;
      this.savedMain = { x: this.x, y: this.y };
      this.x = 0; this.y = 0;
    } else {
      this.grid = this.main;
      if (this.savedMain) { this.x = this.savedMain.x; this.y = this.savedMain.y; }
    }
  }

  eraseDisplay(mode) {
    if (mode === 0) {
      this.erase(this.y, this.x, this.cols - 1);
      for (let r = this.y + 1; r < this.rows; r += 1) this.erase(r, 0, this.cols - 1);
    } else if (mode === 1) {
      this.erase(this.y, 0, this.x);
      for (let r = 0; r < this.y; r += 1) this.erase(r, 0, this.cols - 1);
    } else {
      for (let r = 0; r < this.rows; r += 1) this.erase(r, 0, this.cols - 1);
    }
  }

  eraseLine(mode) {
    if (mode === 0) this.erase(this.y, this.x, this.cols - 1);
    else if (mode === 1) this.erase(this.y, 0, this.x);
    else this.erase(this.y, 0, this.cols - 1);
  }

  sgr(args) {
    for (let i = 0; i < args.length; i += 1) {
      const n = args[i];
      if (n === 0) { this.fg = DEFAULT_FG; this.bg = DEFAULT_BG; this.bold = false; this.inverse = false; }
      else if (n === 1) this.bold = true;
      else if (n === 22) this.bold = false;
      else if (n === 7) this.inverse = true;
      else if (n === 27) this.inverse = false;
      else if (n >= 30 && n <= 37) this.fg = n - 30;
      else if (n === 39) this.fg = DEFAULT_FG;
      else if (n >= 40 && n <= 47) this.bg = n - 40;
      else if (n === 49) this.bg = DEFAULT_BG;
      else if (n >= 90 && n <= 97) this.fg = n - 90 + 8;
      else if (n >= 100 && n <= 107) this.bg = n - 100 + 8;
      else if (n === 38 || n === 48) {
        const target = n === 38 ? "fg" : "bg";
        if (args[i + 1] === 5) { this[target] = this.nearest(XTERM_256[args[i + 2] & 255]); i += 2; }
        else if (args[i + 1] === 2) { this[target] = this.nearest([args[i + 2] | 0, args[i + 3] | 0, args[i + 4] | 0]); i += 4; }
      }
    }
  }

  /** Snap any RGB onto the closest of the 16 token slots. */
  nearest(rgb) {
    if (!rgb) return DEFAULT_FG;
    let best = 0;
    let bestDistance = Infinity;
    for (let i = 0; i < 16; i += 1) {
      const p = this.palette[i];
      // Weighted RGB: cheap, and stable enough for a 16-slot snap.
      const dr = (rgb[0] - p[0]) * 0.30;
      const dg = (rgb[1] - p[1]) * 0.59;
      const db = (rgb[2] - p[2]) * 0.11;
      const distance = dr * dr + dg * dg + db * db;
      if (distance < bestDistance) { bestDistance = distance; best = i; }
    }
    return best;
  }

  reset() {
    this.main = blank(this.cols, this.rows);
    this.alt = blank(this.cols, this.rows);
    this.grid = this.main;
    this.altActive = false;
    this.x = 0; this.y = 0;
    this.fg = DEFAULT_FG; this.bg = DEFAULT_BG;
    this.bold = false; this.inverse = false;
    this.top = 0; this.bottom = this.rows - 1;
  }

  /** Current visible buffer as rows of merged spans. */
  snapshot() {
    return this.grid.map((row) => spans(row));
  }
}

/* -- helpers --------------------------------------------------------------- */

function emptyCell() {
  return { ch: " ", fg: DEFAULT_FG, bg: DEFAULT_BG, bold: false };
}
function blankRow(cols) {
  return Array.from({ length: cols }, emptyCell);
}
function blank(cols, rows) {
  return Array.from({ length: rows }, () => blankRow(cols));
}
function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

/** Merge a row of cells into [text, fg, bg, bold] runs, trailing blanks cut. */
export function spans(row) {
  let end = row.length;
  while (end > 0) {
    const c = row[end - 1];
    if (c.ch !== " " || c.bg !== DEFAULT_BG) break;
    end -= 1;
  }
  const out = [];
  let run = null;
  for (let x = 0; x < end; x += 1) {
    const c = row[x];
    if (run && run[1] === c.fg && run[2] === c.bg && run[3] === (c.bold ? 1 : 0)) {
      run[0] += c.ch;
    } else {
      run = [c.ch, c.fg, c.bg, c.bold ? 1 : 0];
      out.push(run);
    }
  }
  return out;
}

function buildXterm256() {
  const table = new Array(256);
  const base = [
    [0, 0, 0], [205, 0, 0], [0, 205, 0], [205, 205, 0],
    [0, 0, 238], [205, 0, 205], [0, 205, 205], [229, 229, 229],
    [127, 127, 127], [255, 0, 0], [0, 255, 0], [255, 255, 0],
    [92, 92, 255], [255, 0, 255], [0, 255, 255], [255, 255, 255],
  ];
  for (let i = 0; i < 16; i += 1) table[i] = base[i];
  const steps = [0, 95, 135, 175, 215, 255];
  for (let i = 0; i < 216; i += 1) {
    table[16 + i] = [
      steps[Math.floor(i / 36) % 6],
      steps[Math.floor(i / 6) % 6],
      steps[i % 6],
    ];
  }
  for (let i = 0; i < 24; i += 1) {
    const v = 8 + i * 10;
    table[232 + i] = [v, v, v];
  }
  return table;
}
