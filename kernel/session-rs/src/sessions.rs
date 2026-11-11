//! Session documents on disk: the storage half of
//! `kernel/session/agent_logging.py` that needs no running agent.

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::attachments;
use crate::config;
use crate::durable;
use crate::error::{OpError, Result};
use crate::journal::{self, JOURNAL_SUFFIX};
use crate::json::{Object, Value};
use crate::pytext;
use crate::redaction;

const SESSION_VERSION: i64 = 1;
/// Prefix of the names Kvist allocates on its own (app launch, `/new`). Only
/// such a session may be dropped automatically when it is left empty; a session
/// the person named is always kept.
const AUTO_SESSION_PREFIX: &str = "chat-";

pub fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

pub fn is_valid_name(name: &str) -> bool {
    let bytes = name.as_bytes();
    let plain = |byte: &u8| byte.is_ascii_lowercase() || byte.is_ascii_digit();
    (1..=64).contains(&bytes.len())
        && plain(&bytes[0])
        && bytes.iter().all(|byte| plain(byte) || matches!(byte, b'_' | b'-'))
}

pub fn invalid_name(name: &str) -> OpError {
    OpError::Invalid(format!("Invalid session name: {}", pytext::repr(name)))
}

fn sessions_folder(workspace: &Path) -> PathBuf {
    workspace.join(".kvist").join("sessions")
}

pub fn state_path(workspace: &Path, name: &str) -> Result<PathBuf> {
    if !is_valid_name(name) {
        return Err(invalid_name(name));
    }
    durable::validate_storage_path(&sessions_folder(workspace).join(format!("{name}.json")), workspace)
}

/// The session's event journal; its lifecycle is the session's own.
pub fn journal_path(workspace: &Path, name: &str) -> Result<PathBuf> {
    if !is_valid_name(name) {
        return Err(invalid_name(name));
    }
    durable::validate_storage_path(&sessions_folder(workspace).join(format!("{name}{JOURNAL_SUFFIX}")), workspace)
}

fn active_session_path(workspace: &Path) -> Result<PathBuf> {
    durable::validate_storage_path(&workspace.join(".kvist").join("active_session.json"), workspace)
}

fn validate_document(data: &Object, session: &str) -> Result<()> {
    let version = match data.get("version") {
        None | Some(Value::Null) => return Ok(()),
        Some(version) => version,
    };
    if version.as_int() != Some(SESSION_VERSION) {
        let detail = match version {
            Value::Number(text) if version.as_int().is_some() => format!(" {text}"),
            _ => " with an invalid type".to_string(),
        };
        return Err(OpError::Corrupt(format!(
            "Unsupported session version{detail} for session {}",
            pytext::repr(session)
        )));
    }
    match data.get("session") {
        None | Some(Value::Null) => Ok(()),
        Some(Value::String(saved)) if saved == session => Ok(()),
        Some(_) => Err(OpError::Corrupt(format!(
            "Session file identity does not match session {}",
            pytext::repr(session)
        ))),
    }
}

/// The resumable checkpoint inside a document, in the current or the original flat layout.
fn checkpoint_of(data: &Object) -> Option<&Object> {
    if let Some(checkpoint) = data.get("checkpoint").and_then(Value::as_object) {
        if checkpoint.get("goal").is_some() {
            return Some(checkpoint);
        }
    }
    data.get("goal").map(|_| data)
}

fn empty_document(session: &str) -> Result<Object> {
    Ok(Object::from_pairs(vec![
        ("version".to_string(), Value::from(SESSION_VERSION)),
        ("session".to_string(), Value::from(session)),
        ("updated_at".to_string(), Value::float(now())),
        ("session_history".to_string(), Value::Array(Vec::new())),
        ("chat_history".to_string(), Value::Array(Vec::new())),
        ("response_language".to_string(), Value::Null),
        ("styles".to_string(), Value::Object(config::saved_styles()?)),
        ("checkpoint".to_string(), Value::Null),
        ("title".to_string(), Value::Null),
        ("title_manual".to_string(), Value::from(false)),
    ]))
}

/// A session's document, validated and redacted; an empty object when the
/// session does not exist. A document that still held a secret is rewritten
/// without it.
pub fn read_document(workspace: &Path, session: &str) -> Result<Object> {
    let path = state_path(workspace, session)?;
    let data = durable::read_json_object(&path, workspace, false)?;
    if data.is_empty() {
        return Ok(data);
    }
    validate_document(&data, session)?;
    let Value::Object(sanitized) = redaction::redact_value(&Value::Object(data.clone())) else {
        unreachable!("redacting an object yields an object");
    };
    if sanitized != data {
        durable::write_json_object(&path, &sanitized, workspace)?;
    }
    Ok(sanitized)
}

/// `str(value)` for a checkpoint goal: the text itself, or a readable form of
/// whatever else a damaged checkpoint holds there.
fn python_str(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Null => "None".to_string(),
        Value::Bool(flag) => if *flag { "True" } else { "False" }.to_string(),
        other => other.to_compact(),
    }
}

/// The resumable checkpoint of a session as `{"goal", "step", "progress"}`,
/// without loading it into any run state.
pub fn peek_checkpoint(workspace: &Path, session: &str) -> Result<Value> {
    let data = durable::read_json_object(&state_path(workspace, session)?, workspace, false)?;
    if data.is_empty() {
        return Ok(Value::Null);
    }
    validate_document(&data, session)?;
    let Some(checkpoint) = checkpoint_of(&data) else {
        return Ok(Value::Null);
    };
    let field = |key: &str, missing: i64| checkpoint.get(key).cloned().unwrap_or(Value::from(missing));
    Ok(Value::Object(Object::from_pairs(vec![
        ("goal".to_string(), Value::String(checkpoint.get("goal").map(python_str).unwrap_or_default())),
        ("step".to_string(), field("step", 1)),
        ("progress".to_string(), field("progress", 0)),
    ])))
}

/// Whether `document` holds a checkpoint a person may resume.
pub fn is_resumable(document: &Object) -> bool {
    !document.is_empty()
        && checkpoint_of(document).is_some()
        && !document.get("closed").is_some_and(Value::is_true)
        // A helper's service session is continued by its main agent, never by /resume.
        && document.get("service").and_then(Value::as_object).is_none()
}

fn stem(file_name: &str) -> &str {
    file_name.strip_suffix(".json").unwrap_or(file_name)
}

/// The names of the sessions that exist, sorted.
pub fn list(workspace: &Path) -> Result<Vec<String>> {
    let folder = durable::validate_storage_directory(&sessions_folder(workspace), workspace)?;
    if !folder.exists() {
        return Ok(Vec::new());
    }
    let mut sessions: Vec<String> = durable::list_json_object_entries(&folder, workspace)?
        .into_iter()
        .filter(|entry| entry.size > 0 && is_valid_name(stem(&entry.name)))
        .map(|entry| stem(&entry.name).to_string())
        .collect();
    sessions.sort();
    Ok(sessions)
}

pub fn save_active(workspace: &Path, session: &str) -> Result<()> {
    if !is_valid_name(session) {
        return Err(invalid_name(session));
    }
    let marker = Object::from_pairs(vec![
        ("session".to_string(), Value::from(session)),
        ("updated_at".to_string(), Value::float(now())),
    ]);
    durable::write_json_object(&active_session_path(workspace)?, &marker, workspace)
}

/// The most recently written session a person can talk in, when the marker names none.
fn newest_usable(workspace: &Path) -> Result<Option<String>> {
    let folder = durable::validate_storage_directory(&sessions_folder(workspace), workspace)?;
    if !folder.exists() {
        return Ok(None);
    }
    let mut candidates: Vec<durable::Entry> = durable::list_json_object_entries(&folder, workspace)?
        .into_iter()
        .filter(|entry| is_valid_name(stem(&entry.name)))
        .collect();
    candidates.sort_by_key(|entry| std::cmp::Reverse(entry.modified_ns));
    for candidate in candidates {
        let name = stem(&candidate.name);
        let checked = durable::read_json_object(&folder.join(&candidate.name), workspace, false)
            .and_then(|data| validate_document(&data, name).map(|()| data));
        let data = match checked {
            Ok(data) => data,
            Err(error) if error.is_corrupt() => continue,
            Err(error) => return Err(error),
        };
        // A helper's service session is never the person's conversation.
        if data.get("service").and_then(Value::as_object).is_none() {
            return Ok(Some(name.to_string()));
        }
    }
    Ok(None)
}

/// The session the workspace is in; a damaged marker is reported in `warnings`.
pub fn load_active(workspace: &Path, warnings: &mut Vec<String>) -> Result<String> {
    let marker = match durable::read_json_object(&active_session_path(workspace)?, workspace, false) {
        Ok(marker) => marker,
        Err(error) if error.is_corrupt() => {
            warnings.push(
                "The active-session marker was damaged; Kvist selected the most recent usable session.".to_string(),
            );
            Object::new()
        }
        Err(error) => return Err(error),
    };
    if let Some(saved) = marker.get("session").and_then(Value::as_str).filter(|name| is_valid_name(name)) {
        if saved == "default" || state_path(workspace, saved)?.is_file() {
            return Ok(saved.to_string());
        }
    }
    Ok(newest_usable(workspace)?.unwrap_or_else(|| "default".to_string()))
}

/// Create an empty session; `false` when one of that name already exists.
pub fn create(workspace: &Path, session: &str) -> Result<bool> {
    let path = state_path(workspace, session)?;
    let created = durable::create_json_object(&path, &empty_document(session)?, workspace)?;
    if created {
        // A journal left behind by a session of the same name must not be
        // replayed into this one.
        journal::delete_journal(&journal_path(workspace, session)?, workspace)?;
    }
    Ok(created)
}

/// Remove a session with its journal and attachments; `false` when it did not exist.
pub fn delete(workspace: &Path, name: &str, current: &str) -> Result<bool> {
    if name == current {
        return Err(OpError::Invalid(format!("Cannot delete the active session {}", pytext::repr(name))));
    }
    let removed = durable::delete_json_object(&state_path(workspace, name)?, workspace)?;
    journal::delete_journal(&journal_path(workspace, name)?, workspace)?;
    attachments::delete_session(workspace, name)?;
    if removed {
        // Re-assert the marker so a stale marker pointing at the deleted
        // session can never resurrect it on the next start.
        save_active(workspace, current)?;
    }
    Ok(removed)
}

/// Durably reset a session to an empty document: history, chat and checkpoint
/// are gone, the name is kept. A session end_session closed is refused,
/// because emptying it would reopen it.
pub fn reset(workspace: &Path, session: &str) -> Result<()> {
    if read_document(workspace, session)?.get("closed").is_some_and(Value::is_true) {
        return Err(OpError::Invalid(format!(
            "Session {} is closed and cannot be reset; start a new one.",
            pytext::repr(session)
        )));
    }
    // The journal goes first: a crash in between must leave the conversation
    // without its display log, never a reset session that still shows its old feed.
    journal::delete_journal(&journal_path(workspace, session)?, workspace)?;
    attachments::delete_session(workspace, session)?;
    durable::write_json_object(&state_path(workspace, session)?, &empty_document(session)?, workspace)?;
    save_active(workspace, session)
}

/// Allocate an unused, timestamped name for an auto-created session.
pub fn generate_name(workspace: &Path) -> Result<String> {
    let base = format!("{AUTO_SESSION_PREFIX}{}", crate::sys::local_timestamp());
    let taken = list(workspace)?;
    if !taken.contains(&base) {
        return Ok(base);
    }
    (2..1000)
        .map(|suffix| format!("{base}-{suffix}"))
        .find(|candidate| !taken.contains(candidate))
        .ok_or_else(|| OpError::Invalid("Could not allocate a new session name".to_string()))
}

/// True when a session holds no history, no chat, no checkpoint and no journal.
///
/// A damaged session counts as non-empty: it may still hold the person's work,
/// and destroying what cannot be read is never the safe answer.
fn is_empty(workspace: &Path, session: &str) -> Result<bool> {
    let read = read_document(workspace, session)
        .and_then(|document| Ok((document, journal::journal_size(&journal_path(workspace, session)?)?)));
    let (document, recorded) = match read {
        Ok(read) => read,
        Err(error) if error.is_storage() => return Ok(false),
        Err(error) => return Err(error),
    };
    let holds = |key: &str| document.get(key).is_some_and(Value::is_truthy);
    Ok(recorded == 0 && !(holds("session_history") || holds("chat_history") || holds("checkpoint")))
}

/// Drop an auto-created session that was left without any content.
pub fn discard_if_empty(workspace: &Path, session: &str, current: &str) -> Result<()> {
    if !session.starts_with(AUTO_SESSION_PREFIX) || session == current || !is_empty(workspace, session)? {
        return Ok(());
    }
    delete(workspace, session, current).map(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json;

    fn object(text: &str) -> Object {
        match json::parse(text) {
            Some(Value::Object(object)) => object,
            other => panic!("not an object: {other:?}"),
        }
    }

    #[test]
    fn names_follow_the_shared_rule() {
        assert!(is_valid_name("default") && is_valid_name("chat-20261111-101500-2") && is_valid_name("0_a"));
        for name in ["", "-a", "_a", "A", "a.b", "a b", "сессия", &"a".repeat(65)] {
            assert!(!is_valid_name(name), "{name:?}");
        }
    }

    #[test]
    fn version_and_identity_are_checked() {
        assert!(validate_document(&object(r#"{"session": "other"}"#), "s").is_ok());
        assert!(validate_document(&object(r#"{"version": 1, "session": "s"}"#), "s").is_ok());
        let message = |text: &str| validate_document(&object(text), "s").unwrap_err().message().to_string();
        assert_eq!(message(r#"{"version": 2}"#), "Unsupported session version 2 for session 's'");
        assert_eq!(message(r#"{"version": 1.0}"#), "Unsupported session version with an invalid type for session 's'");
        assert_eq!(message(r#"{"version": true}"#), "Unsupported session version with an invalid type for session 's'");
        assert_eq!(message(r#"{"version": 1, "session": "x"}"#), "Session file identity does not match session 's'");
    }

    #[test]
    fn the_flat_checkpoint_layout_still_counts() {
        assert!(checkpoint_of(&object(r#"{"checkpoint": {"goal": "g"}}"#)).is_some());
        assert!(checkpoint_of(&object(r#"{"goal": "g", "step": 3}"#)).is_some());
        assert!(checkpoint_of(&object(r#"{"checkpoint": {"step": 3}}"#)).is_none());
        assert!(checkpoint_of(&object(r#"{"checkpoint": null}"#)).is_none());
    }
}
