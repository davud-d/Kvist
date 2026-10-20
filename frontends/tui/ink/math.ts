// LaTeX math as plain text for terminals that cannot draw it: `\sqrt{6646021} \approx 2577{,}9878`
// becomes `√6646021 ≈ 2577,9878`. Not a typesetter: nested fractions, matrices and the like come out
// as readable approximations, and a command it does not know is left as written.

const SYMBOLS: Record<string, string> = {
  cdot: "·", times: "×", div: "÷", pm: "±", mp: "∓", approx: "≈", neq: "≠", ne: "≠", equiv: "≡",
  le: "≤", leq: "≤", ge: "≥", geq: "≥", ll: "≪", gg: "≫", infty: "∞", partial: "∂", nabla: "∇",
  to: "→", rightarrow: "→", leftarrow: "←", leftrightarrow: "↔", Rightarrow: "⇒", Leftarrow: "⇐",
  Leftrightarrow: "⇔", implies: "⇒", iff: "⇔", mapsto: "↦", in: "∈", notin: "∉", subset: "⊂",
  subseteq: "⊆", cup: "∪", cap: "∩", forall: "∀", exists: "∃", emptyset: "∅", neg: "¬", land: "∧",
  lor: "∨", sum: "∑", prod: "∏", int: "∫", oint: "∮", circ: "∘", ldots: "…", dots: "…", cdots: "⋯",
  vdots: "⋮", angle: "∠", perp: "⊥", parallel: "∥", propto: "∝", sim: "∼", degree: "°", prime: "′",
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", zeta: "ζ", eta: "η",
  theta: "θ", iota: "ι", kappa: "κ", lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π", rho: "ρ",
  sigma: "σ", tau: "τ", upsilon: "υ", phi: "φ", varphi: "φ", chi: "χ", psi: "ψ", omega: "ω",
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π", Sigma: "Σ", Phi: "Φ", Psi: "Ψ",
  Omega: "Ω", quad: "  ", qquad: "    ", ",": " ", ";": " ", ":": " ", " ": " ", "!": "",
  "\\": "\n", "{": "{", "}": "}", "%": "%", "$": "$", "&": "&", "_": "_", "#": "#",
}
const FUNCTIONS = new Set(["sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "lg", "exp", "lim", "min", "max", "gcd", "det", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh"])
// Commands that only style or size their argument.
const WRAPPERS = new Set(["text", "textbf", "textit", "mathrm", "mathbf", "mathit", "mathbb", "mathcal", "mathsf", "operatorname", "displaystyle", "textstyle", "mbox"])
const DROPPED = new Set(["left", "right", "big", "Big", "bigg", "Bigg", "limits", "nolimits"])

const SUPERSCRIPT: Record<string, string> = {
  "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
  "+": "⁺", "-": "⁻", "−": "⁻", "=": "⁼", "(": "⁽", ")": "⁾", n: "ⁿ", i: "ⁱ",
}
const SUBSCRIPT: Record<string, string> = {
  "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
  "+": "₊", "-": "₋", "−": "₋", "=": "₌", "(": "₍", ")": "₎", a: "ₐ", e: "ₑ", o: "ₒ", x: "ₓ",
  h: "ₕ", k: "ₖ", l: "ₗ", m: "ₘ", n: "ₙ", p: "ₚ", s: "ₛ", t: "ₜ",
}

const COMMAND = /^\\([A-Za-z]+|[^A-Za-z])/

/** The argument that starts at `at`: a braced group, a command or one character. */
function argument(tex: string, at: number): [string, number] {
  let i = at
  while (tex[i] === " ") i++
  if (tex[i] === "{") {
    let depth = 0
    for (let j = i; j < tex.length; j++) {
      if (tex[j] === "{") depth++
      else if (tex[j] === "}" && --depth === 0) return [tex.slice(i + 1, j), j + 1]
    }
    return [tex.slice(i + 1), tex.length]
  }
  const command = COMMAND.exec(tex.slice(i))
  if (command) return [command[0], i + command[0].length]
  return [tex[i] ?? "", i + 1]
}

function atomic(text: string): boolean {
  return /^[\p{L}\p{N}.,]+$/u.test(text)
}

function wrapped(text: string): string {
  return atomic(text) ? text : `(${text})`
}

function script(text: string, table: Record<string, string>, marker: string): string {
  const chars = [...text]
  if (chars.length > 0 && chars.every((c) => c in table)) return chars.map((c) => table[c]).join("")
  return marker + wrapped(text)
}

export function texToText(tex: string): string {
  let out = ""
  let i = 0
  while (i < tex.length) {
    const ch = tex[i] ?? ""
    if (ch === "{" || ch === "}") {
      i++
      continue
    }
    if (ch === "^" || ch === "_") {
      const [arg, next] = argument(tex, i + 1)
      out += ch === "^" ? script(texToText(arg), SUPERSCRIPT, "^") : script(texToText(arg), SUBSCRIPT, "_")
      i = next
      continue
    }
    const command = ch === "\\" ? COMMAND.exec(tex.slice(i)) : null
    if (!command) {
      out += ch
      i++
      continue
    }
    const name = command[1] ?? ""
    i += command[0].length
    if (name === "frac" || name === "dfrac" || name === "tfrac") {
      const [top, afterTop] = argument(tex, i)
      const [bottom, afterBottom] = argument(tex, afterTop)
      out += `${wrapped(texToText(top))}/${wrapped(texToText(bottom))}`
      i = afterBottom
    } else if (name === "sqrt") {
      let index = ""
      if (tex[i] === "[") {
        const close = tex.indexOf("]", i)
        index = script(texToText(tex.slice(i + 1, close < 0 ? tex.length : close)), SUPERSCRIPT, "^")
        i = close < 0 ? tex.length : close + 1
      }
      const [radicand, next] = argument(tex, i)
      out += `${index}√${wrapped(texToText(radicand))}`
      i = next
    } else if (name === "boxed") {
      const [inner, next] = argument(tex, i)
      out += `[ ${texToText(inner)} ]`
      i = next
    } else if (WRAPPERS.has(name)) {
      const [inner, next] = argument(tex, i)
      out += texToText(inner)
      i = next
    } else if (DROPPED.has(name)) {
      if (tex[i] === ".") i++
    } else if (name in SYMBOLS) out += SYMBOLS[name]
    else if (FUNCTIONS.has(name)) out += name
    else if (tex[i] === "{") {
      const [inner, next] = argument(tex, i)
      out += `${command[0]}{${texToText(inner)}}`
      i = next
    } else out += command[0]
  }
  return out
}

// Code spans come first so a `$` or `\(` inside them is never read as math.
const MATH = /(`[^`\n]*`)|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$\$([\s\S]+?)\$\$|(?<![\\$])\$(?=[^\s$])([^$\n]+?)(?<=\S)\$(?!\d)/g

function replaceInProse(text: string): string {
  const replaced = text.replace(MATH, (whole, code, bracket, paren, dollars, single) => {
    if (code) return whole
    const display = bracket ?? dollars
    if (display !== undefined) return `\n\n  ${texToText(display).trim().replace(/\n\s*/g, "\n  ")}\n\n`
    return texToText(paren ?? single).trim()
  })
  // A display formula next to a blank line would otherwise leave two.
  return replaced.replace(/\n{3,}/g, "\n\n")
}

/** `source` with every formula written as text; fenced code is left alone. */
export function replaceMath(source: string): string {
  if (!/[$\\]/.test(source)) return source
  const parts = source.split(/^(\s*```[^\n]*\n[\s\S]*?(?:^\s*```[ \t]*$|(?![\s\S])))/m)
  // Odd parts are the fences the split captured; an unfinished one (still streaming) is code too.
  return parts.map((part, i) => (i % 2 === 1 ? part : replaceInProse(part))).join("")
}
