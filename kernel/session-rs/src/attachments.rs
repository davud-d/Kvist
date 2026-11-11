//! Removing a session's attachments (`AttachmentStore.delete_session` in
//! `kernel/context/attachments.py`): an attachment lives as long as its
//! session, and its index entry stays behind as "deleted".

use std::fs;
use std::path::Path;

use crate::durable;
use crate::error::Result;
use crate::json::{Object, Value};

/// `att_` and sixteen hex digits: the only folder names the store creates.
fn is_attachment_id(id: &str) -> bool {
    id.strip_prefix("att_")
        .is_some_and(|hex| hex.len() == 16 && hex.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')))
}

fn belongs_to(record: &Value, session: &str) -> bool {
    record.as_object().and_then(|fields| fields.get("session")?.as_str()) == Some(session)
}

fn is_live(record: &Value) -> bool {
    !record.as_object().and_then(|fields| fields.get("deleted")).is_some_and(Value::is_truthy)
}

fn remove_files(root: &Path, record: &Value) {
    let Some(id) = record.as_object().and_then(|fields| fields.get("id")?.as_str()) else {
        return;
    };
    // The id is read from the index and becomes a path: anything that is not
    // an attachment id could name a folder outside the store.
    if !is_attachment_id(id) {
        return;
    }
    let folder = root.join(id);
    if fs::symlink_metadata(&folder).is_ok_and(|info| info.is_dir()) {
        // A folder that cannot be removed is left for the age-based cleanup.
        let _ = fs::remove_dir_all(&folder);
    }
}

/// Remove every attachment of `session`; the index keeps a tombstone so a
/// reference to one says "deleted" instead of failing.
pub fn delete_session(workspace: &Path, session: &str) -> Result<()> {
    let root = workspace.join(".kvist").join("attachments");
    let index = root.join("index.json");
    if !index.exists() {
        return Ok(());
    }
    // A damaged index cannot say which files belong to the session; they are
    // left for the age-based cleanup rather than guessed at.
    let Ok(records) = durable::read_json_object(&index, workspace, true) else {
        return Ok(());
    };
    let doomed: Vec<&Value> =
        records.iter().map(|(_, record)| record).filter(|record| belongs_to(record, session) && is_live(record)).collect();
    if doomed.is_empty() {
        return Ok(());
    }
    for record in doomed {
        remove_files(&root, record);
    }
    durable::update_json_object(&index, workspace, true, true, |current| {
        Object::from_pairs(current.iter().map(|(key, record)| (key.clone(), tombstone(record, session))).collect())
    })
}

fn tombstone(record: &Value, session: &str) -> Value {
    let Some(fields) = record.as_object().filter(|_| belongs_to(record, session)) else {
        return record.clone();
    };
    let mut marked = fields.clone();
    marked.set("deleted", Value::Bool(true));
    Value::Object(marked)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_store_made_names_become_paths() {
        assert!(is_attachment_id("att_0123456789abcdef"));
        for id in ["att_0123456789abcdeF", "att_0123", "../x", "att_0123456789abcde/", ""] {
            assert!(!is_attachment_id(id), "{id:?}");
        }
    }
}
