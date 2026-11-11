//! JSON as the Python core reads and writes it.
//!
//! The session files are shared with Python, so this module follows Python's
//! `json` rather than the standard: `NaN` and `Infinity` are numbers, a number
//! keeps the exact text it was written with (Python's integers have no size
//! limit), an object keeps its key order and the last of two equal keys wins.
//! One deliberate difference: a `\ud800`-style escape without its pair is
//! refused here, because the string it stands for cannot be printed as UTF-8
//! (Python reads it and then fails when it answers).

use std::collections::HashMap;

#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Null,
    Bool(bool),
    /// The number exactly as written in the source text.
    Number(String),
    String(String),
    Array(Vec<Value>),
    Object(Object),
}

/// A JSON object in insertion order.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Object {
    entries: Vec<(String, Value)>,
}

impl Object {
    pub fn new() -> Object {
        Object::default()
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.entries.iter().find(|(name, _)| name == key).map(|(_, value)| value)
    }

    /// `dict[key] = value`: a known key keeps its place and takes the new value.
    pub fn set(&mut self, key: &str, value: Value) {
        match self.entries.iter_mut().find(|(name, _)| name == key) {
            Some(entry) => entry.1 = value,
            None => self.entries.push((key.to_string(), value)),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn iter(&self) -> impl Iterator<Item = &(String, Value)> {
        self.entries.iter()
    }

    /// Entries in order; equal keys are merged the way `dict` merges them.
    pub fn from_pairs(pairs: Vec<(String, Value)>) -> Object {
        let mut places: HashMap<&str, usize> = HashMap::with_capacity(pairs.len());
        let unique = pairs.iter().enumerate().all(|(index, (key, _))| places.insert(key, index).is_none());
        drop(places);
        if unique {
            return Object { entries: pairs };
        }
        let mut places: HashMap<String, usize> = HashMap::new();
        let mut entries: Vec<(String, Value)> = Vec::new();
        for (key, value) in pairs {
            match places.get(&key) {
                Some(&index) => entries[index].1 = value,
                None => {
                    places.insert(key.clone(), entries.len());
                    entries.push((key, value));
                }
            }
        }
        Object { entries }
    }
}

impl From<&str> for Value {
    fn from(text: &str) -> Value {
        Value::String(text.to_string())
    }
}

impl From<String> for Value {
    fn from(text: String) -> Value {
        Value::String(text)
    }
}

impl From<bool> for Value {
    fn from(flag: bool) -> Value {
        Value::Bool(flag)
    }
}

impl From<u64> for Value {
    fn from(number: u64) -> Value {
        Value::Number(number.to_string())
    }
}

impl From<usize> for Value {
    fn from(number: usize) -> Value {
        Value::Number(number.to_string())
    }
}

impl From<i64> for Value {
    fn from(number: i64) -> Value {
        Value::Number(number.to_string())
    }
}

impl From<Object> for Value {
    fn from(object: Object) -> Value {
        Value::Object(object)
    }
}

impl From<Vec<Value>> for Value {
    fn from(items: Vec<Value>) -> Value {
        Value::Array(items)
    }
}

impl Value {
    /// A float the way Python's `json` prints one.
    pub fn float(number: f64) -> Value {
        Value::Number(float_repr(number))
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Value::String(text) => Some(text),
            _ => None,
        }
    }

    pub fn as_object(&self) -> Option<&Object> {
        match self {
            Value::Object(object) => Some(object),
            _ => None,
        }
    }

    pub fn as_array(&self) -> Option<&[Value]> {
        match self {
            Value::Array(items) => Some(items),
            _ => None,
        }
    }

    /// `value is True`.
    pub fn is_true(&self) -> bool {
        matches!(self, Value::Bool(true))
    }

    /// `type(value) is int`: a number written without fraction or exponent.
    /// `None` also for an integer beyond 64 bits, which nothing Kvist writes reaches.
    pub fn as_int(&self) -> Option<i64> {
        match self {
            Value::Number(text) => text.parse().ok(),
            _ => None,
        }
    }

    /// `float(value) if isinstance(value, (int, float))`; in Python a bool is an int.
    pub fn as_float(&self) -> Option<f64> {
        match self {
            Value::Bool(flag) => Some(f64::from(u8::from(*flag))),
            Value::Number(text) => Some(match text.as_str() {
                "NaN" => f64::NAN,
                "Infinity" => f64::INFINITY,
                "-Infinity" => f64::NEG_INFINITY,
                other => other.parse().unwrap_or(f64::NAN),
            }),
            _ => None,
        }
    }

    /// `bool(value)`.
    pub fn is_truthy(&self) -> bool {
        match self {
            Value::Null => false,
            Value::Bool(flag) => *flag,
            Value::Number(_) => self.as_float() != Some(0.0),
            Value::String(text) => !text.is_empty(),
            Value::Array(items) => !items.is_empty(),
            Value::Object(object) => !object.is_empty(),
        }
    }

    /// `json.dumps(value, ensure_ascii=False)`.
    pub fn to_compact(&self) -> String {
        let mut text = String::new();
        write_value(self, None, 0, &mut text);
        text
    }

    /// `json.dumps(value, ensure_ascii=False, indent=2)`.
    pub fn to_indented(&self) -> String {
        let mut text = String::new();
        write_value(self, Some(2), 0, &mut text);
        text
    }
}

/// `repr(float)`: the shortest text that reads back as the same number, with
/// Python's spelling of the exponent and of the values JSON has no word for.
fn float_repr(number: f64) -> String {
    if number.is_nan() {
        return "NaN".to_string();
    }
    if number.is_infinite() {
        return if number > 0.0 { "Infinity" } else { "-Infinity" }.to_string();
    }
    let text = format!("{number:?}");
    let Some((mantissa, exponent)) = text.split_once('e') else {
        return text;
    };
    let (sign, digits) = match exponent.strip_prefix('-') {
        Some(digits) => ('-', digits),
        None => ('+', exponent),
    };
    format!("{mantissa}e{sign}{digits:0>2}")
}

// --- Writing ------------------------------------------------------------------

fn write_value(value: &Value, indent: Option<usize>, level: usize, text: &mut String) {
    match value {
        Value::Null => text.push_str("null"),
        Value::Bool(flag) => text.push_str(if *flag { "true" } else { "false" }),
        Value::Number(number) => text.push_str(number),
        Value::String(string) => write_string(string, text),
        Value::Array(items) => {
            write_container(items.len(), ('[', ']'), indent, level, text, |index, text| {
                write_value(&items[index], indent, level + 1, text);
            });
        }
        Value::Object(object) => {
            write_container(object.entries.len(), ('{', '}'), indent, level, text, |index, text| {
                let (key, item) = &object.entries[index];
                write_string(key, text);
                text.push_str(": ");
                write_value(item, indent, level + 1, text);
            });
        }
    }
}

fn write_container(
    count: usize,
    brackets: (char, char),
    indent: Option<usize>,
    level: usize,
    text: &mut String,
    mut write_item: impl FnMut(usize, &mut String),
) {
    text.push(brackets.0);
    for index in 0..count {
        if index > 0 {
            text.push_str(if indent.is_some() { "," } else { ", " });
        }
        if let Some(width) = indent {
            text.push('\n');
            text.extend(std::iter::repeat_n(' ', width * (level + 1)));
        }
        write_item(index, text);
    }
    if let (Some(width), true) = (indent, count > 0) {
        text.push('\n');
        text.extend(std::iter::repeat_n(' ', width * level));
    }
    text.push(brackets.1);
}

fn write_string(string: &str, text: &mut String) {
    text.push('"');
    for character in string.chars() {
        match character {
            '"' => text.push_str("\\\""),
            '\\' => text.push_str("\\\\"),
            '\n' => text.push_str("\\n"),
            '\r' => text.push_str("\\r"),
            '\t' => text.push_str("\\t"),
            '\x08' => text.push_str("\\b"),
            '\x0c' => text.push_str("\\f"),
            control if control < ' ' => text.push_str(&format!("\\u{:04x}", u32::from(control))),
            other => text.push(other),
        }
    }
    text.push('"');
}

// --- Reading ------------------------------------------------------------------

// Python's own readers give out near a thousand levels; nothing Kvist writes
// nests deeper than a few dozen, and the limit keeps a hostile file from
// overflowing the stack.
const MAX_DEPTH: usize = 900;

/// `json.loads(text)`; `None` where Python raises.
pub fn parse(text: &str) -> Option<Value> {
    let mut parser = Parser { text, bytes: text.as_bytes(), position: 0, depth: 0 };
    parser.skip_whitespace();
    let value = parser.value()?;
    parser.skip_whitespace();
    (parser.position == parser.bytes.len()).then_some(value)
}

struct Parser<'a> {
    text: &'a str,
    bytes: &'a [u8],
    position: usize,
    depth: usize,
}

impl Parser<'_> {
    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.position).copied()
    }

    fn skip_whitespace(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.position += 1;
        }
    }

    fn word(&mut self, word: &str, value: Value) -> Option<Value> {
        if !self.text[self.position..].starts_with(word) {
            return None;
        }
        self.position += word.len();
        Some(value)
    }

    fn value(&mut self) -> Option<Value> {
        match self.peek()? {
            b'"' => self.string().map(Value::String),
            b'{' => self.nested(Parser::object),
            b'[' => self.nested(Parser::array),
            b'n' => self.word("null", Value::Null),
            b't' => self.word("true", Value::Bool(true)),
            b'f' => self.word("false", Value::Bool(false)),
            b'N' => self.word("NaN", Value::Number("NaN".to_string())),
            b'I' => self.word("Infinity", Value::Number("Infinity".to_string())),
            b'-' | b'0'..=b'9' => self.number(),
            _ => None,
        }
    }

    fn nested(&mut self, read: fn(&mut Self) -> Option<Value>) -> Option<Value> {
        if self.depth == MAX_DEPTH {
            return None;
        }
        self.depth += 1;
        let value = read(self);
        self.depth -= 1;
        value
    }

    fn object(&mut self) -> Option<Value> {
        self.position += 1;
        let mut pairs = Vec::new();
        self.skip_whitespace();
        if self.peek()? == b'}' {
            self.position += 1;
            return Some(Value::Object(Object::new()));
        }
        loop {
            if self.peek()? != b'"' {
                return None;
            }
            let key = self.string()?;
            self.skip_whitespace();
            if self.peek()? != b':' {
                return None;
            }
            self.position += 1;
            self.skip_whitespace();
            pairs.push((key, self.value()?));
            self.skip_whitespace();
            match self.peek()? {
                b',' => self.position += 1,
                b'}' => break,
                _ => return None,
            }
            self.skip_whitespace();
        }
        self.position += 1;
        Some(Value::Object(Object::from_pairs(pairs)))
    }

    fn array(&mut self) -> Option<Value> {
        self.position += 1;
        let mut items = Vec::new();
        self.skip_whitespace();
        if self.peek()? == b']' {
            self.position += 1;
            return Some(Value::Array(items));
        }
        loop {
            items.push(self.value()?);
            self.skip_whitespace();
            match self.peek()? {
                b',' => self.position += 1,
                b']' => break,
                _ => return None,
            }
            self.skip_whitespace();
        }
        self.position += 1;
        Some(Value::Array(items))
    }

    fn digits(&mut self) -> bool {
        let start = self.position;
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.position += 1;
        }
        self.position > start
    }

    fn number(&mut self) -> Option<Value> {
        let start = self.position;
        if self.peek() == Some(b'-') {
            self.position += 1;
        }
        match self.peek() {
            Some(b'0') => self.position += 1,
            Some(b'1'..=b'9') => {
                self.digits();
            }
            _ => {
                self.position = start;
                return self.word("-Infinity", Value::Number("-Infinity".to_string()));
            }
        }
        // A fraction or an exponent counts only when complete; what is left
        // over is then refused by whoever reads on.
        let whole = self.position;
        if self.peek() == Some(b'.') {
            self.position += 1;
            if !self.digits() {
                self.position = whole;
            }
        }
        let mantissa = self.position;
        if matches!(self.peek(), Some(b'e' | b'E')) {
            self.position += 1;
            if matches!(self.peek(), Some(b'+' | b'-')) {
                self.position += 1;
            }
            if !self.digits() {
                self.position = mantissa;
            }
        }
        Some(Value::Number(self.text[start..self.position].to_string()))
    }

    fn string(&mut self) -> Option<String> {
        self.position += 1;
        let mut string = String::new();
        let mut chunk = self.position;
        loop {
            match self.peek()? {
                b'"' => {
                    string.push_str(&self.text[chunk..self.position]);
                    self.position += 1;
                    return Some(string);
                }
                b'\\' => {
                    string.push_str(&self.text[chunk..self.position]);
                    self.position += 1;
                    string.push(self.escape()?);
                    chunk = self.position;
                }
                0..=0x1f => return None,
                _ => self.position += 1,
            }
        }
    }

    fn escape(&mut self) -> Option<char> {
        let kind = self.peek()?;
        self.position += 1;
        Some(match kind {
            b'"' => '"',
            b'\\' => '\\',
            b'/' => '/',
            b'b' => '\x08',
            b'f' => '\x0c',
            b'n' => '\n',
            b'r' => '\r',
            b't' => '\t',
            b'u' => return self.unicode_escape(),
            _ => return None,
        })
    }

    fn hex4(&mut self) -> Option<u32> {
        let digits = self.bytes.get(self.position..self.position + 4)?;
        if !digits.iter().all(u8::is_ascii_hexdigit) {
            return None;
        }
        self.position += 4;
        u32::from_str_radix(&self.text[self.position - 4..self.position], 16).ok()
    }

    fn unicode_escape(&mut self) -> Option<char> {
        let first = self.hex4()?;
        if !(0xd800..=0xdbff).contains(&first) {
            return char::from_u32(first);
        }
        if self.bytes.get(self.position..self.position + 2)? != b"\\u" {
            return None;
        }
        self.position += 2;
        let second = self.hex4()?;
        if !(0xdc00..=0xdfff).contains(&second) {
            return None;
        }
        char::from_u32(0x10000 + ((first - 0xd800) << 10) + (second - 0xdc00))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn object(text: &str) -> Object {
        match parse(text) {
            Some(Value::Object(object)) => object,
            other => panic!("not an object: {other:?}"),
        }
    }

    #[test]
    fn reads_what_python_reads() {
        let document = object(r#" {"a": 1, "b": [true, null, -0.5e+3], "c": NaN, "d": -Infinity, "e": "x\u00e9\ud83d\ude00\n"} "#);
        assert_eq!(document.get("a").and_then(Value::as_int), Some(1));
        assert_eq!(document.get("c"), Some(&Value::Number("NaN".to_string())));
        assert_eq!(document.get("e").and_then(Value::as_str), Some("xé😀\n"));
        assert_eq!(parse("12345678901234567890123").and_then(|v| v.as_float()), Some(1.2345678901234568e22));
    }

    #[test]
    fn refuses_what_python_refuses() {
        for text in ["", "{", "[1,]", "{\"a\":1,}", "01", "1.", "1e", "-", "+1", "\"a\nb\"", "'a'", "{a: 1}", "1 2", "\u{feff}{}", "\"\\x41\"", "nul"] {
            assert_eq!(parse(text), None, "{text:?}");
        }
        assert_eq!(parse(r#""\ud800""#), None);
        assert_eq!(parse(&format!("{}{}", "[".repeat(2000), "]".repeat(2000))), None);
    }

    #[test]
    fn equal_keys_merge_like_a_dict() {
        let document = object(r#"{"a": 1, "b": 2, "a": 3}"#);
        assert_eq!(Value::Object(document).to_compact(), r#"{"a": 3, "b": 2}"#);
    }

    #[test]
    fn integers_and_floats_stay_apart() {
        assert_eq!(parse("1").and_then(|v| v.as_int()), Some(1));
        assert_eq!(parse("1.0").and_then(|v| v.as_int()), None);
        assert_eq!(parse("1e2").and_then(|v| v.as_int()), None);
        assert_eq!(parse("true").and_then(|v| v.as_float()), Some(1.0));
    }

    #[test]
    fn writes_what_python_writes() {
        let value = parse(r#"{"a":[1,{"b":[]},{}],"c":"q\"\\\u0001\u007fé","d":{"e":null}}"#).unwrap();
        assert_eq!(
            value.to_compact(),
            "{\"a\": [1, {\"b\": []}, {}], \"c\": \"q\\\"\\\\\\u0001\u{7f}é\", \"d\": {\"e\": null}}"
        );
        assert_eq!(
            value.to_indented(),
            "{\n  \"a\": [\n    1,\n    {\n      \"b\": []\n    },\n    {}\n  ],\n  \"c\": \"q\\\"\\\\\\u0001\u{7f}é\",\n  \"d\": {\n    \"e\": null\n  }\n}"
        );
    }

    #[test]
    fn floats_print_like_python() {
        let cases = [
            (0.0, "0.0"),
            (1731312345.0, "1731312345.0"),
            (1731312345.1234567, "1731312345.1234567"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.5e-7, "1.5e-07"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1e100, "1e+100"),
            (f64::INFINITY, "Infinity"),
        ];
        for (number, text) in cases {
            assert_eq!(float_repr(number), text);
        }
    }
}
