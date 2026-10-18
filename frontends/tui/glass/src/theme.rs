// Palette and icons. The colours are taken from the backdrop photograph (a
// moonlit forest): night blue for glass, warm moonlight for the accent, fog for
// text. Icons are Nerd Fonts 3.5 code points: Codicons for the interface (one
// designer, one line weight) and Seti for file types.

use ratatui::style::Color;

pub const MOON: Color = Color::Rgb(0xEF, 0xE3, 0xC2);
pub const FOG: Color = Color::Rgb(0xDD, 0xE4, 0xEE);
pub const MIST: Color = Color::Rgb(0xA3, 0xAE, 0xC1);
pub const DUSK: Color = Color::Rgb(0x6E, 0x7A, 0x92);
pub const MOSS: Color = Color::Rgb(0x9F, 0xD1, 0x8F);
pub const ROSEHIP: Color = Color::Rgb(0xEC, 0x8A, 0x82);
pub const INK: Color = Color::Rgb(0x0B, 0x11, 0x1D);
pub const INK_SOFT: Color = Color::Rgb(0x3B, 0x42, 0x55);
pub const HOVER: Color = Color::Rgb(0x22, 0x2D, 0x43);
/// What text fades towards when it dims or before it appears.
pub const NIGHT: Color = Color::Rgb(0x0A, 0x10, 0x1C);

/// Code blocks: the pane behind them and the colours of what they hold.
pub const CODE_BG: Color = Color::Rgb(0x16, 0x20, 0x34);
pub const SYN_KEYWORD: Color = Color::Rgb(0xC7, 0x9B, 0xEA);
pub const SYN_FUNCTION: Color = Color::Rgb(0x82, 0xAA, 0xF2);
pub const SYN_TYPE: Color = Color::Rgb(0xE6, 0xC3, 0x7A);
pub const SYN_NUMBER: Color = Color::Rgb(0xF0, 0x9A, 0x77);
pub const SYN_OPERATOR: Color = Color::Rgb(0x7F, 0xD4, 0xE6);
pub const SYN_COMMENT: Color = Color::Rgb(0x7B, 0x88, 0xA3);
pub const LINK: Color = Color::Rgb(0x8E, 0xB4, 0xF0);

/// Glass tint, as raw RGB for the compositor.
pub const GLASS_TINT: [u8; 3] = [0x0C, 0x14, 0x24];

pub const I_FOLDER: &str = "\u{ea83}";
pub const I_BRANCH: &str = "\u{ea68}";
pub const I_EYE: &str = "\u{ea70}";
pub const I_SEARCH: &str = "\u{ea6d}";
pub const I_EDIT: &str = "\u{ea73}";
pub const I_TERMINAL: &str = "\u{ea85}";
pub const I_GLOBE: &str = "\u{eb01}";
pub const I_CHECK: &str = "\u{eab2}";
pub const I_CLOSE: &str = "\u{ea76}";
pub const I_ERROR: &str = "\u{ea87}";
pub const I_CLOSED: &str = "\u{eabd}";
pub const I_CHIP: &str = "\u{ec19}";
pub const I_HELPER: &str = "\u{f1078}";
pub const I_EXPAND: &str = "\u{eb4c}";
pub const I_CHEVRON: &str = "\u{eab6}";

const SPINNER: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

pub fn spinner(ms: u128) -> &'static str {
    SPINNER[(ms / 80) as usize % SPINNER.len()]
}

/// A Seti glyph and its language colour for a file name.
pub fn file_icon(path: &str) -> (&'static str, Color) {
    let name = path.rsplit('/').next().unwrap_or(path);
    if name.starts_with("Dockerfile") {
        return ("\u{e650}", Color::Rgb(0x5B, 0xA8, 0xE0));
    }
    if name == ".gitignore" {
        return ("\u{e65d}", Color::Rgb(0xE8, 0x7A, 0x5A));
    }
    let ext = name.rsplit_once('.').map(|(_, e)| e).unwrap_or("");
    match ext {
        "py" => ("\u{e606}", Color::Rgb(0x7F, 0xB2, 0xE0)),
        "rs" => ("\u{e68b}", Color::Rgb(0xDE, 0xA5, 0x84)),
        "ts" | "mts" => ("\u{e628}", Color::Rgb(0x5E, 0xA2, 0xE8)),
        "tsx" | "jsx" => ("\u{e625}", Color::Rgb(0x6F, 0xD8, 0xF5)),
        "js" | "mjs" | "cjs" => ("\u{e60c}", Color::Rgb(0xE8, 0xD4, 0x4D)),
        "json" => ("\u{e60b}", Color::Rgb(0xCB, 0xCB, 0x6A)),
        "md" => ("\u{e609}", Color::Rgb(0x9B, 0xB3, 0xC9)),
        "html" => ("\u{e60e}", Color::Rgb(0xE3, 0x8B, 0x5A)),
        "css" => ("\u{e614}", Color::Rgb(0x6C, 0xB8, 0xE8)),
        "sh" | "bash" | "zsh" | "fish" => ("\u{e691}", Color::Rgb(0x9C, 0xC9, 0x6B)),
        "toml" | "yaml" | "yml" | "ini" | "cfg" => ("\u{e615}", Color::Rgb(0xA9, 0xB1, 0xBF)),
        "lock" => ("\u{e672}", Color::Rgb(0xA0, 0xA6, 0xB0)),
        "go" => ("\u{e627}", Color::Rgb(0x6F, 0xC8, 0xE0)),
        "c" | "h" => ("\u{e649}", Color::Rgb(0x8A, 0xA8, 0xD8)),
        "cpp" | "hpp" | "cc" => ("\u{e646}", Color::Rgb(0x8A, 0xA8, 0xD8)),
        "java" => ("\u{e66d}", Color::Rgb(0xE0, 0x8F, 0x5A)),
        "svelte" => ("\u{e697}", Color::Rgb(0xF0, 0x7A, 0x52)),
        "vue" => ("\u{e6a0}", Color::Rgb(0x7F, 0xC8, 0x9B)),
        "png" | "jpg" | "jpeg" | "gif" | "webp" | "svg" => ("\u{e60d}", Color::Rgb(0xB8, 0x9C, 0xD8)),
        _ => ("\u{e64e}", Color::Rgb(0x9A, 0xA4, 0xB2)),
    }
}

pub fn rgb(c: Color) -> (u8, u8, u8) {
    match c {
        Color::Rgb(r, g, b) => (r, g, b),
        _ => (0xDD, 0xE4, 0xEE),
    }
}

pub fn mix(a: Color, b: Color, t: f32) -> Color {
    let t = t.clamp(0.0, 1.0);
    let (ar, ag, ab) = rgb(a);
    let (br, bg, bb) = rgb(b);
    let lerp = |x: u8, y: u8| (x as f32 + (y as f32 - x as f32) * t).round() as u8;
    Color::Rgb(lerp(ar, br), lerp(ag, bg), lerp(ab, bb))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_icons_follow_the_extension() {
        assert_eq!(file_icon("kernel/agent.py").0, "\u{e606}");
        assert_eq!(file_icon("src/main.rs").0, "\u{e68b}");
        assert_eq!(file_icon("ui/app.tsx").0, "\u{e625}");
        assert_eq!(file_icon("Makefile").0, "\u{e64e}");
    }

    #[test]
    fn mix_reaches_both_ends() {
        assert_eq!(mix(INK, FOG, 0.0), INK);
        assert_eq!(mix(INK, FOG, 1.0), FOG);
    }
}
