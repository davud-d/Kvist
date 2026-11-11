//! Secret redaction, the same answers as `kernel/sandbox/redaction.py`.
//!
//! A session document is redacted every time it is read, and a difference from
//! the Python module would either show a frontend a secret Python hides or
//! rewrite a file Python leaves alone. So this is a port, not a redesign, and
//! `kernel/tests/test_session_native.py` compares the two on generated text.
//!
//! Python's patterns use look-around, which the `regex` crate leaves out to
//! stay linear in the input. Each one is rewritten so that it needs none:
//!
//! * "not after a word character" becomes a captured character in front of the
//!   match, put back by the replacement. Nothing is lost by consuming it: every
//!   pattern here ends in a greedy run that already holds any character the
//!   next match could start with.
//! * "followed by `@`" consumes the `@` and puts it back.
//! * the known-token pattern, which looks both ways, is a scanner.
//!
//! Where Python's `re` and the crate disagree about a character, Python's side
//! is spelled out: `\s` there includes U+001C..U+001F, a word character is a
//! letter, a number or `_`, and a case-insensitive `i` also matches U+0130 and
//! U+0131.

use std::collections::HashMap;
use std::sync::LazyLock;

use regex::Regex;

use crate::json::{Object, Value};
use crate::pytext::{self, is_space};

pub const REDACTED: &str = "[REDACTED]";

const SPACE: &str = r"[\s\x1c-\x1f]";
const NO_WORD_BEFORE: &str = r"(^|[^\p{L}\p{N}_])";
const I: &str = r"[i\x{130}\x{131}]";
const CREDENTIAL: &str = r"[A-Za-z0-9._~+/=\x{130}\x{131}-]{8,}";

fn pattern(source: String) -> Regex {
    Regex::new(&source).expect("valid pattern")
}

static PRIVATE_KEY: LazyLock<Regex> = LazyLock::new(|| {
    pattern(format!(
        r"(?is)-----BEG{I}N [^-]*PR{I}VATE KEY-----.*?-----END [^-]*PR{I}VATE KEY-----"
    ))
});
static AUTH_HEADER: LazyLock<Regex> = LazyLock::new(|| {
    pattern(format!(
        r"(?i){NO_WORD_BEFORE}(?:author{I}zat{I}on|proxy-author{I}zat{I}on){SPACE}*[:=]{SPACE}*(?:(?:bas{I}c|bearer|token){SPACE}+)?{CREDENTIAL}"
    ))
});
static COOKIE_HEADER: LazyLock<Regex> = LazyLock::new(|| {
    pattern(format!(
        r#"(?i){NO_WORD_BEFORE}(?:cook{I}e|set-cook{I}e){SPACE}*:{SPACE}*[^ \t\r\n"'\{{\}}=;]+=[^ \t\r\n"'\{{\}};]+(?:{SPACE}*;{SPACE}*[^ \t\r\n"'\{{\}};]+)*"#
    ))
});
static BEARER: LazyLock<Regex> =
    LazyLock::new(|| pattern(format!(r"(?i){NO_WORD_BEFORE}bearer{SPACE}+{CREDENTIAL}")));
static JWT: LazyLock<Regex> = LazyLock::new(|| {
    pattern(r"(^|[^A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}".to_string())
});
static URL_PASSWORD: LazyLock<Regex> = LazyLock::new(|| {
    pattern(r"([A-Za-z][A-Za-z0-9+.-]*://[^/\s\x1c-\x1f:@]+:)[^@\s\x1c-\x1f/]+@".to_string())
});

const SENSITIVE_KEY_SUFFIXES: [&str; 19] = [
    "apikey",
    "accesskey",
    "accesskeyid",
    "secretaccesskey",
    "accesstoken",
    "authtoken",
    "refreshtoken",
    "clientsecret",
    "privatekey",
    "password",
    "passwd",
    "credential",
    "credentials",
    "secret",
    "token",
    "proxyauthorization",
    "authorization",
    "setcookie",
    "cookie",
];
const ASSIGNMENT_TRIGGER_FRAGMENTS: [&str; 8] =
    ["auth", "cookie", "credential", "key", "passwd", "password", "secret", "token"];
const NON_SECRET_SCALARS: [&str; 5] = ["false", "none", "null", "true", "undefined"];
const PLACEHOLDER_VALUES: [&str; 13] = [
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "your_api_key",
    "your_password",
    "your_secret",
    "your_token",
];
const DANGEROUS_FORMAT_CONTROLS: [char; 19] = [
    '\u{61c}', '\u{200b}', '\u{200e}', '\u{200f}', '\u{202a}', '\u{202b}', '\u{202c}', '\u{202d}', '\u{202e}',
    '\u{2060}', '\u{2061}', '\u{2062}', '\u{2063}', '\u{2064}', '\u{2066}', '\u{2067}', '\u{2068}', '\u{2069}',
    '\u{feff}',
];

// --- Known tokens -------------------------------------------------------------

fn is_token_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-'
}

/// One alternative of Python's `_KNOWN_TOKEN_RE`: a prefix, then a run of body bytes.
struct TokenShape {
    prefixes: &'static [&'static str],
    body: fn(u8) -> bool,
    least: usize,
    /// The body is exactly `least` bytes long instead of as long as it runs.
    exact: bool,
}

const fn shape(prefixes: &'static [&'static str], body: fn(u8) -> bool, least: usize) -> TokenShape {
    TokenShape { prefixes, body, least, exact: false }
}

fn alphanumeric(byte: u8) -> bool {
    byte.is_ascii_alphanumeric()
}

// `sk-` needs no entry for its optional `proj-`/`ant-`/... part: those are
// token bytes too, so the run after `sk-` covers them either way.
const TOKEN_SHAPES: [TokenShape; 13] = [
    shape(&["sk-"], is_token_byte, 16),
    shape(&["sk_live_", "sk_test_", "rk_live_", "rk_test_"], alphanumeric, 16),
    shape(&["gsk_"], is_token_byte, 16),
    shape(&["hf_"], alphanumeric, 20),
    shape(&["key-"], is_token_byte, 20),
    shape(&["AIza"], is_token_byte, 20),
    shape(&["github_pat_"], |byte| byte.is_ascii_alphanumeric() || byte == b'_', 20),
    shape(&["gho_", "ghp_", "ghu_", "ghs_", "ghr_"], alphanumeric, 20),
    shape(&["glpat-"], is_token_byte, 16),
    shape(&["xoxb-", "xoxa-", "xoxp-", "xoxr-", "xoxs-"], |byte| byte.is_ascii_alphanumeric() || byte == b'-', 10),
    TokenShape {
        prefixes: &["AKIA", "ASIA"],
        body: |byte| byte.is_ascii_digit() || byte.is_ascii_uppercase(),
        least: 16,
        exact: true,
    },
    shape(&["npm_"], alphanumeric, 20),
    shape(&["pypi-"], is_token_byte, 20),
];

/// Where the known token starting at the head of `rest` ends, if one does.
///
/// A token must be followed by something that is not a token byte. The body
/// run is never retried shorter: every body byte is a token byte, so a shorter
/// run would only be followed by one.
fn known_token_end(rest: &[u8]) -> Option<usize> {
    for shape in &TOKEN_SHAPES {
        let Some(prefix) = shape.prefixes.iter().find(|prefix| rest.starts_with(prefix.as_bytes())) else {
            continue;
        };
        let run = rest[prefix.len()..].iter().take_while(|&&byte| (shape.body)(byte)).count();
        if run < shape.least {
            return None;
        }
        let end = prefix.len() + if shape.exact { shape.least } else { run };
        return rest.get(end).is_none_or(|&byte| !is_token_byte(byte)).then_some(end);
    }
    None
}

fn redact_known_tokens(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut redacted = String::new();
    let mut copied = 0;
    let mut position = 0;
    while position < bytes.len() {
        if !is_token_byte(bytes[position]) {
            position += 1;
            continue;
        }
        // A token starts only where the byte before is not a token byte, so
        // only the head of each run can be one.
        match known_token_end(&bytes[position..]) {
            Some(length) => {
                redacted.push_str(&text[copied..position]);
                redacted.push_str(REDACTED);
                position += length;
                copied = position;
            }
            None => position += bytes[position..].iter().take_while(|&&byte| is_token_byte(byte)).count(),
        }
    }
    if copied == 0 {
        return text.to_string();
    }
    redacted.push_str(&text[copied..]);
    redacted
}

// --- Assignments --------------------------------------------------------------

fn is_sensitive_key(key: &str) -> bool {
    let normalized: String =
        pytext::casefold(key).chars().filter(|c| c.is_ascii_lowercase() || c.is_ascii_digit()).collect();
    SENSITIVE_KEY_SUFFIXES.iter().any(|suffix| normalized.ends_with(suffix))
}

fn looks_like_placeholder(value: &str) -> bool {
    let normalized = pytext::casefold(pytext::strip(value));
    PLACEHOLDER_VALUES.contains(&normalized.as_str())
        || ["${", "{{", "<", "example_", "placeholder_"].iter().any(|prefix| normalized.starts_with(prefix))
        || normalized.ends_with("}}")
        || normalized.ends_with('>')
}

fn is_identifier(text: &str, allow_dots: bool) -> bool {
    let mut characters = text.chars();
    characters.next().is_some_and(|first| first.is_ascii_alphabetic() || first == '_')
        && characters.all(|c| c.is_ascii_alphanumeric() || c == '_' || (allow_dots && c == '.'))
}

fn looks_like_unquoted_secret(value: &str, key: &str, separator: &[char]) -> bool {
    let stripped = pytext::strip(value);
    let lowered = pytext::casefold(stripped);
    if stripped == REDACTED || lowered == pytext::casefold(key.trim_matches(['"', '\''])) {
        return false;
    }
    if NON_SECRET_SCALARS.contains(&lowered.as_str()) || looks_like_placeholder(stripped) {
        return false;
    }
    if pytext::is_decimal(stripped) || stripped.starts_with(['$', '%', '{', '<']) {
        return false;
    }
    if stripped.contains(['(', ')', '[', ']', '{', '}']) {
        return false;
    }
    if stripped.contains('.') && is_identifier(stripped, true) {
        return false;
    }
    let short_word = pytext::length(stripped) < 16 || stripped.contains('_');
    if is_identifier(stripped, false) && separator.iter().copied().any(is_space) && short_word {
        return false;
    }
    pytext::length(stripped) >= 8
}

fn is_key_character(character: char) -> bool {
    character.is_ascii_alphanumeric() || matches!(character, '_' | '.' | '-')
}

fn is_quote(character: char) -> bool {
    character == '"' || character == '\''
}

/// Spans of the sensitive key names, line by line; a line that names nothing
/// credential-like is skipped whole.
fn sensitive_assignment_keys(text: &[char]) -> Vec<(usize, usize)> {
    let mut keys = Vec::new();
    let mut line_start = 0;
    while line_start < text.len() {
        let line_end = text[line_start..]
            .iter()
            .position(|&c| c == '\n')
            .map_or(text.len(), |newline| line_start + newline + 1);
        let folded = pytext::casefold(&text[line_start..line_end].iter().collect::<String>());
        if ASSIGNMENT_TRIGGER_FRAGMENTS.iter().any(|fragment| folded.contains(fragment)) {
            let mut cursor = line_start;
            while cursor < line_end {
                if !is_key_character(text[cursor]) {
                    cursor += 1;
                    continue;
                }
                let start = cursor;
                while cursor < line_end && is_key_character(text[cursor]) {
                    cursor += 1;
                }
                if is_sensitive_key(&text[start..cursor].iter().collect::<String>()) {
                    keys.push((start, cursor));
                }
            }
        }
        line_start = line_end;
    }
    keys
}

fn assignment_value_end(text: &[char], start: usize) -> Option<usize> {
    let first = *text.get(start)?;
    if is_quote(first) {
        let mut cursor = start + 1;
        while cursor < text.len() && !matches!(text[cursor], '\r' | '\n' | '"' | '\'') {
            cursor += 1;
        }
        let closed = cursor < text.len() && text[cursor] == first && cursor - (start + 1) >= 4;
        return closed.then_some(cursor + 1);
    }
    let mut cursor = start;
    while cursor < text.len() && !is_space(text[cursor]) && !matches!(text[cursor], ',' | ';' | '}' | ']' | '&') {
        cursor += 1;
    }
    (cursor - start >= 4).then_some(cursor)
}

/// The span to hide for the value after `key = `, when it reads as a secret.
fn assigned_secret(text: &[char], key: &str, separator_start: usize, value_start: usize) -> Option<(usize, usize)> {
    let value_end = assignment_value_end(text, value_start)?;
    let raw: String = text[value_start..value_end].iter().collect();
    if is_quote(text[value_start]) {
        let value: String = text[value_start + 1..value_end - 1].iter().collect();
        let secret = value != REDACTED && !looks_like_placeholder(&value);
        return secret.then_some((value_start + 1, value_end - 1));
    }
    looks_like_unquoted_secret(&raw, key, &text[separator_start..value_start]).then_some((value_start, value_end))
}

fn skip_spaces(text: &[char], mut cursor: usize) -> usize {
    while cursor < text.len() && is_space(text[cursor]) {
        cursor += 1;
    }
    cursor
}

/// Hide the values of `key = value`, `"key": "value"` and `--key value`.
fn redact_sensitive_assignments(source: &str) -> String {
    let text: Vec<char> = source.chars().collect();
    let mut spans: Vec<(usize, usize)> = Vec::new();
    let mut covered_until = 0;
    for (key_start, key_end) in sensitive_assignment_keys(&text) {
        if key_start < covered_until {
            continue;
        }
        let key: String = text[key_start..key_end].iter().collect();
        let mut cursor = key_end;
        if cursor < text.len() && is_quote(text[cursor]) {
            cursor += 1;
        }
        let separator_start = cursor;
        cursor = skip_spaces(&text, cursor);
        if cursor < text.len() && matches!(text[cursor], ':' | '=') {
            let value_start = skip_spaces(&text, cursor + 1);
            if let Some(span) = assigned_secret(&text, &key, separator_start, value_start) {
                spans.push(span);
                // The closing quote of a quoted value is covered too.
                covered_until = span.1 + usize::from(is_quote(text[value_start]));
            }
            continue;
        }
        let is_flag = key.starts_with('-') && (key_start == 0 || is_space(text[key_start - 1]));
        if !is_flag || cursor >= text.len() || cursor <= separator_start {
            continue;
        }
        let value_start = cursor;
        while cursor < text.len() && !is_space(text[cursor]) {
            cursor += 1;
        }
        spans.push((value_start, cursor));
        covered_until = cursor;
    }
    if spans.is_empty() {
        return source.to_string();
    }
    let mut redacted = String::new();
    let mut cursor = 0;
    for (start, end) in spans {
        if start < cursor {
            continue;
        }
        redacted.extend(&text[cursor..start]);
        redacted.push_str(REDACTED);
        cursor = end;
    }
    redacted.extend(&text[cursor..]);
    redacted
}

// --- Entry points -------------------------------------------------------------

/// `text` with common credential forms replaced.
pub fn redact_text(text: &str) -> String {
    let folded = pytext::casefold(text);
    let mut redacted = text.to_string();
    if folded.contains("private key") {
        redacted = PRIVATE_KEY.replace_all(&redacted, REDACTED).into_owned();
    }
    if folded.contains("authorization") {
        redacted = AUTH_HEADER.replace_all(&redacted, "${1}Authorization: [REDACTED]").into_owned();
    }
    if folded.contains("cookie") {
        redacted = COOKIE_HEADER.replace_all(&redacted, "${1}Cookie: [REDACTED]").into_owned();
    }
    if folded.contains("bearer") {
        redacted = BEARER.replace_all(&redacted, "${1}Bearer [REDACTED]").into_owned();
    }
    if text.contains("://") && text.contains('@') {
        redacted = URL_PASSWORD.replace_all(&redacted, "${1}[REDACTED]@").into_owned();
    }
    redacted = redact_known_tokens(&redacted);
    if text.contains("eyJ") {
        redacted = JWT.replace_all(&redacted, "${1}[REDACTED]").into_owned();
    }
    if ASSIGNMENT_TRIGGER_FRAGMENTS.iter().any(|fragment| folded.contains(fragment)) {
        redacted = redact_sensitive_assignments(&redacted);
    }
    redacted
}

/// Make terminal motion and Unicode formatting controls visible.
fn escape_terminal_controls(text: &str) -> String {
    let mut escaped = String::with_capacity(text.len());
    for character in text.chars() {
        let plain = matches!(character, '\n' | '\t')
            || !(character.is_control() || DANGEROUS_FORMAT_CONTROLS.contains(&character));
        if plain {
            escaped.push(character);
        } else {
            escaped.push_str(&pytext::escape(character));
        }
    }
    escaped
}

/// Redact credentials and neutralize terminal presentation controls.
pub fn sanitize_display_text(text: &str) -> String {
    escape_terminal_controls(&redact_text(text))
}

fn redact_object(object: &Object) -> Object {
    let mut places: HashMap<String, usize> = HashMap::new();
    let mut entries: Vec<(String, Value)> = Vec::new();
    for (key, item) in object.iter() {
        let mut safe_key = redact_text(key);
        if places.contains_key(&safe_key) && safe_key != *key {
            let mut suffix = 2;
            while places.contains_key(&format!("{safe_key}#{suffix}")) {
                suffix += 1;
            }
            safe_key = format!("{safe_key}#{suffix}");
        }
        let value = if is_sensitive_key(key) { Value::from(REDACTED) } else { redact_value(item) };
        match places.get(&safe_key) {
            Some(&index) => entries[index].1 = value,
            None => {
                places.insert(safe_key.clone(), entries.len());
                entries.push((safe_key, value));
            }
        }
    }
    Object::from_pairs(entries)
}

/// Redact a JSON value all the way down, the values of sensitive keys included.
pub fn redact_value(value: &Value) -> Value {
    match value {
        Value::String(text) => Value::String(redact_text(text)),
        Value::Array(items) => Value::Array(items.iter().map(redact_value).collect()),
        Value::Object(object) => Value::Object(redact_object(object)),
        Value::Null | Value::Bool(_) | Value::Number(_) => value.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn headers_and_tokens_are_hidden() {
        assert_eq!(redact_text("Authorization: Bearer abcdefgh12345"), "Authorization: [REDACTED]");
        assert_eq!(redact_text("x-Authorization=abcdefgh12345"), "x-Authorization: [REDACTED]");
        assert_eq!(redact_text("myauthorization: abcdefgh12345"), "myauthorization: abcdefgh12345");
        assert_eq!(redact_text("myauthorization=abcdefgh12345"), "myauthorization=[REDACTED]");
        assert_eq!(redact_text("Cookie: sid=abc123; theme=dark"), "Cookie: [REDACTED]");
        assert_eq!(redact_text("curl -H 'bearer abcdefgh.ijkl'"), "curl -H 'Bearer [REDACTED]'");
        assert_eq!(redact_text("https://user:hunter2@host/path"), "https://user:[REDACTED]@host/path");
        assert_eq!(redact_text("use sk-ant-0123456789abcdefXYZ now"), "use [REDACTED] now");
        assert_eq!(redact_text("AKIAABCDEFGHIJKLMNOP AKIAABCDEFGHIJKLMNOPQ"), "[REDACTED] AKIAABCDEFGHIJKLMNOPQ");
        assert_eq!(redact_text("hf_abcdefghijklmnopqrstuvwx_y"), "hf_abcdefghijklmnopqrstuvwx_y");
        assert_eq!(
            redact_text("jwt eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeK ok"),
            "jwt [REDACTED] ok"
        );
    }

    #[test]
    fn assignments_are_hidden_and_placeholders_are_not() {
        assert_eq!(redact_text("API_KEY=abcd1234efgh"), "API_KEY=[REDACTED]");
        assert_eq!(redact_text("\"password\": \"hunter22\""), "\"password\": \"[REDACTED]\"");
        assert_eq!(redact_text("--token abcdefgh rest"), "--token [REDACTED] rest");
        assert_eq!(redact_text("password = ${PASSWORD}"), "password = ${PASSWORD}");
        assert_eq!(redact_text("token: true"), "token: true");
        assert_eq!(redact_text("pass_word=abcd1234efgh"), "pass_word=abcd1234efgh");
    }

    #[test]
    fn values_of_sensitive_keys_are_replaced() {
        let value = crate::json::parse(r#"{"api_key": {"a": 1}, "note": ["token=abcd1234efgh"]}"#).unwrap();
        assert_eq!(redact_value(&value).to_compact(), r#"{"api_key": "[REDACTED]", "note": ["token=[REDACTED]"]}"#);
    }

    #[test]
    fn display_text_shows_control_characters() {
        assert_eq!(sanitize_display_text("a\x1b[2Jb\u{202e}c\n"), "a\\x1b[2Jb\\u202ec\n");
    }
}
