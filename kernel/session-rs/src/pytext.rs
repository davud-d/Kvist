//! Python's string rules, where Rust's differ.
//!
//! This program answers the same requests as the Python core and shares its
//! files, so wherever the two languages disagree about a character (what is
//! whitespace, what a case fold produces, how a string is quoted in a message)
//! Python's answer is the contract.

use std::sync::LazyLock;

use regex::Regex;

/// `str.isspace()` for one character: Unicode White_Space plus the four ASCII
/// information separators, which Python counts and Rust does not.
pub fn is_space(character: char) -> bool {
    character.is_whitespace() || ('\x1c'..='\x1f').contains(&character)
}

/// `str.strip()`.
pub fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// `str.split()` with no argument: runs of whitespace separate, none is kept.
pub fn split(text: &str) -> impl Iterator<Item = &str> {
    text.split(is_space).filter(|part| !part.is_empty())
}

/// `len(text)`: code points, not bytes.
pub fn length(text: &str) -> usize {
    text.chars().count()
}

/// `str.casefold()`, exact wherever the result contains an ASCII character.
///
/// Every caller compares the fold with ASCII words, so only that part has to
/// agree with Python. `char::to_lowercase` already does for every character
/// but the ones listed, whose full case folding has ASCII letters in it.
pub fn casefold(text: &str) -> String {
    let mut folded = String::with_capacity(text.len());
    for character in text.chars() {
        if character.is_ascii() {
            folded.push(character.to_ascii_lowercase());
            continue;
        }
        match character {
            '\u{df}' | '\u{1e9e}' => folded.push_str("ss"),
            '\u{149}' => folded.push_str("\u{2bc}n"),
            '\u{17f}' => folded.push('s'),
            '\u{1f0}' => folded.push_str("j\u{30c}"),
            '\u{1e96}' => folded.push_str("h\u{331}"),
            '\u{1e97}' => folded.push_str("t\u{308}"),
            '\u{1e98}' => folded.push_str("w\u{30a}"),
            '\u{1e99}' => folded.push_str("y\u{30a}"),
            '\u{1e9a}' => folded.push_str("a\u{2be}"),
            '\u{fb00}' => folded.push_str("ff"),
            '\u{fb01}' => folded.push_str("fi"),
            '\u{fb02}' => folded.push_str("fl"),
            '\u{fb03}' => folded.push_str("ffi"),
            '\u{fb04}' => folded.push_str("ffl"),
            '\u{fb05}' | '\u{fb06}' => folded.push_str("st"),
            other => folded.extend(other.to_lowercase()),
        }
    }
    folded
}

/// `str.isdecimal()`: at least one character, all of them decimal digits.
pub fn is_decimal(text: &str) -> bool {
    static DECIMAL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\p{Nd}+$").expect("valid pattern"));
    DECIMAL.is_match(text)
}

fn is_printable(character: char) -> bool {
    static HIDDEN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[\p{C}\p{Z}]$").expect("valid pattern"));
    character == ' ' || !HIDDEN.is_match(character.encode_utf8(&mut [0; 4]))
}

/// The escape Python writes for a character it will not show as itself.
pub fn escape(character: char) -> String {
    let code = u32::from(character);
    if code <= 0xff {
        format!("\\x{code:02x}")
    } else if code <= 0xffff {
        format!("\\u{code:04x}")
    } else {
        format!("\\U{code:08x}")
    }
}

/// `repr(text)`: the quoting of names and paths inside error messages.
pub fn repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut quoted = String::with_capacity(text.len() + 2);
    quoted.push(quote);
    for character in text.chars() {
        match character {
            '\\' => quoted.push_str("\\\\"),
            '\n' => quoted.push_str("\\n"),
            '\r' => quoted.push_str("\\r"),
            '\t' => quoted.push_str("\\t"),
            _ if character == quote => {
                quoted.push('\\');
                quoted.push(quote);
            }
            _ if is_printable(character) => quoted.push(character),
            _ => quoted.push_str(&escape(character)),
        }
    }
    quoted.push(quote);
    quoted
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn whitespace_follows_python() {
        assert!(is_space('\x1c') && is_space('\u{a0}') && is_space('\u{3000}'));
        assert!(!is_space('\u{200b}'));
        assert_eq!(strip("\x1f  goal \n"), "goal");
        assert_eq!(split(" a\t b\u{2003}c ").collect::<Vec<_>>(), ["a", "b", "c"]);
    }

    #[test]
    fn casefold_reaches_ascii_where_python_does() {
        assert_eq!(casefold("TOKEN"), "token");
        assert_eq!(casefold("pa\u{17f}\u{17f}word"), "password");
        assert_eq!(casefold("\u{212a}EY"), "key");
        assert_eq!(casefold("\u{fb03}"), "ffi");
        assert_eq!(casefold("\u{130}"), "i\u{307}");
    }

    #[test]
    fn repr_quotes_like_python() {
        assert_eq!(repr("chat-1"), "'chat-1'");
        assert_eq!(repr("it's"), "\"it's\"");
        assert_eq!(repr("a'b\"c"), "'a\\'b\"c'");
        assert_eq!(repr("a\nb\x1b\u{a0}\u{200b}"), "'a\\nb\\x1b\\xa0\\u200b'");
        assert_eq!(repr("сессия"), "'сессия'");
    }
}
