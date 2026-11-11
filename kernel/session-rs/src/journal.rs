//! Reading a session's event journal (`kernel/session/session_events.py`).
//!
//! The journal is written by the running core only; a headless operation reads
//! a page of it, measures it, or removes it with its session.

use std::fs;
use std::io;
use std::path::Path;

use crate::durable;
use crate::error::{OpError, Result};
use crate::json::{self, Object, Value};
use crate::pytext;

pub const JOURNAL_SUFFIX: &str = ".events.jsonl";
pub const DEFAULT_HISTORY_LIMIT: i64 = 500;
pub const MAX_HISTORY_LIMIT: i64 = 5_000;

struct Entry {
    seq: i64,
    turn: i64,
    ts: f64,
    event: Object,
}

fn parse_entry(line: &[u8]) -> Option<Entry> {
    // Python decodes a line as UTF-8 with an optional byte-order mark.
    let text = std::str::from_utf8(line.strip_prefix(b"\xef\xbb\xbf").unwrap_or(line)).ok()?;
    let Value::Object(record) = json::parse(text)? else {
        return None;
    };
    Some(Entry {
        seq: record.get("seq")?.as_int()?,
        turn: record.get("turn")?.as_int()?,
        ts: record.get("ts").and_then(Value::as_float).unwrap_or(0.0),
        event: record.get("event")?.as_object()?.clone(),
    })
}

/// Valid entries in seq order plus the number of lines that were not.
///
/// A crash can leave a torn last line, and a damaged line must cost only
/// itself, never the rest of the journal.
fn parse(raw: &[u8]) -> (Vec<Entry>, usize) {
    let mut entries = Vec::new();
    let mut skipped = 0;
    for line in raw.split(|&byte| byte == b'\n' || byte == b'\r') {
        // What Python's `bytes.strip()` removes; a line of nothing else is not a record.
        if line.iter().all(|byte| matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c)) {
            continue;
        }
        match parse_entry(line) {
            Some(entry) => entries.push(entry),
            None => skipped += 1,
        }
    }
    entries.sort_by_key(|entry| entry.seq);
    (entries, skipped)
}

/// A cursor page of the journal.
///
/// `before` pages backwards and keeps the newest matching events; `after`
/// pages forwards and keeps the oldest, which lets a reconnecting frontend
/// recover every event after its last one.
pub fn read_history(
    path: &Path,
    workspace: &Path,
    limit: usize,
    before: Option<i64>,
    after: Option<i64>,
) -> Result<Object> {
    let raw = durable::read_private_bytes(path, workspace)?.unwrap_or_default();
    let (mut entries, skipped) = parse(&raw);
    let total = entries.len();
    if let Some(before) = before {
        entries.retain(|entry| entry.seq < before);
    }
    if let Some(after) = after {
        entries.retain(|entry| entry.seq > after);
    }
    let has_more = entries.len() > limit;
    let selected = if after.is_some() {
        entries.truncate(limit);
        entries
    } else {
        entries.split_off(entries.len().saturating_sub(limit))
    };
    Ok(page(selected, has_more, total, skipped, raw.len()))
}

fn page(entries: Vec<Entry>, has_more: bool, total: usize, skipped: usize, bytes: usize) -> Object {
    let events = entries
        .into_iter()
        .map(|entry| {
            Value::Object(Object::from_pairs(vec![
                ("seq".to_string(), Value::from(entry.seq)),
                ("turn".to_string(), Value::from(entry.turn)),
                ("ts".to_string(), Value::float(entry.ts)),
                ("event".to_string(), Value::Object(entry.event)),
            ]))
        })
        .collect();
    Object::from_pairs(vec![
        ("events".to_string(), Value::Array(events)),
        ("has_more".to_string(), Value::from(has_more)),
        ("total_events".to_string(), Value::from(total)),
        ("skipped_lines".to_string(), Value::from(skipped)),
        ("journal_bytes".to_string(), Value::from(bytes)),
    ])
}

pub fn journal_size(path: &Path) -> Result<u64> {
    match fs::symlink_metadata(path) {
        Ok(info) => Ok(info.len()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(0),
        Err(_) => {
            let name = path.file_name().map(|name| name.to_string_lossy()).unwrap_or_default();
            Err(OpError::Storage(format!("Cannot inspect journal {}", pytext::repr(&name))))
        }
    }
}

pub fn delete_journal(path: &Path, workspace: &Path) -> Result<bool> {
    // Looked up first: taking the storage lock would create `.kvist/sessions`
    // in a workspace that never had one.
    if fs::symlink_metadata(path).is_err() {
        return Ok(false);
    }
    durable::delete_private_file(path, workspace)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn damaged_lines_cost_only_themselves() {
        let raw = b"{\"seq\":2,\"turn\":1,\"ts\":5,\"event\":{\"type\":\"final\"}}\n\nnot json\r\n{\"seq\":1,\"turn\":1,\"event\":{}}\n{\"seq\":1.0,\"turn\":1,\"event\":{}}\n{\"seq\":3,\"turn\":1,\"event\":{\"type\":\"torn";
        let (entries, skipped) = parse(raw);
        assert_eq!(entries.iter().map(|entry| entry.seq).collect::<Vec<_>>(), [1, 2]);
        assert_eq!(skipped, 3);
        assert_eq!(entries[0].ts, 0.0);
        assert_eq!(entries[1].ts, 5.0);
    }
}
