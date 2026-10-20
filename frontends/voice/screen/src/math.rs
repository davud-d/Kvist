// Math in what the voice says and shows: a formula (`\[ … \]`, `$$ … $$`, `\( … \)`, `$ … $`) becomes
// words the speech engine can read (`\sqrt{9} \approx 3` -> `√9 ≈ 3`, then `speech_ru.py` says the symbols).
// The conversation screen draws display formulas as pictures; this screen has no transcript to draw them in.
// A copy of `frontends/tui/glass/src/math.rs` without the typesetting (no RaTeX here), maintained by hand:
// a fix to the rules is made in both, and in `ink/math.ts` and `classic/math_text.py`.
// Differences for speech: a minus is U+2212 so "2578-8" is not read as a range, and a fraction says
// "разделить на" instead of drawing a slash.

#[derive(Debug, PartialEq)]
pub enum Piece {
    Text(String),
    Display(String),
}

fn symbol(name: &str) -> Option<&'static str> {
    Some(match name {
        "cdot" => "·", "times" => "×", "div" => "÷", "pm" => "±", "mp" => "∓", "approx" => "≈",
        "neq" | "ne" => "≠", "equiv" => "≡", "le" | "leq" => "≤", "ge" | "geq" => "≥", "ll" => "≪",
        "gg" => "≫", "infty" => "∞", "partial" => "∂", "nabla" => "∇", "to" | "rightarrow" => "→",
        "leftarrow" => "←", "leftrightarrow" => "↔", "Rightarrow" | "implies" => "⇒", "Leftarrow" => "⇐",
        "Leftrightarrow" | "iff" => "⇔", "mapsto" => "↦", "in" => "∈", "notin" => "∉", "subset" => "⊂",
        "subseteq" => "⊆", "cup" => "∪", "cap" => "∩", "forall" => "∀", "exists" => "∃", "emptyset" => "∅",
        "neg" => "¬", "land" => "∧", "lor" => "∨", "sum" => "∑", "prod" => "∏", "int" => "∫", "oint" => "∮",
        "circ" => "∘", "ldots" | "dots" => "…", "cdots" => "⋯", "vdots" => "⋮", "angle" => "∠", "perp" => "⊥",
        "parallel" => "∥", "propto" => "∝", "sim" => "∼", "degree" => "°", "prime" => "′",
        "alpha" => "α", "beta" => "β", "gamma" => "γ", "delta" => "δ", "epsilon" | "varepsilon" => "ε",
        "zeta" => "ζ", "eta" => "η", "theta" => "θ", "iota" => "ι", "kappa" => "κ", "lambda" => "λ",
        "mu" => "μ", "nu" => "ν", "xi" => "ξ", "pi" => "π", "rho" => "ρ", "sigma" => "σ", "tau" => "τ",
        "upsilon" => "υ", "phi" | "varphi" => "φ", "chi" => "χ", "psi" => "ψ", "omega" => "ω",
        "Gamma" => "Γ", "Delta" => "Δ", "Theta" => "Θ", "Lambda" => "Λ", "Xi" => "Ξ", "Pi" => "Π",
        "Sigma" => "Σ", "Phi" => "Φ", "Psi" => "Ψ", "Omega" => "Ω", "quad" => "  ", "qquad" => "    ",
        "," | ";" | ":" | " " => " ", "!" => "", "\\" => "\n", "{" => "{", "}" => "}", "%" => "%",
        "$" => "$", "&" => "&", "_" => "_", "#" => "#",
        _ => return None,
    })
}

const FUNCTIONS: [&str; 20] = [
    "sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "lg", "exp", "lim", "min", "max", "gcd", "det",
    "arcsin", "arccos", "arctan", "sinh", "cosh",
];
/// Commands that only style or size their argument.
const WRAPPERS: [&str; 10] = [
    "text", "textbf", "textit", "mathrm", "mathbf", "mathit", "mathbb", "mathcal", "mathsf", "operatorname",
];
const DROPPED: [&str; 8] = ["left", "right", "big", "Big", "bigg", "Bigg", "limits", "nolimits"];

fn superscript(c: char) -> Option<char> {
    Some(match c {
        '0' => '⁰', '1' => '¹', '2' => '²', '3' => '³', '4' => '⁴', '5' => '⁵', '6' => '⁶', '7' => '⁷',
        '8' => '⁸', '9' => '⁹', '+' => '⁺', '-' | '−' => '⁻', '=' => '⁼', '(' => '⁽', ')' => '⁾',
        'n' => 'ⁿ', 'i' => 'ⁱ',
        _ => return None,
    })
}

fn subscript(c: char) -> Option<char> {
    Some(match c {
        '0' => '₀', '1' => '₁', '2' => '₂', '3' => '₃', '4' => '₄', '5' => '₅', '6' => '₆', '7' => '₇',
        '8' => '₈', '9' => '₉', '+' => '₊', '-' | '−' => '₋', '=' => '₌', '(' => '₍', ')' => '₎',
        'a' => 'ₐ', 'e' => 'ₑ', 'o' => 'ₒ', 'x' => 'ₓ', 'h' => 'ₕ', 'k' => 'ₖ', 'l' => 'ₗ', 'm' => 'ₘ',
        'n' => 'ₙ', 'p' => 'ₚ', 's' => 'ₛ', 't' => 'ₜ',
        _ => return None,
    })
}

/// The command name at `at` (a backslash) and the index after it.
fn command(chars: &[char], at: usize) -> Option<(String, usize)> {
    let first = *chars.get(at + 1)?;
    if !first.is_ascii_alphabetic() {
        return Some((first.to_string(), at + 2));
    }
    let name: String = chars[at + 1..].iter().take_while(|c| c.is_ascii_alphabetic()).collect();
    let end = at + 1 + name.chars().count();
    Some((name, end))
}

/// The argument that starts at `at`: a braced group, a command or one character.
fn argument(chars: &[char], at: usize) -> (String, usize) {
    let mut i = at;
    while chars.get(i) == Some(&' ') {
        i += 1;
    }
    match chars.get(i) {
        Some('{') => {
            let mut depth = 0;
            for (j, c) in chars.iter().enumerate().skip(i) {
                match c {
                    '{' => depth += 1,
                    '}' => {
                        depth -= 1;
                        if depth == 0 {
                            return (chars[i + 1..j].iter().collect(), j + 1);
                        }
                    }
                    _ => {}
                }
            }
            (chars[i + 1..].iter().collect(), chars.len())
        }
        Some('\\') => match command(chars, i) {
            Some((_, end)) => (chars[i..end].iter().collect(), end),
            None => (String::new(), chars.len()),
        },
        Some(c) => (c.to_string(), i + 1),
        None => (String::new(), i),
    }
}

fn wrapped(text: String) -> String {
    let atomic = !text.is_empty() && text.chars().all(|c| c.is_alphanumeric() || c == '.' || c == ',');
    if atomic { text } else { format!("({text})") }
}

fn script(text: String, table: fn(char) -> Option<char>, marker: char) -> String {
    let mapped: Option<String> = text.chars().map(table).collect();
    match mapped {
        Some(done) if !text.is_empty() => done,
        _ => format!("{marker}{}", wrapped(text)),
    }
}

pub fn tex_to_text(tex: &str) -> String {
    let chars: Vec<char> = tex.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        let ch = chars[i];
        if ch == '{' || ch == '}' {
            i += 1;
            continue;
        }
        if ch == '^' || ch == '_' {
            let (arg, next) = argument(&chars, i + 1);
            let inner = tex_to_text(&arg);
            out += &if ch == '^' { script(inner, superscript, '^') } else { script(inner, subscript, '_') };
            i = next;
            continue;
        }
        let Some((name, next)) = (if ch == '\\' { command(&chars, i) } else { None }) else {
            out.push(if ch == '-' { '−' } else { ch });
            i += 1;
            continue;
        };
        let written: String = chars[i..next].iter().collect();
        i = next;
        match name.as_str() {
            "frac" | "dfrac" | "tfrac" => {
                let (top, after_top) = argument(&chars, i);
                let (bottom, after_bottom) = argument(&chars, after_top);
                out += &format!("{} разделить на {}", wrapped(tex_to_text(&top)), wrapped(tex_to_text(&bottom)));
                i = after_bottom;
            }
            "sqrt" => {
                let mut index = String::new();
                if chars.get(i) == Some(&'[') {
                    let close = chars[i..].iter().position(|&c| c == ']').map(|p| p + i);
                    let end = close.unwrap_or(chars.len());
                    index = script(tex_to_text(&chars[i + 1..end].iter().collect::<String>()), superscript, '^');
                    i = close.map_or(chars.len(), |p| p + 1);
                }
                let (radicand, next) = argument(&chars, i);
                out += &format!("{index}√{}", wrapped(tex_to_text(&radicand)));
                i = next;
            }
            "boxed" => {
                let (inner, next) = argument(&chars, i);
                out += &format!("[ {} ]", tex_to_text(&inner));
                i = next;
            }
            "displaystyle" | "textstyle" => {}
            n if WRAPPERS.contains(&n) => {
                let (inner, next) = argument(&chars, i);
                out += &tex_to_text(&inner);
                i = next;
            }
            n if DROPPED.contains(&n) => {
                if chars.get(i) == Some(&'.') {
                    i += 1;
                }
            }
            n if symbol(n).is_some() => out += symbol(n).unwrap_or(""),
            n if FUNCTIONS.contains(&n) => out += n,
            _ if chars.get(i) == Some(&'{') => {
                let (inner, next) = argument(&chars, i);
                out += &format!("{written}{{{}}}", tex_to_text(&inner));
                i = next;
            }
            _ => out += &written,
        }
    }
    out
}

/// What follows `from` up to the first `close`, as (inner text, index after the closer).
fn until(chars: &[char], from: usize, close: &[char]) -> Option<(String, usize)> {
    let end = (from..chars.len()).find(|&j| chars[j..].starts_with(close))?;
    Some((chars[from..end].iter().collect(), end + close.len()))
}

/// A `$…$` pair that reads as math: no space inside the edges, and no digit right after (`$5 and $6`).
fn single_dollar(chars: &[char], at: usize) -> Option<(String, usize)> {
    let first = *chars.get(at + 1)?;
    if first.is_whitespace() || first == '$' {
        return None;
    }
    let (inner, next) = until(chars, at + 1, &['$'])?;
    let closes_cleanly = !inner.contains('\n')
        && !inner.ends_with(char::is_whitespace)
        && !chars.get(next).is_some_and(char::is_ascii_digit);
    closes_cleanly.then_some((inner, next))
}

fn scan_prose(text: &str, pieces: &mut Vec<Piece>) {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < chars.len() {
        let rest = &chars[i..];
        let found = if rest.starts_with(&['\\', '[']) {
            until(&chars, i + 2, &['\\', ']']).map(|(tex, next)| (Some(tex), String::new(), next))
        } else if rest.starts_with(&['\\', '(']) {
            until(&chars, i + 2, &['\\', ')']).map(|(tex, next)| (None, tex_to_text(&tex).trim().to_string(), next))
        } else if rest.starts_with(&['$', '$']) {
            until(&chars, i + 2, &['$', '$']).map(|(tex, next)| (Some(tex), String::new(), next))
        } else if rest.starts_with(&['$']) {
            single_dollar(&chars, i).map(|(tex, next)| (None, tex_to_text(&tex).trim().to_string(), next))
        } else {
            None
        };
        match found {
            Some((Some(display), _, next)) => {
                pieces.push(Piece::Text(std::mem::take(&mut out)));
                pieces.push(Piece::Display(display));
                i = next;
            }
            Some((None, inline, next)) => {
                out += &inline;
                i = next;
            }
            // An unfinished formula (still arriving) and anything else stay as written; an escape and a
            // code span are copied whole so what is inside is never read as math.
            None => {
                let width = match rest {
                    ['\\', _, ..] => 2,
                    ['`', ..] => rest.iter().skip(1).take_while(|&&c| c != '\n').position(|&c| c == '`').map_or(1, |p| p + 2),
                    _ => 1,
                };
                out.extend(&chars[i..i + width]);
                i += width;
            }
        }
    }
    pieces.push(Piece::Text(out));
}

/// `source` as (is code, text): fenced blocks, an unfinished one included, and the prose between them.
fn chunks(source: &str) -> Vec<(bool, String)> {
    let mut out: Vec<(bool, String)> = Vec::new();
    let mut fenced = false;
    for line in source.split_inclusive('\n') {
        let fence = line.trim_start().starts_with("```");
        let code = fenced || fence;
        match out.last_mut() {
            Some((was_code, text)) if *was_code == code => text.push_str(line),
            _ => out.push((code, line.to_string())),
        }
        if fence {
            fenced = !fenced;
        }
    }
    out
}

/// `source` cut at its display formulas; inline ones are already text. Fenced code is left alone.
pub fn split(source: &str) -> Vec<Piece> {
    let mut pieces = Vec::new();
    for (code, text) in chunks(source) {
        if code {
            pieces.push(Piece::Text(text));
        } else {
            scan_prose(&text, &mut pieces);
        }
    }
    let mut merged: Vec<Piece> = Vec::new();
    for piece in pieces {
        match (merged.last_mut(), piece) {
            (Some(Piece::Text(before)), Piece::Text(next)) => before.push_str(&next),
            (_, piece) => merged.push(piece),
        }
    }
    merged.retain(|piece| !matches!(piece, Piece::Text(text) if text.is_empty()));
    merged
}

/// `source` with every formula, inline or display, written as text; fenced code is left alone.
pub fn spoken(source: &str) -> String {
    split(source)
        .into_iter()
        .map(|piece| match piece {
            Piece::Text(text) => text,
            Piece::Display(tex) => format!(" {} ", tex_to_text(&tex).trim()),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_formula_is_written_as_text_with_a_real_minus_and_a_spoken_fraction() {
        assert_eq!(spoken("так: $\\sqrt{6646021}\\approx2577{,}9878$."), "так: √6646021≈2577,9878.");
        assert_eq!(spoken("\\[2578^2-8^2\\]"), " 2578²−8² ");
        assert_eq!(spoken("$\\frac{a+b}{c}$"), "(a+b) разделить на c");
    }

    #[test]
    fn money_code_and_an_unfinished_formula_are_left_alone() {
        assert_eq!(spoken("costs $5 and $6"), "costs $5 and $6");
        assert_eq!(spoken("run `$x$` now"), "run `$x$` now");
        assert_eq!(spoken("so \\[x^2"), "so \\[x^2");
    }
}
