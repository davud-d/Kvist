//! The one thing a session operation needs from the user config: the style a
//! new session is stamped with (`kernel/config/user_config.py`).

use std::path::{Path, PathBuf};

use crate::durable;
use crate::error::{OpError, Result};
use crate::json::{Object, Value};

const CONFIG_DIR_ENV: &str = "KVIST_CONFIG_DIR";
const DEFAULT_STYLE: &str = "Default";

/// Agent style -> the chat style that goes with it. A style missing here is
/// one this program does not know, and reads as the default, exactly as an
/// unknown name does in Python; `test_session_native.py` checks the two tables
/// name the same styles.
const CHAT_STYLE_FOR_AGENT_STYLE: [(&str, &str); 9] = [
    ("Default", "Default"),
    ("Architect", "Default"),
    ("Senior-dev", "Honest"),
    ("Paranoid", "Honest"),
    ("Perfectionist", "Honest"),
    ("Friendly Developer", "Friendly"),
    ("Explainer", "Friendly"),
    ("Minimalist", "No-Friendly"),
    ("Fast-and-dirty", "Energetic"),
];

pub use crate::sys::home_directory;

/// `Path.expanduser()` for the forms a caller passes: `~` and `~/...`.
pub fn expand_user(path: &Path) -> PathBuf {
    let Ok(rest) = path.strip_prefix("~") else {
        return path.to_path_buf();
    };
    match home_directory() {
        Some(home) => home.join(rest),
        None => path.to_path_buf(),
    }
}

fn config_path_and_boundary() -> Result<(PathBuf, PathBuf)> {
    let unavailable = || OpError::Os("User config parent directory is unavailable.".to_string());
    let override_directory = std::env::var(CONFIG_DIR_ENV).ok().map(|raw| raw.trim().to_string());
    let Some(directory) = override_directory.filter(|raw| !raw.is_empty()) else {
        let home = home_directory().ok_or_else(unavailable)?;
        let boundary = durable::canonical(&home)?;
        return Ok((boundary.join(".kvist").join("config.json"), boundary));
    };
    let path = durable::absolute(&expand_user(Path::new(&directory)).join("config.json"))?;
    // The folder may not exist yet; the boundary is the nearest one that does.
    let mut boundary = path.parent().ok_or_else(unavailable)?;
    while !boundary.exists() {
        boundary = boundary.parent().ok_or_else(unavailable)?;
    }
    let boundary = durable::canonical(boundary)?;
    Ok((path, boundary))
}

/// The styles a session starting now is opened with: `{"agents", "chat"}`.
pub fn saved_styles() -> Result<Object> {
    let (path, boundary) = config_path_and_boundary()?;
    let config = durable::read_json_object(&path, &boundary, true).map_err(|error| {
        let text = if error.is_corrupt() { "User config is invalid." } else { "Failed to read user config." };
        OpError::Storage(text.to_string())
    })?;
    let saved = config.get("styles").and_then(Value::as_object).and_then(|styles| styles.get("agents")?.as_str());
    let (agents, chat) = CHAT_STYLE_FOR_AGENT_STYLE
        .iter()
        .find(|(name, _)| Some(*name) == saved)
        .copied()
        .unwrap_or((DEFAULT_STYLE, DEFAULT_STYLE));
    Ok(Object::from_pairs(vec![
        ("agents".to_string(), Value::from(agents)),
        ("chat".to_string(), Value::from(chat)),
    ]))
}
