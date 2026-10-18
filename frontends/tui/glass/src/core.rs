// The Kvist core as a child process speaking `--frontend stdio-multi`
// (docs/UI_API.md §8, §8c): one JSON object per line each way. Events are read
// on a thread; the interface polls them between frames.

use std::fs::File;
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread;

use serde_json::Value;

/// A checkout's interpreter and entry point, for running from the source tree.
const CHECKOUT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../..");

pub enum Message {
    Event(Value),
    /// The core's stdout closed; the text says how it ended.
    Gone(String),
}

pub struct Core {
    child: Child,
    /// `None` once closed, which tells the core to finish.
    stdin: Option<ChildStdin>,
    pub messages: Receiver<Message>,
    pub log: PathBuf,
}

/// The command that starts the core: `KVIST_CORE_CMD` (a JSON array of
/// strings, as the launcher passes it to the Ink app) or the checkout's
/// `.venv/bin/python kernel/__main__.py`.
pub fn command(env_value: Option<&str>) -> Result<Vec<String>, String> {
    let Some(raw) = env_value else {
        let root = Path::new(CHECKOUT);
        return Ok(vec![
            root.join(".venv/bin/python").display().to_string(),
            root.join("kernel/__main__.py").display().to_string(),
        ]);
    };
    match serde_json::from_str::<Vec<String>>(raw) {
        Ok(argv) if !argv.is_empty() && argv.iter().all(|a| !a.is_empty()) => Ok(argv),
        _ => Err(format!("KVIST_CORE_CMD must be a non-empty JSON array of strings, got {raw}")),
    }
}

impl Core {
    pub fn spawn(argv: &[String], workspace: &Path) -> io::Result<Core> {
        let log = std::env::temp_dir().join(format!("kvist-glass-core-{}.log", std::process::id()));
        let mut child = Command::new(&argv[0])
            .args(&argv[1..])
            // `--wake`: this interface shows turns the core starts by itself, so
            // helpers and background jobs may outlive an answer.
            .args(["--workspace", &workspace.display().to_string(), "--frontend", "stdio-multi", "--wake"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(File::create(&log)?)
            .spawn()?;
        let stdin = child.stdin.take().ok_or_else(|| io::Error::other("the core has no stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| io::Error::other("the core has no stdout"))?;
        let (sender, messages) = mpsc::channel();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let message = match line {
                    Ok(line) if line.trim().is_empty() => continue,
                    Ok(line) => match serde_json::from_str::<Value>(&line) {
                        Ok(event) => Message::Event(event),
                        Err(error) => Message::Gone(format!("unreadable line from the core ({error})")),
                    },
                    Err(error) => Message::Gone(format!("reading the core failed ({error})")),
                };
                let gone = matches!(message, Message::Gone(_));
                if sender.send(message).is_err() || gone {
                    return;
                }
            }
            // The receiver may already be gone when the interface quits first.
            let _ = sender.send(Message::Gone("the core exited".to_string()));
        });
        Ok(Core { child, stdin: Some(stdin), messages, log })
    }

    pub fn send(&mut self, message: &Value) -> io::Result<()> {
        let stdin = self.stdin.as_mut().ok_or_else(|| io::Error::other("the core's input is closed"))?;
        let mut line = serde_json::to_vec(message)?;
        line.push(b'\n');
        stdin.write_all(&line)?;
        stdin.flush()
    }
}

impl Drop for Core {
    /// Closing stdin is the core's signal to finish (it cancels an open dialog
    /// and exits); a core that does not is killed. The log is removed after a
    /// clean exit and kept after any other, for whoever looks into it.
    fn drop(&mut self) {
        drop(self.stdin.take());
        let deadline = std::time::Instant::now() + std::time::Duration::from_millis(1500);
        while std::time::Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(status)) => {
                    if status.success()
                        && let Err(error) = std::fs::remove_file(&self.log)
                    {
                        eprintln!("kvist-glass: could not remove {}: {error}", self.log.display());
                    }
                    return;
                }
                Ok(None) => thread::sleep(std::time::Duration::from_millis(30)),
                Err(error) => {
                    eprintln!("kvist-glass: could not wait for the core: {error}");
                    break;
                }
            }
        }
        if let Err(error) = self.child.kill() {
            eprintln!("kvist-glass: could not stop the core: {error}");
        }
    }
}

/// `message` of a `command_result` is "English / Русский"; this picks one half.
pub fn localized(message: &str, russian: bool) -> &str {
    match message.split_once(" / ") {
        Some((english, russian_text)) => if russian { russian_text } else { english },
        None => message,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_core_command_comes_from_the_launcher_or_the_checkout() {
        assert_eq!(command(Some(r#"["kvist-core"]"#)), Ok(vec!["kvist-core".to_string()]));
        assert!(command(Some("[]")).is_err());
        assert!(command(Some("kvist-core")).is_err());
        let default = command(None).unwrap();
        assert!(default[0].ends_with(".venv/bin/python") && default[1].ends_with("kernel/__main__.py"));
    }

    #[test]
    fn a_bilingual_message_shows_one_language() {
        assert_eq!(localized("Role set to: coder / Роль: coder", true), "Роль: coder");
        assert_eq!(localized("Role set to: coder / Роль: coder", false), "Role set to: coder");
        assert_eq!(localized("Cancelled.", true), "Cancelled.");
    }
}
