// Turns the core's JSON events (docs/UI_API.md §7) into `CoreEvent`s.
// Events this interface does not use are dropped, as §4 requires.

use serde_json::Value;

use crate::arena::{
    ArenaDiff, CouncilVerdict, DiffStat, Dissent, ExecState, ExecutorInfo, Finish, Finished, Member, Outcome, Risk, Started,
    Status, TestState, Tests, Usage, Verdict, VerdictRow,
};
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
        // Older cores sent no `append` and a cumulative snapshot: the text then replaces what is shown.
        "reasoning_chunk" => vec![CoreEvent::ThoughtText {
            text: text("text"),
            append: event.get("append").and_then(Value::as_bool).unwrap_or(false),
        }],
        "reasoning_commit" => {
            vec![CoreEvent::ThinkingDone { seconds: event.get("duration_seconds").and_then(Value::as_f64).unwrap_or(0.0) as f32 }]
        }
        // Older cores sent no `append` and a cumulative snapshot; a commit always carries the whole answer.
        "stream_chunk" => vec![CoreEvent::Stream {
            id: text("stream_id"),
            text: text("text"),
            append: event.get("append").and_then(Value::as_bool).unwrap_or(false),
            done: false,
        }],
        "stream_commit" => vec![CoreEvent::Stream { id: text("stream_id"), text: text("text"), append: false, done: true }],
        "plan" => vec![CoreEvent::Say(text("markdown"))],
        "message" => vec![CoreEvent::Say(text("markdown"))],
        "final" => vec![CoreEvent::Say(text("message"))],
        "step" => step(event),
        "action_result" => result(event),
        "call_begin" if !ENDINGS.contains(&text("tool").as_str()) => {
            let tool = text("tool");
            let label = Some(text("description")).filter(|d| !d.trim().is_empty()).unwrap_or_else(|| tool.clone());
            vec![CoreEvent::CallBegin { call: text("call_id"), kind: kind(&tool), label }]
        }
        "call_stream" => vec![CoreEvent::CallStream {
            call: text("call_id"),
            tool: text("tool"),
            path: text("path"),
            field: text("field"),
            line: event.get("line").and_then(Value::as_u64).unwrap_or(0) as u32,
            text: text("text"),
        }],
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
            "closed" | "unavailable" => vec![CoreEvent::ScreenClosed { screen_id: text("screen_id") }],
            _ if text("frame_url").is_empty() => Vec::new(),
            _ => vec![CoreEvent::Screen {
                screen_id: text("screen_id"),
                title: text("title"),
                frame_url: text("frame_url"),
                stream_url: text("stream_url"),
                url: text("url"),
            }],
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
        "usage" => vec![CoreEvent::Spent(usage(event))],
        "tool_mode" if !text("mode").is_empty() => vec![CoreEvent::ToolMode(text("mode"))],
        "bestof_started" => vec![CoreEvent::BestofStarted(started(event))],
        "bestof_status" => vec![CoreEvent::BestofStatus(status(event))],
        "executor_activity" => executor_activity(event),
        "bestof_eliminated" => {
            vec![CoreEvent::BestofEliminated { run_id: text("run_id"), id: text("executor_id"), reason: text("reason") }]
        }
        "bestof_verdict" => vec![CoreEvent::BestofVerdict(verdict(event))],
        "bestof_diff" => vec![CoreEvent::BestofDiff(ArenaDiff {
            run_id: text("run_id"),
            id: text("executor_id"),
            patch: text("patch"),
            files: strings_of(event.get("files")),
        })],
        "bestof_finished" => vec![CoreEvent::BestofFinished(Finished {
            run_id: text("run_id"),
            outcome: Finish::parse(&text("outcome")),
            id: text("executor_id"),
            commit: text("commit"),
            message: text("message"),
        })],
        "council_started" => vec![CoreEvent::CouncilStarted {
            council_id: text("council_id"),
            question: text("question"),
            members: items(event, "members").map(|m| Member { id: field(m, "executor_id"), angle: field(m, "angle") }).collect(),
        }],
        "council_card" => vec![CoreEvent::CouncilCard {
            council_id: text("council_id"),
            id: text("executor_id"),
            angle: text("angle"),
            decision: text("decision"),
            argument: text("argument"),
        }],
        "council_objection" => vec![CoreEvent::CouncilObjection {
            council_id: text("council_id"),
            id: text("executor_id"),
            angle: text("angle"),
            risks: items(event, "risks").map(|r| Risk { target: field(r, "target"), risk: field(r, "risk") }).collect(),
        }],
        "council_verdict" => vec![CoreEvent::CouncilVerdict(CouncilVerdict {
            council_id: text("council_id"),
            chosen: text("chosen"),
            decision: text("decision"),
            reason: text("reason"),
            dissent: items(event, "dissent").map(dissent).collect(),
        })],
        _ => Vec::new(),
    }
}

fn field(value: &Value, key: &str) -> String {
    value.get(key).and_then(Value::as_str).unwrap_or("").to_string()
}

/// The objects of an array field; a missing or malformed field has none.
fn items<'a>(event: &'a Value, key: &str) -> impl Iterator<Item = &'a Value> {
    event.get(key).and_then(Value::as_array).into_iter().flatten()
}

fn strings_of(value: Option<&Value>) -> Vec<String> {
    value.and_then(Value::as_array).into_iter().flatten().filter_map(|v| v.as_str().map(str::to_string)).collect()
}

fn number(value: &Value, key: &str) -> u32 {
    value.get(key).and_then(Value::as_u64).map_or(0, |n| n.min(u64::from(u32::MAX)) as u32)
}

/// A call's tokens and price; a price the core left out stays unknown.
fn usage(value: &Value) -> Usage {
    let tokens = |key: &str| value.get(key).and_then(Value::as_u64).unwrap_or(0);
    Usage {
        input: tokens("input_tokens"),
        output: tokens("output_tokens"),
        cache_read: value.get("cache_read_tokens").and_then(Value::as_u64),
        cost: value.get("cost_usd").and_then(Value::as_f64),
        approx: value.get("cost_approx").and_then(Value::as_bool).unwrap_or(false),
    }
}

/// `{state, summary}`; a bare string is only a summary.
fn tests(value: Option<&Value>) -> Tests {
    match value {
        Some(object @ Value::Object(_)) => Tests {
            state: match field(object, "state").as_str() {
                "running" => TestState::Running,
                "passed" => TestState::Passed,
                "failed" => TestState::Failed,
                _ => TestState::Unknown,
            },
            summary: field(object, "summary"),
        },
        Some(Value::String(summary)) => Tests { state: TestState::Unknown, summary: summary.clone() },
        _ => Tests::default(),
    }
}

fn diff_stat(value: Option<&Value>) -> DiffStat {
    value.map_or_else(DiffStat::default, |v| DiffStat { files: number(v, "files"), added: number(v, "added"), removed: number(v, "removed") })
}

fn started(event: &Value) -> Started {
    Started {
        run_id: field(event, "run_id"),
        task: field(event, "task"),
        count: number(event, "count"),
        base: field(event, "base"),
        executors: items(event, "executors")
            .map(|e| ExecutorInfo { id: field(e, "executor_id"), style: field(e, "style"), branch: field(e, "branch"), session: field(e, "session") })
            .collect(),
    }
}

fn status(event: &Value) -> Status {
    Status {
        run_id: field(event, "run_id"),
        id: field(event, "executor_id"),
        style: field(event, "style"),
        branch: field(event, "branch"),
        state: ExecState::parse(&field(event, "state")),
        step: number(event, "step"),
        tests: tests(event.get("tests")),
        diff: diff_stat(event.get("diff")),
        usage: event.get("usage").filter(|u| u.is_object()).map(usage),
    }
}

fn verdict(event: &Value) -> Verdict {
    Verdict {
        run_id: field(event, "run_id"),
        rows: items(event, "rows")
            .map(|row| VerdictRow {
                id: field(row, "executor_id"),
                style: field(row, "style"),
                outcome: Outcome::parse(&field(row, "outcome")),
                tests: tests(row.get("tests")),
                diff: diff_stat(row.get("diff")),
                note: field(row, "note"),
            })
            .collect(),
        winner: field(event, "winner"),
        reason: field(event, "reason"),
    }
}

fn dissent(entry: &Value) -> Dissent {
    let angle = field(entry, "angle");
    Dissent { who: if angle.is_empty() { field(entry, "executor_id") } else { angle }, why: field(entry, "why") }
}

fn executor_activity(event: &Value) -> Vec<CoreEvent> {
    let run_id = field(event, "run_id");
    let id = field(event, "executor_id");
    let call = field(event, "call_id");
    let action = field(event, "action_type");
    match field(event, "phase").as_str() {
        "step" => vec![CoreEvent::ExecutorStep { run_id, id, call, kind: kind(&action), label: label(event, &action) }],
        "result" => vec![CoreEvent::ExecutorResult {
            run_id,
            id,
            call,
            ok: event.get("ok").and_then(Value::as_bool).unwrap_or(false),
            summary: field(event, "summary"),
        }],
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
    #[test]
    fn a_call_begin_is_a_card_unless_it_is_the_answer() {
        let begin = |tool: &str| json!({"type": "call_begin", "call_id": "c1", "tool": tool, "path": "a.py", "description": "reads a.py"});
        assert_eq!(translate(&begin("read_file")).len(), 1);
        assert!(translate(&begin("final_answer")).is_empty());
    }

    use super::*;
    use serde_json::json;

    #[test]
    fn thinking_events_carry_their_text_and_duration() {
        let chunk = json!({"type": "reasoning_chunk", "stream_id": "s", "text": "hm", "append": true});
        assert_eq!(translate(&chunk), [CoreEvent::ThoughtText { text: "hm".into(), append: true }]);
        let old = json!({"type": "reasoning_chunk", "stream_id": "s", "text": "hm"});
        assert_eq!(translate(&old), [CoreEvent::ThoughtText { text: "hm".into(), append: false }], "an older core sends snapshots");
        let commit = json!({"type": "reasoning_commit", "stream_id": "s", "duration_seconds": 2.5});
        assert_eq!(translate(&commit), [CoreEvent::ThinkingDone { seconds: 2.5 }]);
    }

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
        assert_eq!(
            translate(&json!({"type": "screen", "state": "closed", "screen_id": "browser", "frame_url": ""})),
            vec![CoreEvent::ScreenClosed { screen_id: "browser".into() }]
        );
        assert_eq!(
            translate(&json!({"type": "screen", "state": "running", "screen_id": "browser", "title": "Browser", "frame_url": "http://127.0.0.1:1/f", "stream_url": "http://127.0.0.1:1/s", "url": "http://127.0.0.1:1/?t=k"})),
            vec![CoreEvent::Screen { screen_id: "browser".into(), title: "Browser".into(), frame_url: "http://127.0.0.1:1/f".into(), stream_url: "http://127.0.0.1:1/s".into(), url: "http://127.0.0.1:1/?t=k".into() }]
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
    fn the_lines_of_a_call_being_written_are_passed_on() {
        let piece = json!({"type": "call_stream", "call_id": "c1", "tool": "write_file", "path": "a.py",
            "field": "content", "line": 4, "text": "x = 1\n", "done": false});
        assert_eq!(
            translate(&piece),
            vec![CoreEvent::CallStream {
                call: "c1".into(),
                tool: "write_file".into(),
                path: "a.py".into(),
                field: "content".into(),
                line: 4,
                text: "x = 1\n".into()
            }]
        );
    }

    fn started_event() -> Value {
        json!({"type": "bestof_started", "run_id": "r1", "task": "fix it", "count": 2, "base": "abc123",
            "executors": [
                {"executor_id": "e1", "style": "senior-dev", "branch": "kvist/bestof/r1/e1", "session": "s-e1"},
                {"executor_id": "e2", "style": "minimalist", "branch": "kvist/bestof/r1/e2", "session": "s-e2"}]})
    }

    #[test]
    fn a_started_run_lists_its_executors() {
        let [CoreEvent::BestofStarted(started)] = &translate(&started_event())[..] else { panic!("one event") };
        assert_eq!((started.run_id.as_str(), started.count, started.base.as_str()), ("r1", 2, "abc123"));
        assert_eq!(started.executors.len(), 2);
        assert_eq!(started.executors[1], ExecutorInfo { id: "e2".into(), style: "minimalist".into(), branch: "kvist/bestof/r1/e2".into(), session: "s-e2".into() });
        // Nothing but its type: every field has an empty reading.
        let [CoreEvent::BestofStarted(bare)] = &translate(&json!({"type": "bestof_started"}))[..] else { panic!("one event") };
        assert!(bare.executors.is_empty() && bare.run_id.is_empty() && bare.count == 0);
    }

    #[test]
    fn a_status_snapshot_reads_tests_diff_and_usage_with_what_the_core_left_out() {
        let status = json!({"type": "bestof_status", "run_id": "r1", "executor_id": "e1", "style": "senior-dev",
            "branch": "b", "state": "running", "step": 4,
            "tests": {"state": "passed", "summary": "12 passed"},
            "diff": {"files": 2, "added": 30, "removed": 4},
            "usage": {"input_tokens": 1000, "output_tokens": 200, "cost_approx": false}});
        let [CoreEvent::BestofStatus(status)] = &translate(&status)[..] else { panic!("one event") };
        assert_eq!(status.state, Some(ExecState::Running));
        assert_eq!(status.tests, Tests { state: TestState::Passed, summary: "12 passed".into() });
        assert_eq!(status.diff, DiffStat { files: 2, added: 30, removed: 4 });
        assert_eq!(status.usage, Some(Usage { input: 1000, output: 200, cache_read: None, cost: None, approx: false }));

        let priced = json!({"type": "bestof_status", "run_id": "r1", "executor_id": "e1", "state": "finished", "step": 9,
            "tests": {"state": "failed", "summary": ""}, "diff": {"files": 0, "added": 0, "removed": 0},
            "usage": {"input_tokens": 10, "output_tokens": 2, "cache_read_tokens": 4, "cost_usd": 0.5, "cost_approx": true}});
        let [CoreEvent::BestofStatus(priced)] = &translate(&priced)[..] else { panic!("one event") };
        assert_eq!(priced.usage, Some(Usage { input: 10, output: 2, cache_read: Some(4), cost: Some(0.5), approx: true }));

        let unknown = json!({"type": "bestof_status", "run_id": "r1", "executor_id": "e1", "state": "levitating"});
        let [CoreEvent::BestofStatus(unknown)] = &translate(&unknown)[..] else { panic!("one event") };
        assert_eq!((unknown.state, unknown.usage.as_ref(), unknown.tests.state), (None, None, TestState::Unknown));
    }

    #[test]
    fn an_executors_calls_arrive_as_they_start_and_end_and_a_result_without_ok_is_not_ok() {
        let step = json!({"type": "executor_activity", "run_id": "r1", "executor_id": "e1", "phase": "step", "call_id": "c1",
            "action_type": "run_shell", "label": "runs the tests", "summary": "", "output": ""});
        assert_eq!(
            translate(&step),
            vec![CoreEvent::ExecutorStep { run_id: "r1".into(), id: "e1".into(), call: "c1".into(), kind: ToolKind::Shell, label: "runs the tests".into() }]
        );
        let done = json!({"type": "executor_activity", "run_id": "r1", "executor_id": "e1", "phase": "result", "call_id": "c1",
            "action_type": "run_shell", "label": "runs the tests", "ok": true, "summary": "exit 0", "output": "48 passed"});
        assert_eq!(
            translate(&done),
            vec![CoreEvent::ExecutorResult { run_id: "r1".into(), id: "e1".into(), call: "c1".into(), ok: true, summary: "exit 0".into() }]
        );
        let no_ok = json!({"type": "executor_activity", "run_id": "r1", "executor_id": "e1", "phase": "result", "call_id": "c1", "action_type": "run_shell"});
        assert!(matches!(&translate(&no_ok)[..], [CoreEvent::ExecutorResult { ok: false, .. }]));
        assert!(translate(&json!({"type": "executor_activity", "phase": "later"})).is_empty());
    }

    #[test]
    fn an_elimination_a_verdict_and_a_patch_are_read() {
        assert_eq!(
            translate(&json!({"type": "bestof_eliminated", "run_id": "r1", "executor_id": "e2", "reason": "tests failed"})),
            vec![CoreEvent::BestofEliminated { run_id: "r1".into(), id: "e2".into(), reason: "tests failed".into() }]
        );
        let verdict = json!({"type": "bestof_verdict", "run_id": "r1", "winner": "e1", "reason": "It passes everything.", "rows": [
            {"executor_id": "e1", "style": "senior-dev", "outcome": "winner", "tests": {"state": "passed", "summary": "12 passed"},
             "diff": {"files": 2, "added": 30, "removed": 4}, "note": "clean"},
            {"executor_id": "e2", "style": "minimalist", "outcome": "eliminated", "tests": {"state": "failed", "summary": "2 failed"},
             "diff": {"files": 1, "added": 3, "removed": 0}, "note": ""}]});
        let [CoreEvent::BestofVerdict(verdict)] = &translate(&verdict)[..] else { panic!("one event") };
        assert_eq!((verdict.winner.as_str(), verdict.rows.len()), ("e1", 2));
        assert_eq!(verdict.rows[0].outcome, Some(Outcome::Winner));
        assert_eq!(verdict.rows[1].tests, Tests { state: TestState::Failed, summary: "2 failed".into() });
        assert_eq!(verdict.rows[0].diff, DiffStat { files: 2, added: 30, removed: 4 });

        let patch = json!({"type": "bestof_diff", "run_id": "r1", "executor_id": "e1", "patch": "@@ -1 +1 @@\n-a\n+b", "files": ["a.py", "b.py"]});
        assert_eq!(
            translate(&patch),
            vec![CoreEvent::BestofDiff(ArenaDiff { run_id: "r1".into(), id: "e1".into(), patch: "@@ -1 +1 @@\n-a\n+b".into(), files: vec!["a.py".into(), "b.py".into()] })]
        );
    }

    #[test]
    fn a_finished_run_without_an_executor_or_a_commit_reads_them_empty() {
        let accepted = json!({"type": "bestof_finished", "run_id": "r1", "outcome": "accepted", "executor_id": "e1", "commit": "abc1234", "message": "Committed"});
        assert_eq!(
            translate(&accepted),
            vec![CoreEvent::BestofFinished(Finished { run_id: "r1".into(), outcome: Some(Finish::Accepted), id: "e1".into(), commit: "abc1234".into(), message: "Committed".into() })]
        );
        let discarded = json!({"type": "bestof_finished", "run_id": "r1", "outcome": "discarded", "message": "Thrown away"});
        assert_eq!(
            translate(&discarded),
            vec![CoreEvent::BestofFinished(Finished { run_id: "r1".into(), outcome: Some(Finish::Discarded), id: String::new(), commit: String::new(), message: "Thrown away".into() })]
        );
    }

    #[test]
    fn the_council_events_are_read() {
        let started = json!({"type": "council_started", "council_id": "c1", "question": "Which queue?", "members": [
            {"executor_id": "m1", "angle": "simplicity", "session": "s1"}, {"executor_id": "m2", "angle": "security", "session": "s2"}]});
        assert_eq!(
            translate(&started),
            vec![CoreEvent::CouncilStarted {
                council_id: "c1".into(),
                question: "Which queue?".into(),
                members: vec![Member { id: "m1".into(), angle: "simplicity".into() }, Member { id: "m2".into(), angle: "security".into() }]
            }]
        );
        let card = json!({"type": "council_card", "council_id": "c1", "executor_id": "m1", "angle": "simplicity", "decision": "Use a list", "argument": "It is enough"});
        assert_eq!(
            translate(&card),
            vec![CoreEvent::CouncilCard { council_id: "c1".into(), id: "m1".into(), angle: "simplicity".into(), decision: "Use a list".into(), argument: "It is enough".into() }]
        );
        let objection = json!({"type": "council_objection", "council_id": "c1", "executor_id": "m2", "angle": "security",
            "risks": [{"target": "m1", "risk": "unbounded"}]});
        assert_eq!(
            translate(&objection),
            vec![CoreEvent::CouncilObjection { council_id: "c1".into(), id: "m2".into(), angle: "security".into(), risks: vec![Risk { target: "m1".into(), risk: "unbounded".into() }] }]
        );
        let verdict = json!({"type": "council_verdict", "council_id": "c1", "chosen": "m1", "decision": "Use a list", "reason": "Simplest.",
            "dissent": [{"executor_id": "m2", "angle": "security", "why": "unbounded"}, {"executor_id": "m3", "why": "slow"}]});
        let [CoreEvent::CouncilVerdict(verdict)] = &translate(&verdict)[..] else { panic!("one event") };
        assert_eq!(verdict.chosen, "m1");
        assert_eq!(verdict.dissent, vec![Dissent { who: "security".into(), why: "unbounded".into() }, Dissent { who: "m3".into(), why: "slow".into() }]);
        let [CoreEvent::CouncilCard { argument, .. }] = &translate(&json!({"type": "council_card", "council_id": "c1", "executor_id": "m1"}))[..] else { panic!("one event") };
        assert!(argument.is_empty());
    }

    #[test]
    fn usage_and_tool_mode_come_through_and_a_price_the_core_left_out_stays_unknown() {
        let call = json!({"type": "usage", "input_tokens": 1200, "output_tokens": 80, "usage_source": "provider", "kind": "step", "step": 1});
        assert_eq!(translate(&call), vec![CoreEvent::Spent(Usage { input: 1200, output: 80, cache_read: None, cost: None, approx: false })]);
        let priced = json!({"type": "usage", "input_tokens": 1200, "output_tokens": 80, "cache_read_tokens": 1000, "cost_usd": 0.01, "cost_approx": true});
        assert_eq!(translate(&priced), vec![CoreEvent::Spent(Usage { input: 1200, output: 80, cache_read: Some(1000), cost: Some(0.01), approx: true })]);
        assert_eq!(translate(&json!({"type": "tool_mode", "mode": "native", "reason": "probe_ok"})), vec![CoreEvent::ToolMode("native".into())]);
        assert!(translate(&json!({"type": "tool_mode", "reason": "probe_ok"})).is_empty());
    }

    #[test]
    fn unknown_events_are_ignored() {
        assert!(translate(&json!({"type": "something_new", "x": 1})).is_empty());
    }
}
