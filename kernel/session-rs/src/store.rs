//! The session operations themselves (`kernel/session/session_store.py`).
//!
//! Semantics match the Python store exactly: `create` creates AND activates a
//! named empty session, `new` and `launch` do the same with a name Kvist
//! allocates, `switch` activates an existing session and fails on a missing or
//! damaged one, `delete` refuses the active session, and `reset` empties the
//! active session in place, except one end_session closed.

use std::path::{Path, PathBuf};

use crate::config;
use crate::durable;
use crate::error::{OpError, Result};
use crate::input_history;
use crate::journal::{self, DEFAULT_HISTORY_LIMIT, MAX_HISTORY_LIMIT};
use crate::json::{Object, Value};
use crate::pytext;
use crate::sessions;

pub const SESSION_OPS: [&str; 11] = [
    "status",
    "list",
    "history",
    "create",
    "switch",
    "delete",
    "reset",
    "new",
    "launch",
    "input-history",
    "input-history-add",
];

/// What a title falls back to is cut to this many characters at a word boundary.
const FALLBACK_TITLE_CHARS: usize = 40;

/// One request, as the command line states it.
#[derive(Default)]
pub struct Request {
    pub workspace: Option<String>,
    pub op: Option<String>,
    pub name: Option<String>,
    pub limit: Option<i64>,
    pub before: Option<i64>,
    pub after: Option<i64>,
}

fn unknown_session(name: &str) -> OpError {
    OpError::Invalid(format!("Unknown session: {}", pytext::repr(name)))
}

fn resolve_workspace(raw: &str) -> Result<PathBuf> {
    let expanded = config::expand_user(Path::new(raw));
    durable::canonical(&expanded)
}

// --- Titles and notices -------------------------------------------------------

fn fallback_title(text: &str) -> String {
    let collapsed = pytext::split(text).collect::<Vec<_>>().join(" ");
    if pytext::length(&collapsed) <= FALLBACK_TITLE_CHARS {
        return collapsed;
    }
    let cut: String = collapsed.chars().take(FALLBACK_TITLE_CHARS).collect();
    let kept = match cut.rfind(' ') {
        Some(boundary) if boundary > 0 => &cut[..boundary],
        _ => cut.as_str(),
    };
    pytext::strip(kept).to_string()
}

fn text_of<'a>(fields: &'a Object, key: &str) -> Option<&'a str> {
    fields.get(key)?.as_str().filter(|text| !pytext::strip(text).is_empty())
}

/// The title to show for a session: stored, derived from its first message or
/// its checkpoint, or the storage name.
fn display_title(document: &Object, name: &str) -> String {
    if let Some(title) = text_of(document, "title") {
        return pytext::strip(title).to_string();
    }
    let history = document.get("session_history").and_then(Value::as_array).unwrap_or_default();
    if let Some(goal) = history.first().and_then(Value::as_object).and_then(|first| text_of(first, "goal")) {
        return fallback_title(goal);
    }
    let chat = document.get("chat_history").and_then(Value::as_array).unwrap_or_default();
    let from_person = |message: &&Object| message.get("role").and_then(Value::as_str) == Some("user");
    if let Some(content) = chat.iter().filter_map(Value::as_object).filter(from_person).find_map(|m| text_of(m, "content")) {
        return fallback_title(content);
    }
    match document.get("checkpoint").and_then(Value::as_object).and_then(|checkpoint| text_of(checkpoint, "goal")) {
        Some(goal) => fallback_title(goal),
        None => name.to_string(),
    }
}

/// `closed`/`closed_notice` for a session end_session closed, or nothing.
fn notice_fields(document: &Object) -> Vec<(String, Value)> {
    if !document.get("closed").is_some_and(Value::is_true) {
        return Vec::new();
    }
    let russian = document.get("response_language").and_then(Value::as_str) == Some("ru");
    let notice =
        if russian { "разговор окончен · Enter — новая сессия" } else { "conversation over · Enter — new session" };
    vec![("closed".to_string(), Value::Bool(true)), ("closed_notice".to_string(), Value::from(notice))]
}

fn list_or_empty(document: &Object, key: &str) -> Value {
    match document.get(key) {
        Some(Value::Array(items)) => Value::Array(items.clone()),
        _ => Value::Array(Vec::new()),
    }
}

// --- The store ----------------------------------------------------------------

pub struct Store {
    workspace: PathBuf,
    current: String,
    warnings: Vec<String>,
}

impl Store {
    pub fn open(workspace: &str) -> Result<Store> {
        let workspace = resolve_workspace(workspace)?;
        let mut warnings = Vec::new();
        let current = sessions::load_active(&workspace, &mut warnings)?;
        Ok(Store { workspace, current, warnings })
    }

    fn journal_bytes(&mut self, name: &str) -> u64 {
        let size = sessions::journal_path(&self.workspace, name).and_then(|path| journal::journal_size(&path));
        size.unwrap_or_else(|_| {
            self.warnings.push(format!("The event journal of session {} is unreadable.", pytext::repr(name)));
            0
        })
    }

    fn session_item(&mut self, name: &str, document: &Object, with_histories: bool) -> (f64, Value) {
        let updated_at = document.get("updated_at").and_then(Value::as_float).unwrap_or(0.0);
        let mut fields = vec![
            ("name".to_string(), Value::from(name)),
            ("title".to_string(), Value::String(display_title(document, name))),
            ("updated_at".to_string(), Value::float(updated_at)),
            ("has_checkpoint".to_string(), Value::Bool(document.get("checkpoint").and_then(Value::as_object).is_some())),
            ("journal_bytes".to_string(), Value::from(self.journal_bytes(name))),
        ];
        fields.extend(notice_fields(document));
        // A helper's service session: readable and stoppable, never a
        // conversation the person types into (frontends group it apart).
        if let Some(service) = document.get("service").filter(|service| service.as_object().is_some()) {
            fields.push(("service".to_string(), service.clone()));
        }
        if with_histories {
            fields.push(("session_history".to_string(), list_or_empty(document, "session_history")));
            fields.push(("chat_history".to_string(), list_or_empty(document, "chat_history")));
        }
        (updated_at, Value::Object(Object::from_pairs(fields)))
    }

    fn overview(&mut self, with_histories: bool) -> Result<Object> {
        let names = sessions::list(&self.workspace)?;
        let mut items: Vec<(f64, Value)> = Vec::new();
        // The newest resumable checkpoint. A launch opens its own session, so
        // an interrupted run's checkpoint is usually not in the active one.
        let mut resumable: Option<(f64, &str)> = None;
        for name in &names {
            let document = match sessions::read_document(&self.workspace, name) {
                Ok(document) => document,
                Err(error) if error.is_corrupt() => {
                    self.warnings
                        .push(format!("Session {} is damaged and was hidden from the session list.", pytext::repr(name)));
                    continue;
                }
                Err(error) => return Err(error),
            };
            let item = self.session_item(name, &document, with_histories);
            if sessions::is_resumable(&document) && resumable.is_none_or(|(stamp, _)| item.0 > stamp) {
                resumable = Some((item.0, name));
            }
            items.push(item);
        }
        // Newest first; sessions written in the same instant keep their name order.
        items.sort_by(|left, right| right.0.total_cmp(&left.0));
        let checkpoint = sessions::peek_checkpoint(&self.workspace, &self.current)?;
        Ok(Object::from_pairs(vec![
            ("active_session".to_string(), Value::from(self.current.as_str())),
            ("sessions".to_string(), Value::Array(names.iter().map(|name| Value::from(name.as_str())).collect())),
            ("session_items".to_string(), Value::Array(items.into_iter().map(|(_, item)| item).collect())),
            ("checkpoint".to_string(), checkpoint),
            ("resumable_session".to_string(), resumable.map_or(Value::Null, |(_, name)| Value::from(name))),
            ("warnings".to_string(), Value::Array(self.warnings.iter().map(|text| Value::from(text.as_str())).collect())),
        ]))
    }

    fn status(&mut self) -> Result<Object> {
        self.overview(true)
    }

    fn history(&mut self, name: &str, limit: i64, before: Option<i64>, after: Option<i64>) -> Result<Object> {
        if !sessions::is_valid_name(name) {
            return Err(sessions::invalid_name(name));
        }
        if !(1..=MAX_HISTORY_LIMIT).contains(&limit) {
            return Err(OpError::Invalid(format!("History limit must be between 1 and {MAX_HISTORY_LIMIT}")));
        }
        if before.is_some_and(|cursor| cursor < 1) {
            return Err(OpError::Invalid("History cursor must be a positive sequence number".to_string()));
        }
        if after.is_some_and(|cursor| cursor < 0) {
            return Err(OpError::Invalid("History cursor must be a non-negative sequence number".to_string()));
        }
        if before.is_some() && after.is_some() {
            return Err(OpError::Invalid("History cursors 'before' and 'after' are mutually exclusive".to_string()));
        }
        let document = sessions::read_document(&self.workspace, name)?;
        if document.is_empty() {
            return Err(unknown_session(name));
        }
        let path = sessions::journal_path(&self.workspace, name)?;
        let page = journal::read_history(&path, &self.workspace, usize::try_from(limit).unwrap_or(1), before, after)?;
        let mut fields = vec![("session".to_string(), Value::from(name))];
        fields.extend(page.iter().cloned());
        fields.extend(notice_fields(&document));
        Ok(Object::from_pairs(fields))
    }

    fn create(&mut self, name: &str) -> Result<Object> {
        if !sessions::is_valid_name(name) {
            return Err(sessions::invalid_name(name));
        }
        if !sessions::create(&self.workspace, name)? {
            return Err(OpError::Invalid(format!("Session {} already exists", pytext::repr(name))));
        }
        sessions::save_active(&self.workspace, name)?;
        self.current = name.to_string();
        self.status()
    }

    /// A headless process has no conversation in memory to save, so a switch
    /// only moves the active marker: the outgoing session was already
    /// persisted by the process that owned it.
    fn switch(&mut self, name: &str) -> Result<Object> {
        if !sessions::is_valid_name(name) {
            return Err(sessions::invalid_name(name));
        }
        if name != self.current {
            if sessions::read_document(&self.workspace, name)?.is_empty() {
                return Err(unknown_session(name));
            }
            sessions::save_active(&self.workspace, name)?;
            self.current = name.to_string();
        }
        self.status()
    }

    fn delete(&mut self, name: &str) -> Result<Object> {
        if !sessions::is_valid_name(name) {
            return Err(sessions::invalid_name(name));
        }
        if !sessions::delete(&self.workspace, name, &self.current)? {
            return Err(unknown_session(name));
        }
        self.status()
    }

    fn reset(&mut self) -> Result<Object> {
        sessions::reset(&self.workspace, &self.current)?;
        self.status()
    }

    /// Create and activate a fresh auto-named session (`new`, `launch`).
    ///
    /// The outgoing session is dropped when Kvist created it automatically and
    /// it was never used, so repeated launches cannot litter the session list
    /// with empty entries. An interrupted run stays reachable through
    /// `resumable_session` in the returned status.
    fn rotate(&mut self) -> Result<Object> {
        let name = sessions::generate_name(&self.workspace)?;
        if !sessions::create(&self.workspace, &name)? {
            return Err(OpError::Invalid(format!("Session {} already exists", pytext::repr(&name))));
        }
        sessions::save_active(&self.workspace, &name)?;
        let previous = std::mem::replace(&mut self.current, name);
        sessions::discard_if_empty(&self.workspace, &previous, &self.current)?;
        self.status()
    }
}

fn succeeded(payload: Object) -> Object {
    let mut fields = vec![("ok".to_string(), Value::Bool(true))];
    fields.extend(payload.iter().cloned());
    Object::from_pairs(fields)
}

/// Execute one session operation and return its JSON payload. `text` is the
/// input `input-history-add` appends to the workspace's input history.
pub fn run(request: &Request, text: impl FnOnce() -> String) -> Result<Object> {
    let op = request.op.as_deref().unwrap_or_default();
    if !SESSION_OPS.contains(&op) {
        return Err(OpError::Invalid(format!("Unknown session operation: {}", pytext::repr(op))));
    }
    let Some(workspace) = request.workspace.as_deref() else {
        return Err(OpError::Invalid("A session operation requires --workspace".to_string()));
    };
    if op == "input-history" {
        let entries = input_history::entries(&resolve_workspace(workspace)?);
        return Ok(succeeded(Object::from_pairs(vec![("entries".to_string(), Value::Array(entries))])));
    }
    if op == "input-history-add" {
        input_history::add(&resolve_workspace(workspace)?, &text());
        return Ok(succeeded(Object::new()));
    }
    let mut store = Store::open(workspace)?;
    let payload = match (op, request.name.as_deref().filter(|name| !name.is_empty())) {
        ("status", _) => store.status(),
        ("list", _) => store.overview(false),
        ("reset", _) => store.reset(),
        ("new" | "launch", _) => store.rotate(),
        (_, None) => {
            return Err(OpError::Invalid(format!("Session operation {} requires a session name", pytext::repr(op))));
        }
        ("history", Some(name)) => {
            store.history(name, request.limit.unwrap_or(DEFAULT_HISTORY_LIMIT), request.before, request.after)
        }
        ("create", Some(name)) => store.create(name),
        ("switch", Some(name)) => store.switch(name),
        (_, Some(name)) => store.delete(name),
    };
    payload.map(succeeded)
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
    fn a_long_first_message_is_cut_at_a_word() {
        assert_eq!(fallback_title("  fix   the\nbug "), "fix the bug");
        assert_eq!(
            fallback_title("rewrite the session storage layer in another language today"),
            "rewrite the session storage layer in"
        );
        assert_eq!(fallback_title(&"x".repeat(60)), "x".repeat(40));
    }

    #[test]
    fn the_title_falls_back_in_python_s_order() {
        let title = |text: &str| display_title(&object(text), "chat-1");
        assert_eq!(title(r#"{"title": " Named ", "session_history": [{"goal": "g"}]}"#), "Named");
        assert_eq!(title(r#"{"title": " ", "session_history": [{"goal": "first goal"}]}"#), "first goal");
        assert_eq!(
            title(r#"{"session_history": [{}], "chat_history": [{"role": "assistant", "content": "a"}, {"role": "user", "content": "asked"}]}"#),
            "asked"
        );
        assert_eq!(title(r#"{"checkpoint": {"goal": "interrupted"}}"#), "interrupted");
        assert_eq!(title(r#"{"checkpoint": null}"#), "chat-1");
    }

    #[test]
    fn a_closed_session_carries_its_notice() {
        assert!(notice_fields(&object(r#"{"closed": 1}"#)).is_empty());
        let fields = notice_fields(&object(r#"{"closed": true, "response_language": "ru"}"#));
        assert_eq!(fields[1].1.as_str(), Some("разговор окончен · Enter — новая сессия"));
    }
}
