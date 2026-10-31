// One-shot core processes (`--session-op …`, docs/UI_API.md §8a). The running
// cores are busy with their own streams, and these operations are not part of
// them. Each prints one JSON object on its last stdout line.

use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

use serde_json::Value;

/// What the page may ask for by name. `launch`, `switch` and `new` change the
/// workspace's active session, so only `Agents::open` runs them, one at a time.
const PAGE_OPS: [&str; 6] = ["status", "list", "history", "input-history", "input-history-add", "delete"];

pub struct Request<'a> {
    pub op: &'a str,
    pub session_name: Option<&'a str>,
    pub history_limit: Option<u32>,
    pub history_before: Option<u64>,
    pub stdin: Option<&'a str>,
}

pub fn page_may_run(op: &str) -> bool {
    PAGE_OPS.contains(&op)
}

fn arguments(request: &Request) -> Vec<String> {
    let mut args = vec!["--session-op".to_string(), request.op.to_string()];
    if let Some(name) = request.session_name {
        args.extend(["--session-name".to_string(), name.to_string()]);
    }
    if let Some(limit) = request.history_limit {
        args.extend(["--history-limit".to_string(), limit.to_string()]);
    }
    if let Some(before) = request.history_before {
        args.extend(["--history-before".to_string(), before.to_string()]);
    }
    args
}

pub fn run(argv: &[String], workspace: &Path, request: &Request) -> Result<Value, String> {
    let mut child = Command::new(&argv[0])
        .args(&argv[1..])
        .args(["--workspace", &workspace.display().to_string()])
        .args(arguments(request))
        .stdin(if request.stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("could not start the core: {error}"))?;
    if let (Some(text), Some(mut stdin)) = (request.stdin, child.stdin.take()) {
        stdin.write_all(text.as_bytes()).map_err(|error| error.to_string())?;
    }
    let output = child.wait_with_output().map_err(|error| error.to_string())?;
    parse_reply(&String::from_utf8_lossy(&output.stdout))
}

/// The reply is JSON on the last line; anything before it is noise.
pub fn parse_reply(stdout: &str) -> Result<Value, String> {
    let line = stdout.trim().lines().next_back().unwrap_or("");
    let reply: Value = serde_json::from_str(line).map_err(|error| format!("unreadable reply from the core ({error})"))?;
    if reply["ok"].as_bool() == Some(true) {
        return Ok(reply);
    }
    Err(reply["error"].as_str().unwrap_or("the core refused the operation").to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(op: &str) -> Request<'_> {
        Request { op, session_name: None, history_limit: None, history_before: None, stdin: None }
    }

    #[test]
    fn the_page_cannot_change_the_active_session() {
        for op in ["launch", "switch", "new", "create", "reset"] {
            assert!(!page_may_run(op), "{op}");
        }
        for op in ["status", "list", "history", "delete"] {
            assert!(page_may_run(op), "{op}");
        }
    }

    #[test]
    fn arguments_follow_the_request() {
        let mut history = request("history");
        history.session_name = Some("chat-1");
        history.history_limit = Some(300);
        history.history_before = Some(812);
        assert_eq!(
            arguments(&history),
            ["--session-op", "history", "--session-name", "chat-1", "--history-limit", "300", "--history-before", "812"]
        );
        assert_eq!(arguments(&request("list")), ["--session-op", "list"]);
    }

    #[test]
    fn a_reply_is_read_from_the_last_line() {
        assert_eq!(parse_reply("noise\n{\"ok\":true,\"sessions\":[]}").unwrap()["sessions"], serde_json::json!([]));
        assert_eq!(parse_reply(r#"{"ok":false,"error":"Unknown session: 'ghost'"}"#).unwrap_err(), "Unknown session: 'ghost'");
        assert!(parse_reply("").is_err());
    }
}
