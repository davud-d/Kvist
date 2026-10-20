// Math in the conversation. A display formula (`\[ … \]`, `$$ … $$`) is typeset by RaTeX and shown
// as a picture over the rows the transcript reserves for it; an inline one, or a display one RaTeX
// cannot read, becomes plain text (`\sqrt{9} \approx 3` -> `√9 ≈ 3`). The text converter is a port of
// `frontends/tui/ink/math.ts`; a fix in one is made in the other by hand.
//
// Third-party: RaTeX (MIT, © erweixin, https://github.com/erweixin/RaTeX) typesets and draws;
// the KaTeX fonts it embeds are under the SIL Open Font License 1.1 (see THIRD_PARTY.md).

use image::{ImageFormat, RgbaImage, imageops};
use ratex_layout::{LayoutOptions, layout, to_display_list, validate_layout};
use ratex_parser::parser::parse;
use ratex_render::{RenderOptions, render_to_png};
use ratex_types::color::Color;
use ratex_types::display_item::DisplayList;
use ratex_types::math_style::MathStyle;

/// Tallest formula picture, in rows; a taller one is scaled down to fit.
const MAX_ROWS: u32 = 16;

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
            out.push(ch);
            i += 1;
            continue;
        };
        let written: String = chars[i..next].iter().collect();
        i = next;
        match name.as_str() {
            "frac" | "dfrac" | "tfrac" => {
                let (top, after_top) = argument(&chars, i);
                let (bottom, after_bottom) = argument(&chars, after_top);
                out += &format!("{}/{}", wrapped(tex_to_text(&top)), wrapped(tex_to_text(&bottom)));
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

/// How a formula sits in the transcript: the size of its picture in cells and the em it is drawn at.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Fit {
    pub em: f32,
    pub cols: u16,
    pub rows: u16,
}

fn laid_out(tex: &str, colour: [u8; 3]) -> Option<DisplayList> {
    let ink = Color { r: colour[0] as f32 / 255.0, g: colour[1] as f32 / 255.0, b: colour[2] as f32 / 255.0, a: 1.0 };
    let outcome = std::panic::catch_unwind(|| {
        let ast = parse(tex).ok()?;
        let options = LayoutOptions::default().with_style(MathStyle::Display).with_color(ink);
        let boxed = layout(&ast, &options);
        validate_layout(&boxed).ok()?;
        Some(to_display_list(&boxed))
    });
    let list = outcome.ok()??;
    (list.width.is_finite() && list.width > 0.0 && (list.height + list.depth).is_finite()).then_some(list)
}

/// Size for `tex` drawn at `em` pixels per em in a column `room` pixels wide; `None` when RaTeX cannot read it.
pub fn fit(tex: &str, em: f32, room: u32, cell: (u32, u32)) -> Option<Fit> {
    let list = laid_out(tex, [255; 3])?;
    let shrink = (room as f64 / (list.width * em as f64)).min(1.0);
    let em = em * shrink as f32;
    let rows = (((list.height + list.depth) * em as f64) / cell.1 as f64).ceil().clamp(1.0, MAX_ROWS as f64) as u32;
    let em = em.min((rows * cell.1) as f32 / (list.height + list.depth).max(0.01) as f32);
    Some(Fit { em, cols: (room / cell.0).max(1) as u16, rows: rows as u16 })
}

/// The picture of `tex` as RGBA, `fit.cols × fit.rows` cells big, the formula centred on transparency.
pub fn paint(tex: &str, fit: Fit, cell: (u32, u32), colour: [u8; 3]) -> Option<RgbaImage> {
    let list = laid_out(tex, colour)?;
    let options = RenderOptions {
        font_size: fit.em,
        padding: 0.0,
        background_color: Color { r: 0.0, g: 0.0, b: 0.0, a: 0.0 },
        font_dir: String::new(),
        device_pixel_ratio: 1.0,
    };
    let png = render_to_png(&list, &options).ok()?;
    let formula = image::load_from_memory_with_format(&png, ImageFormat::Png).ok()?.to_rgba8();
    let (width, height) = (fit.cols as u32 * cell.0, fit.rows as u32 * cell.1);
    let mut canvas = RgbaImage::new(width, height);
    let x = (width as i64 - formula.width() as i64) / 2;
    let y = (height as i64 - formula.height() as i64) / 2;
    imageops::overlay(&mut canvas, &formula, x, y);
    Some(canvas)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(tex: &str) -> String {
        tex_to_text(tex)
    }

    #[test]
    fn roots_products_and_approximations_read_as_text() {
        assert_eq!(text("\\sqrt{6,646,021}\\approx2577{,}9878"), "√6,646,021≈2577,9878");
        assert_eq!(text("2586\\cdot2570+1=(2578+8)(2578-8)+1"), "2586·2570+1=(2578+8)(2578-8)+1");
        assert_eq!(text("\\sqrt{a+b}"), "√(a+b)");
        assert_eq!(text("\\sqrt[3]{x}"), "³√x");
    }

    #[test]
    fn powers_and_indices_become_unicode_when_every_character_has_one() {
        assert_eq!(text("2578^2-8^2"), "2578²-8²");
        assert_eq!(text("x_{10}^{-1}"), "x₁₀⁻¹");
        assert_eq!(text("e^{x+y}"), "e^(x+y)");
        assert_eq!(text("a_{k}"), "aₖ");
    }

    #[test]
    fn fractions_parenthesise_only_what_is_not_a_single_word() {
        assert_eq!(text("\\frac{1}{2}"), "1/2");
        assert_eq!(text("\\frac{a+b}{c}"), "(a+b)/c");
    }

    #[test]
    fn styling_commands_boxes_and_left_right_keep_the_content() {
        assert_eq!(text("\\boxed{\\sqrt{2}\\approx1.41}"), "[ √2≈1.41 ]");
        assert_eq!(text("\\left(\\frac{a}{b}\\right)"), "(a/b)");
        assert_eq!(text("\\mathbf{x}+\\text{ cm}"), "x+ cm");
        assert_eq!(text("\\alpha\\to\\infty"), "α→∞");
        assert_eq!(text("\\unknowncommand{x}"), "\\unknowncommand{x}");
    }

    fn pieces(source: &str) -> Vec<Piece> {
        split(source)
    }

    #[test]
    fn display_formulas_split_the_text_and_inline_ones_become_text() {
        assert_eq!(pieces("so \\(2^2\\) and $3^2$ here"), vec![Piece::Text("so 2² and 3² here".into())]);
        assert_eq!(
            pieces("a\n\\[\n\\sqrt{9}=3\n\\]\nb"),
            vec![Piece::Text("a\n".into()), Piece::Display("\n\\sqrt{9}=3\n".into()), Piece::Text("\nb".into())]
        );
        assert_eq!(pieces("$$x^2$$"), vec![Piece::Display("x^2".into())]);
    }

    #[test]
    fn money_escapes_code_spans_and_fences_are_not_math() {
        assert_eq!(pieces("costs $5 and $6 today"), vec![Piece::Text("costs $5 and $6 today".into())]);
        assert_eq!(pieces("a \\$b\\$ c"), vec![Piece::Text("a \\$b\\$ c".into())]);
        assert_eq!(pieces("use `$x^2$` literally"), vec![Piece::Text("use `$x^2$` literally".into())]);
        assert_eq!(
            pieces("```tex\n\\[x^2\\]\n```\n$y^2$"),
            vec![Piece::Text("```tex\n\\[x^2\\]\n```\ny²".into())]
        );
        assert_eq!(pieces("```tex\n\\[x^2\\]"), vec![Piece::Text("```tex\n\\[x^2\\]".into())]);
    }

    #[test]
    fn a_formula_still_arriving_stays_as_written() {
        assert_eq!(pieces("so \\[x^2"), vec![Piece::Text("so \\[x^2".into())]);
    }

    #[test]
    fn a_formula_is_measured_and_drawn_into_whole_cells() {
        let cell = (10, 20);
        let fit = fit("\\sqrt{6646021}\\approx2577.9878", 26.0, 400, cell).expect("RaTeX reads it");
        assert!(fit.rows >= 1 && fit.cols == 40);
        let picture = paint("\\sqrt{6646021}\\approx2577.9878", fit, cell, [221, 228, 238]).expect("it draws");
        assert_eq!((picture.width(), picture.height()), (400, fit.rows as u32 * 20));
        assert!(picture.pixels().any(|p| p.0[3] > 0), "the formula leaves ink");
        assert!(picture.get_pixel(0, 0).0[3] == 0, "the rest is transparent");
    }

    #[test]
    fn a_wide_formula_is_scaled_to_the_room_and_text_ratex_cannot_read_has_no_fit() {
        let wide = "a+b+c+d+e+f+g+h+i+j+k+l+m+n+o+p+q+r+s+t+u+v+w+x+y+z";
        let fit = fit(wide, 26.0, 200, (10, 20)).expect("reads");
        assert!(fit.em < 26.0);
        assert!(super::fit("\\notacommand{", 26.0, 200, (10, 20)).is_none());
    }
}
