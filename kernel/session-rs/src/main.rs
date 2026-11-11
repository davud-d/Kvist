//! `kvist-session`: Kvist's headless session operations as a native program.
//!
//! It answers `--session-op` exactly as `python -m kernel --session-op` does
//! (docs/UI_API.md, "Headless session operations"): the same arguments, one
//! JSON object on stdout, exit code 0 or 1, and the same files under
//! `<workspace>/.kvist/`. The Python core hands these requests over when this
//! program is installed and serves them itself when it is not, so the two must
//! never drift apart; `kernel/tests/test_session_native.py` holds them together.
//!
//! It runs on Linux, macOS, the BSDs and Windows. What differs between them (locking
//! details, owners, link counts, permissions, listing a folder) is in `sys.rs`, with the
//! same rules the Python implementation follows on each system.

mod attachments;
mod config;
mod durable;
mod error;
mod input_history;
mod journal;
mod json;
mod pytext;
mod redaction;
mod sessions;
mod store;
mod sys;

use std::io::Read;
use std::process::ExitCode;

use error::{OpError, Result};
use json::{Object, Value};
use store::Request;

fn integer(flag: &str, value: &str) -> Result<i64> {
    value.parse().map_err(|_| OpError::Invalid(format!("argument {flag}: invalid int value: {}", pytext::repr(value))))
}

fn parse_arguments(arguments: impl Iterator<Item = String>) -> Result<Request> {
    let mut request = Request::default();
    let mut arguments = arguments.peekable();
    while let Some(argument) = arguments.next() {
        let (flag, inline) = match argument.split_once('=') {
            Some((flag, value)) if flag.starts_with("--") => (flag.to_string(), Some(value.to_string())),
            _ => (argument, None),
        };
        let known = ["--workspace", "--session-op", "--session-name", "--history-limit", "--history-before", "--history-after"];
        if !known.contains(&flag.as_str()) {
            return Err(OpError::Invalid(format!("unrecognized arguments: {flag}")));
        }
        let Some(value) = inline.or_else(|| arguments.next()) else {
            return Err(OpError::Invalid(format!("argument {flag}: expected one argument")));
        };
        match flag.as_str() {
            "--workspace" => request.workspace = Some(value),
            "--session-op" => request.op = Some(value),
            "--session-name" => request.name = Some(value),
            "--history-limit" => request.limit = Some(integer(&flag, &value)?),
            "--history-before" => request.before = Some(integer(&flag, &value)?),
            _ => request.after = Some(integer(&flag, &value)?),
        }
    }
    Ok(request)
}

fn standard_input() -> String {
    let mut raw = Vec::new();
    // An unreadable stdin is an empty input, which `input-history-add` skips.
    let _ = std::io::stdin().lock().read_to_end(&mut raw);
    String::from_utf8_lossy(&raw).into_owned()
}

fn main() -> ExitCode {
    let outcome = parse_arguments(std::env::args_os().skip(1).map(|argument| argument.to_string_lossy().into_owned())).and_then(|request| store::run(&request, standard_input));
    let (payload, code) = match outcome {
        Ok(payload) => (payload, ExitCode::SUCCESS),
        Err(error) => {
            let message = redaction::sanitize_display_text(pytext::strip(error.message()));
            let failure = Object::from_pairs(vec![
                ("ok".to_string(), Value::Bool(false)),
                ("error".to_string(), Value::String(message)),
            ]);
            (failure, ExitCode::FAILURE)
        }
    };
    println!("{}", Value::Object(payload).to_compact());
    code
}
