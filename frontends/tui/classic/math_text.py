# -*- coding: utf-8 -*-
"""LaTeX math as plain text for a terminal that cannot draw it.

`\\sqrt{6646021} \\approx 2577{,}9878` becomes `√6646021 ≈ 2577,9878`. Not a typesetter: nested
fractions and matrices come out as readable approximations, and a command it does not know is
left as written. A port of `frontends/tui/ink/math.ts`; a fix in one is made in the other by hand.
"""
from __future__ import annotations

import re

_SYMBOLS = {
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "approx": "≈", "neq": "≠", "ne": "≠",
    "equiv": "≡", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ll": "≪", "gg": "≫", "infty": "∞",
    "partial": "∂", "nabla": "∇", "to": "→", "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "implies": "⇒", "iff": "⇔", "mapsto": "↦",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "cup": "∪", "cap": "∩", "forall": "∀",
    "exists": "∃", "emptyset": "∅", "neg": "¬", "land": "∧", "lor": "∨", "sum": "∑", "prod": "∏",
    "int": "∫", "oint": "∮", "circ": "∘", "ldots": "…", "dots": "…", "cdots": "⋯", "vdots": "⋮",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "propto": "∝", "sim": "∼", "degree": "°", "prime": "′",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω", "quad": "  ", "qquad": "    ",
    ",": " ", ";": " ", ":": " ", " ": " ", "!": "", "\\": "\n", "{": "{", "}": "}", "%": "%",
    "$": "$", "&": "&", "_": "_", "#": "#",
}
_FUNCTIONS = frozenset(
    "sin cos tan cot sec csc log ln lg exp lim min max gcd det arcsin arccos arctan sinh cosh tanh".split()
)
# Commands that only style or size their argument.
_WRAPPERS = frozenset(
    "text textbf textit mathrm mathbf mathit mathbb mathcal mathsf operatorname displaystyle textstyle mbox".split()
)
_DROPPED = frozenset("left right big Big bigg Bigg limits nolimits".split())

_SUPERSCRIPT = dict(zip("0123456789+-−=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱ"))
_SUBSCRIPT = dict(zip("0123456789+-−=()aeoxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ"))

_COMMAND = re.compile(r"\\([A-Za-z]+|[^A-Za-z])")
_ATOMIC = re.compile(r"[\w.,]+")


def _argument(tex: str, at: int) -> tuple[str, int]:
    """The argument that starts at `at`: a braced group, a command or one character."""
    i = at
    while i < len(tex) and tex[i] == " ":
        i += 1
    if tex[i : i + 1] == "{":
        depth = 0
        for j in range(i, len(tex)):
            if tex[j] == "{":
                depth += 1
            elif tex[j] == "}":
                depth -= 1
                if depth == 0:
                    return tex[i + 1 : j], j + 1
        return tex[i + 1 :], len(tex)
    command = _COMMAND.match(tex, i)
    if command:
        return command.group(0), command.end()
    return tex[i : i + 1], i + 1


def _wrapped(text: str) -> str:
    return text if _ATOMIC.fullmatch(text) else f"({text})"


def _script(text: str, table: dict[str, str], marker: str) -> str:
    if text and all(char in table for char in text):
        return "".join(table[char] for char in text)
    return marker + _wrapped(text)


def tex_to_text(tex: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(tex):
        char = tex[i]
        if char in "{}":
            i += 1
            continue
        if char in "^_":
            arg, i = _argument(tex, i + 1)
            table, marker = (_SUPERSCRIPT, "^") if char == "^" else (_SUBSCRIPT, "_")
            out.append(_script(tex_to_text(arg), table, marker))
            continue
        command = _COMMAND.match(tex, i) if char == "\\" else None
        if not command:
            out.append(char)
            i += 1
            continue
        name = command.group(1)
        i = command.end()
        if name in ("frac", "dfrac", "tfrac"):
            top, i = _argument(tex, i)
            bottom, i = _argument(tex, i)
            out.append(f"{_wrapped(tex_to_text(top))}/{_wrapped(tex_to_text(bottom))}")
        elif name == "sqrt":
            index = ""
            if tex[i : i + 1] == "[":
                close = tex.find("]", i)
                end = len(tex) if close < 0 else close
                index = _script(tex_to_text(tex[i + 1 : end]), _SUPERSCRIPT, "^")
                i = len(tex) if close < 0 else close + 1
            radicand, i = _argument(tex, i)
            out.append(f"{index}√{_wrapped(tex_to_text(radicand))}")
        elif name == "boxed":
            inner, i = _argument(tex, i)
            out.append(f"[ {tex_to_text(inner)} ]")
        elif name in _WRAPPERS:
            inner, i = _argument(tex, i)
            out.append(tex_to_text(inner))
        elif name in _DROPPED:
            if tex[i : i + 1] == ".":
                i += 1
        elif name in _SYMBOLS:
            out.append(_SYMBOLS[name])
        elif name in _FUNCTIONS:
            out.append(name)
        elif tex[i : i + 1] == "{":
            inner, i = _argument(tex, i)
            out.append(f"{command.group(0)}{{{tex_to_text(inner)}}}")
        else:
            out.append(command.group(0))
    return "".join(out)


# Code spans come first so a `$` or `\(` inside them is never read as math.
_MATH = re.compile(
    r"(`[^`\n]*`)"
    r"|\\\[([\s\S]+?)\\\]"
    r"|\\\(([\s\S]+?)\\\)"
    r"|\$\$([\s\S]+?)\$\$"
    r"|(?<![\\$])\$(?=[^\s$])([^$\n]+?)(?<=\S)\$(?!\d)"
)
_FENCE = re.compile(r"^(\s*```[^\n]*\n[\s\S]*?(?:^\s*```[ \t]*$|\Z))", re.MULTILINE)


def _replace_in_prose(text: str) -> str:
    def swap(match: re.Match[str]) -> str:
        code, bracket, paren, dollars, single = match.groups()
        if code:
            return match.group(0)
        display = bracket if bracket is not None else dollars
        if display is not None:
            body = re.sub(r"\n\s*", "\n  ", tex_to_text(display).strip())
            return f"\n\n  {body}\n\n"
        return tex_to_text(paren if paren is not None else single).strip()

    # A display formula next to a blank line would otherwise leave two.
    return re.sub(r"\n{3,}", "\n\n", _MATH.sub(swap, text))


def replace_math(source: str) -> str:
    """`source` with every formula written as text; fenced code is left alone."""
    if "$" not in source and "\\" not in source:
        return source
    # Odd parts are the fences the split captured; an unfinished one (still streaming) is code too.
    parts = _FENCE.split(source)
    return "".join(part if index % 2 else _replace_in_prose(part) for index, part in enumerate(parts))
