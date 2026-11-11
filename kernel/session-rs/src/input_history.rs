//! The last inputs of a workspace, kept across restarts for the Up arrow
//! (`kernel/session/input_history.py`): `{"entries": [oldest, ..., newest]}`
//! in `<workspace>/.kvist/input_history.json`.

use std::path::{Path, PathBuf};

use crate::durable;
use crate::json::{Object, Value};
use crate::pytext;

const MAX_ENTRIES: usize = 100;
/// One paste must not turn the file into a megabyte-sized document.
const MAX_ENTRY_CHARS: usize = 20_000;

fn history_path(workspace: &Path) -> PathBuf {
    workspace.join(".kvist").join("input_history.json")
}

fn stored_entries(data: &Object) -> Vec<Value> {
    let stored = data.get("entries").and_then(Value::as_array).unwrap_or_default();
    stored.iter().filter(|entry| entry.as_str().is_some()).cloned().collect()
}

fn newest(mut entries: Vec<Value>) -> Vec<Value> {
    entries.split_off(entries.len().saturating_sub(MAX_ENTRIES))
}

/// Oldest first; a missing or unreadable file is an empty history.
pub fn entries(workspace: &Path) -> Vec<Value> {
    // The history is a convenience: a damaged file must not stop the interface.
    durable::read_json_object(&history_path(workspace), workspace, false)
        .map(|data| newest(stored_entries(&data)))
        .unwrap_or_default()
}

/// Append one input; blank text, over-long text and a repeat of the last entry are skipped.
pub fn add(workspace: &Path, text: &str) {
    if pytext::strip(text).is_empty() || pytext::length(text) > MAX_ENTRY_CHARS {
        return;
    }
    // Losing one history entry is harmless next to failing the operation.
    let _ = durable::update_json_object(&history_path(workspace), workspace, false, false, |data| {
        let mut entries = stored_entries(&data);
        if entries.last().and_then(Value::as_str) != Some(text) {
            entries.push(Value::from(text));
        }
        Object::from_pairs(vec![("entries".to_string(), Value::Array(newest(entries)))])
    });
}
