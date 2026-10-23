// Turns the core's JSON events (docs/UI_API.md §7) into `CoreEvent`s.
// Events this interface does not use are dropped, as §4 requires.

use serde_json::Value;

use crate::model::{CoreEvent, HelperState, ToolKind};

/// Steps that are the turn's ending rather than work; their words arrive
/// with `final`.
const ENDINGS: [&str; 4] = ["final_answer", "report_to_user", "refuse", "end_session"];

pub fn translate(event: &Value) -> Vec<CoreEvent> {
    let text = |key: &str| event.get(key).and_then(Value::as_str).unwrap_or("").to_string();
    match event.get("type").and_then(Value::as_str).unwrap_or("") {
        "wake" => vec![CoreEvent::TurnStarted],
        // Only a journal has these; a wake-up turn's request has no text.
        "user" if !text("text").is_empty() => vec![CoreEvent::User(text("text"))],
        "reasoning_start" => vec![CoreEvent::Thinking],
        "reasoning_commit" => vec![CoreEvent::ThinkingDone],
        "stream_chunk" | "stream_commit" => vec![CoreEvent::Stream { id: text("stream_id"), text: text("text") }],
        "plan" => vec![CoreEvent::Say(text("markdown"))],
        "message" => vec![CoreEvent::Say(text("markdown"))],
        "final" => vec![CoreEvent::Say(text("message"))],
        "step" => step(event),
        "action_result" => result(event),
        "error" => vec![CoreEvent::Error(text("message"))],
        "turn_end" => vec![CoreEvent::TurnEnd],
        "helper" => vec![CoreEvent::Helper {
            id: text("agent_id"),
            task: text("task"),
            state: helper_state(&text("state")),
            summary: text("summary"),
        }],
        "helper_activity" => helper_activity(event),
        "screen" => match text("state").as_str() {
            "closed" | "unavailable" => vec![CoreEvent::ScreenClosed],
            _ if text("frame_url").is_empty() => Vec::new(),
            _ => vec![CoreEvent::Screen { title: text("title"), frame_url: text("frame_url") }],
        },
        "image_job" => match text("state").as_str() {
            "generating" => vec![CoreEvent::ImageDrawing { title: text("name") }],
            "failed" => vec![CoreEvent::ImageFailed { error: text("error") }],
            _ => Vec::new(),
        },
        // A picture the agent saved (generate_image, or a skill's image): shown
        // in the browser's place on the right.
        "artifact" if text("mime").starts_with("image/") && !text("path").is_empty() => {
            vec![CoreEvent::Image { title: text("name"), path: text("path") }]
        }
        "session_ended" => vec![CoreEvent::SessionEnded { notice: text("closed_notice") }],
        "session" if event.get("closed").and_then(Value::as_bool) == Some(true) => {
            vec![CoreEvent::SessionEnded { notice: text("closed_notice") }]
        }
        _ => Vec::new(),
    }
}

fn step(event: &Value) -> Vec<CoreEvent> {
    let action = event.get("action_type").and_then(Value::as_str).unwrap_or("");
    let words = event.get("user_message").and_then(Value::as_str).unwrap_or("");
    let mut out = Vec::new();
    if !words.trim().is_empty() {
        out.push(CoreEvent::Say(words.to_string()));
    }
    if ENDINGS.contains(&action) {
        return out;
    }
    out.push(CoreEvent::Step { call: call_id(event), kind: kind(action), label: label(event, action) });
    out
}

/// A call's result; a successful write also changes its file's counts.
fn result(event: &Value) -> Vec<CoreEvent> {
    let ok = event.get("ok").and_then(Value::as_bool).unwrap_or(false);
    let summary = event.get("summary").and_then(Value::as_str).unwrap_or("").to_string();
    let diff = line_counts(event).filter(|_| ok);
    let mut out = vec![CoreEvent::Result { call: call_id(event), ok, summary, diff }];
    let path = event.get("path").and_then(Value::as_str).filter(|p| !p.is_empty());
    if let (Some((added, removed)), Some(path)) = (diff, path) {
        let patch = event.get("patch").and_then(Value::as_str).filter(|p| !p.is_empty()).map(str::to_string);
        out.push(CoreEvent::FileChanged { path: path.to_string(), added, removed, patch });
    }
    out
}

/// The core's line diff of a write (`lines_added`, `lines_removed`).
fn line_counts(event: &Value) -> Option<(u32, u32)> {
    let count = |key: &str| event.get(key).and_then(Value::as_u64).map(|n| n as u32);
    Some((count("lines_added")?, count("lines_removed")?))
}

fn helper_activity(event: &Value) -> Vec<CoreEvent> {
    let text = |key: &str| event.get(key).and_then(Value::as_str).unwrap_or("").to_string();
    let action = text("action_type");
    let id = text("agent_id");
    let call = text("call_id");
    match text("phase").as_str() {
        "step" => vec![CoreEvent::HelperStep { id, call, kind: kind(&action), label: label(event, &action) }],
        "result" => vec![CoreEvent::HelperResult {
            id,
            call,
            ok: event.get("ok").and_then(Value::as_bool).unwrap_or(false),
            summary: text("summary"),
            output: text("output"),
        }],
        _ => Vec::new(),
    }
}

/// What a call says it does, or its action's name when it says nothing.
fn label(event: &Value, action: &str) -> String {
    ["action_description", "label"]
        .iter()
        .filter_map(|key| event.get(*key).and_then(Value::as_str))
        .find(|text| !text.trim().is_empty())
        .unwrap_or(action)
        .to_string()
}

/// A call's id, or its step number for a core that sends none.
fn call_id(event: &Value) -> String {
    match event.get("call_id").and_then(Value::as_str).filter(|id| !id.is_empty()) {
        Some(id) => id.to_string(),
        None => format!("step-{}", event.get("step").and_then(Value::as_i64).unwrap_or(0)),
    }
}

fn kind(action: &str) -> ToolKind {
    match action {
        "read_file" | "read_multiple_files" | "list_dir" | "list_tree" | "find_files" | "git_status" | "git_diff" => {
            ToolKind::Read
        }
        "search_code" | "web_search" => ToolKind::Search,
        "write_file" | "append_file" | "replace_in_file" => ToolKind::Write,
        "run_shell" | "job_output" | "job_stop" => ToolKind::Shell,
        "fetch_url" => ToolKind::Web,
        _ => ToolKind::Other,
    }
}

fn helper_state(state: &str) -> HelperState {
    match state {
        "done" => HelperState::Done,
        "failed" | "stopped" => HelperState::Failed,
        _ => HelperState::Running,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_write_carries_the_cores_line_counts_to_its_file() {
        let step = json!({"type": "step", "step": 3, "call_id": "c3", "action_type": "replace_in_file",
            "action_description": "edits a.py", "user_message": ""});
        assert_eq!(translate(&step), vec![CoreEvent::Step { call: "c3".into(), kind: ToolKind::Write, label: "edits a.py".into() }]);
        let result = json!({"type": "action_result", "call_id": "c3", "ok": true, "summary": "Replaced",
            "path": "a.py", "lines_added": 3, "lines_removed": 2, "patch": "@@ -1 +1 @@\n-x\n+y"});
        assert_eq!(
            translate(&result),
            vec![
                CoreEvent::Result { call: "c3".into(), ok: true, summary: "Replaced".into(), diff: Some((3, 2)) },
                CoreEvent::FileChanged { path: "a.py".into(), added: 3, removed: 2, patch: Some("@@ -1 +1 @@\n-x\n+y".into()) },
            ]
        );
    }

    #[test]
    fn a_journal_from_before_patches_still_changes_its_files() {
        let result = json!({"type": "action_result", "call_id": "c3", "ok": true, "summary": "Replaced",
            "path": "a.py", "lines_added": 1, "lines_removed": 0, "patch": null});
        assert_eq!(translate(&result)[1], CoreEvent::FileChanged { path: "a.py".into(), added: 1, removed: 0, patch: None });
    }

    #[test]
    fn a_failed_write_or_a_read_changes_no_file() {
        let failed = json!({"type": "action_result", "call_id": "c1", "ok": false, "summary": "denied", "path": "b",
            "lines_added": null, "lines_removed": null});
        assert_eq!(translate(&failed), vec![CoreEvent::Result { call: "c1".into(), ok: false, summary: "denied".into(), diff: None }]);
        let read = json!({"type": "action_result", "call_id": "c2", "ok": true, "summary": "12 lines", "path": "b"});
        assert_eq!(translate(&read).len(), 1);
    }

    #[test]
    fn the_models_words_come_before_its_call_and_endings_are_not_calls() {
        let out = translate(&json!({"type": "step", "call_id": "c", "action_type": "final_answer", "user_message": "Готово"}));
        assert_eq!(out, vec![CoreEvent::Say("Готово".into())]);
        let out = translate(&json!({"type": "step", "step": 2, "action_type": "run_shell", "user_message": "Запускаю", "action_description": ""}));
        assert_eq!(out, vec![CoreEvent::Say("Запускаю".into()), CoreEvent::Step { call: "step-2".into(), kind: ToolKind::Shell, label: "run_shell".into() }]);
    }

    #[test]
    fn a_helpers_calls_arrive_as_they_start_and_end() {
        let step = json!({"type": "helper_activity", "agent_id": "h1", "phase": "step", "call_id": "x",
            "action_type": "run_shell", "label": "runs the tests"});
        assert_eq!(translate(&step), vec![CoreEvent::HelperStep { id: "h1".into(), call: "x".into(), kind: ToolKind::Shell, label: "runs the tests".into() }]);
        let done = json!({"type": "helper_activity", "agent_id": "h1", "phase": "result", "call_id": "x",
            "action_type": "run_shell", "ok": true, "summary": "exit 0", "output": "48 passed"});
        assert_eq!(translate(&done), vec![CoreEvent::HelperResult { id: "h1".into(), call: "x".into(), ok: true, summary: "exit 0".into(), output: "48 passed".into() }]);
    }

    #[test]
    fn a_picture_being_drawn_or_failed_is_reported() {
        let drawing = json!({"type": "image_job", "state": "generating", "name": "fox", "image_id": "img_1"});
        assert_eq!(translate(&drawing), vec![CoreEvent::ImageDrawing { title: "fox".into() }]);
        let failed = json!({"type": "image_job", "state": "failed", "name": "fox", "error": "no key"});
        assert_eq!(translate(&failed), vec![CoreEvent::ImageFailed { error: "no key".into() }]);
    }

    #[test]
    fn a_saved_picture_goes_to_the_card_and_other_files_do_not() {
        let picture = json!({"type": "artifact", "name": "fox.png", "mime": "image/png", "path": "/w/fox.png"});
        assert_eq!(translate(&picture), vec![CoreEvent::Image { title: "fox.png".into(), path: "/w/fox.png".into() }]);
        assert!(translate(&json!({"type": "artifact", "name": "a.pdf", "mime": "application/pdf", "path": "/w/a.pdf"})).is_empty());
    }

    #[test]
    fn a_closed_screen_or_session_is_reported() {
        assert_eq!(translate(&json!({"type": "screen", "state": "closed", "frame_url": ""})), vec![CoreEvent::ScreenClosed]);
        assert_eq!(
            translate(&json!({"type": "screen", "state": "running", "title": "Browser", "frame_url": "http://127.0.0.1:1/f"})),
            vec![CoreEvent::Screen { title: "Browser".into(), frame_url: "http://127.0.0.1:1/f".into() }]
        );
        assert_eq!(
            translate(&json!({"type": "session", "session": "s", "closed": true, "closed_notice": "Закрыт"})),
            vec![CoreEvent::SessionEnded { notice: "Закрыт".into() }]
        );
        assert!(translate(&json!({"type": "session", "session": "s"})).is_empty());
    }

    #[test]
    fn a_journaled_request_is_shown_unless_it_is_a_wake_up() {
        assert_eq!(translate(&json!({"type": "user", "text": "fix it"})), vec![CoreEvent::User("fix it".into())]);
        assert!(translate(&json!({"type": "user", "text": "", "woken": true})).is_empty());
    }

    #[test]
    fn unknown_events_are_ignored() {
        assert!(translate(&json!({"type": "something_new", "x": 1})).is_empty());
    }
}
