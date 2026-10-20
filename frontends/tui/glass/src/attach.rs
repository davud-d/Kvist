// Pictures attached to a request: read from the clipboard or a file, shown as
// thumbnails over the input, sent with the goal as the core's
// `kvist-image-goal` envelope (docs/vision.md).

use std::io::Read;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use image::imageops::FilterType;
use image::{DynamicImage, RgbImage};
use serde_json::json;

use crate::backdrop::{self, WebFrame};

pub const MAX_IMAGES: usize = 8;
/// The core refuses larger pictures.
pub const MAX_BYTES: usize = 5 * 1024 * 1024;
/// A clipboard owner that does not answer must not freeze the screen.
const CLIPBOARD_WAIT: Duration = Duration::from_secs(3);
/// The opened picture never needs more pixels than the window can show.
const SHOWN_WIDTH: u32 = 1500;
const SHOWN_HEIGHT: u32 = 940;
/// Marks a frame as an attachment's, so its cache key never meets the browser's.
const VERSION_BIT: u64 = 1 << 63;

static VERSION: AtomicU64 = AtomicU64::new(1);

pub struct Attachment {
    /// The N of its `[Image #N]` marker.
    pub id: u32,
    pub name: String,
    data_url: String,
    pub frame: Arc<WebFrame>,
    pub aspect: f32,
}

/// `Ok(None)`: the clipboard holds no picture. `Err`: it does, and it cannot be used.
pub fn from_clipboard(id: u32) -> Result<Option<Attachment>, String> {
    let (program, args) = clipboard_command(&["--type", "image/png"], &["-t", "image/png", "-o"]);
    let Ok(data) = capture(program, &args) else { return Ok(None) };
    if data.is_empty() {
        return Ok(None);
    }
    build(id, "clipboard.png", "png", data).map(Some)
}

/// The clipboard's text, for a Ctrl+V that finds no picture.
pub fn clipboard_text() -> Option<String> {
    let (program, args) = clipboard_command(&["--no-newline"], &["-o"]);
    let data = capture(program, &args).ok()?;
    let text = String::from_utf8(data).ok()?;
    (!text.is_empty()).then_some(text)
}

/// A pasted or dropped text that names a PNG or JPEG file. `None`: it is just text.
pub fn from_path(id: u32, text: &str) -> Option<Result<Attachment, String>> {
    let path = path_in(text)?;
    let kind = match path.extension()?.to_str()?.to_ascii_lowercase().as_str() {
        "png" => "png",
        "jpg" | "jpeg" => "jpeg",
        _ => return None,
    };
    if !path.is_file() {
        return None;
    }
    let name = path.file_name()?.to_string_lossy().into_owned();
    let data = match read_limited(&path) {
        Ok(data) => data,
        Err(error) => return Some(Err(error)),
    };
    Some(build(id, &name, kind, data))
}

/// The ids named by `[Image #N]` markers in `text`, in order, each once.
pub fn markers(text: &str) -> Vec<u32> {
    let mut found = Vec::new();
    let mut rest = text;
    while let Some(at) = rest.find("[Image #") {
        rest = &rest[at + "[Image #".len()..];
        let digits: String = rest.chars().take_while(char::is_ascii_digit).collect();
        if !digits.is_empty()
            && rest[digits.len()..].starts_with(']')
            && let Ok(id) = digits.parse()
            && !found.contains(&id)
        {
            found.push(id);
        }
    }
    found
}

/// Where a marker ending just before `cursor` (in characters) begins, so one
/// Backspace removes the whole marker.
pub fn marker_before(text: &str, cursor: usize) -> Option<usize> {
    let before: Vec<char> = text.chars().take(cursor).collect();
    if before.last() != Some(&']') {
        return None;
    }
    let start = before.iter().rposition(|c| *c == '[')?;
    let inside: String = before[start..].iter().collect();
    (markers(&inside).len() == 1 && inside.starts_with("[Image #")).then_some(start)
}

/// The goal as the core wants it: plain text, or the envelope with the pictures.
pub fn pack(text: &str, attached: &[&Attachment]) -> String {
    if attached.is_empty() {
        return text.to_string();
    }
    let images: Vec<_> = attached.iter().map(|a| json!({"name": a.name, "data_url": a.data_url})).collect();
    json!({"type": "kvist-image-goal", "text": text, "images": images}).to_string()
}

fn build(id: u32, name: &str, kind: &str, data: Vec<u8>) -> Result<Attachment, String> {
    if data.len() > MAX_BYTES {
        return Err(too_big());
    }
    let decoded = image::load_from_memory(&data).map_err(|error| error.to_string())?;
    let raw = shown(decoded);
    let version = VERSION_BIT | VERSION.fetch_add(1, Ordering::Relaxed);
    let aspect = raw.width() as f32 / raw.height().max(1) as f32;
    Ok(Attachment {
        id,
        name: name.to_string(),
        data_url: format!("data:image/{kind};base64,{}", STANDARD.encode(&data)),
        frame: Arc::new(backdrop::web_frame(raw, version)),
        aspect,
    })
}

fn too_big() -> String {
    crate::strings::strings().image_too_big.to_string()
}

fn shown(decoded: DynamicImage) -> RgbImage {
    let rgb = decoded.to_rgb8();
    if rgb.width() <= SHOWN_WIDTH && rgb.height() <= SHOWN_HEIGHT {
        return rgb;
    }
    let scale = (SHOWN_WIDTH as f32 / rgb.width() as f32).min(SHOWN_HEIGHT as f32 / rgb.height() as f32);
    let size = |side: u32| ((side as f32 * scale).round() as u32).max(1);
    image::imageops::resize(&rgb, size(rgb.width()), size(rgb.height()), FilterType::Triangle)
}

fn read_limited(path: &PathBuf) -> Result<Vec<u8>, String> {
    let file = std::fs::File::open(path).map_err(|error| error.to_string())?;
    let mut data = Vec::new();
    file.take(MAX_BYTES as u64 + 1).read_to_end(&mut data).map_err(|error| error.to_string())?;
    if data.len() > MAX_BYTES {
        return Err(too_big());
    }
    Ok(data)
}

/// The path a terminal pastes for a dropped file: maybe quoted, escaped,
/// `file://`-prefixed or relative to the home folder.
fn path_in(text: &str) -> Option<PathBuf> {
    let text = text.trim();
    if text.is_empty() || text.contains('\n') {
        return None;
    }
    let unquoted = text.trim_matches(|c| c == '\'' || c == '"');
    let path = match unquoted.strip_prefix("file://") {
        Some(rest) => percent_decoded(rest),
        None => unquoted.replace("\\ ", " "),
    };
    match path.strip_prefix("~/") {
        Some(rest) => Some(PathBuf::from(std::env::var_os("HOME")?).join(rest)),
        None => Some(PathBuf::from(path)),
    }
}

fn percent_decoded(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let hex = bytes.get(i + 1..i + 3).and_then(|pair| std::str::from_utf8(pair).ok()).and_then(|pair| u8::from_str_radix(pair, 16).ok());
        match (bytes[i], hex) {
            (b'%', Some(byte)) => {
                out.push(byte);
                i += 3;
            }
            (byte, _) => {
                out.push(byte);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Wayland's `wl-paste`, else X11's `xclip`.
fn clipboard_command(wayland: &[&'static str], x11: &[&'static str]) -> (&'static str, Vec<&'static str>) {
    if std::env::var_os("WAYLAND_DISPLAY").is_some() {
        ("wl-paste", wayland.to_vec())
    } else {
        let mut args = vec!["-selection", "clipboard"];
        args.extend_from_slice(x11);
        ("xclip", args)
    }
}

/// What `program` prints, within `CLIPBOARD_WAIT` and `MAX_BYTES`.
fn capture(program: &str, args: &[&str]) -> Result<Vec<u8>, String> {
    let mut child = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("{program}: {error}"))?;
    let stdout = child.stdout.take().ok_or_else(|| format!("{program}: no output"))?;
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut data = Vec::new();
        let read = stdout.take(MAX_BYTES as u64 + 1).read_to_end(&mut data).map(|_| data);
        // The receiver is gone once the wait timed out; nobody needs the data then.
        tx.send(read).ok();
    });
    let data = match rx.recv_timeout(CLIPBOARD_WAIT) {
        Ok(Ok(data)) => data,
        Ok(Err(error)) => {
            child.kill().ok();
            child.wait().ok();
            return Err(error.to_string());
        }
        Err(_) => {
            child.kill().ok();
            child.wait().ok();
            return Err(format!("{program}: no answer"));
        }
    };
    if data.len() > MAX_BYTES {
        child.kill().ok();
        child.wait().ok();
        return Err(too_big());
    }
    let status = child.wait().map_err(|error| error.to_string())?;
    status.success().then_some(data).ok_or_else(|| format!("{program}: {status}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markers_are_found_in_order_once_each() {
        assert_eq!(markers("a [Image #2] b [Image #1] [Image #2] [Image #x] [Image #3"), vec![2, 1]);
    }

    #[test]
    fn one_backspace_takes_the_whole_marker() {
        assert_eq!(marker_before("see [Image #12]", 15), Some(4));
        assert_eq!(marker_before("see [Image #12] ", 16), None);
        assert_eq!(marker_before("see [not]", 9), None);
    }

    #[test]
    fn dropped_paths_lose_their_quoting() {
        assert_eq!(path_in("'/tmp/a b.png'"), Some(PathBuf::from("/tmp/a b.png")));
        assert_eq!(path_in("/tmp/a\\ b.png"), Some(PathBuf::from("/tmp/a b.png")));
        assert_eq!(path_in("file:///tmp/a%20b.png"), Some(PathBuf::from("/tmp/a b.png")));
        assert_eq!(path_in("two\nlines.png"), None);
    }

    #[test]
    fn text_that_is_not_an_image_path_stays_text() {
        assert!(from_path(1, "hello world").is_none());
        assert!(from_path(1, "/nonexistent/picture.png").is_none());
    }

    #[test]
    fn a_png_file_becomes_an_attachment_the_core_can_read() {
        let dir = std::env::temp_dir().join(format!("kvist-attach-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("dot.png");
        RgbImage::from_pixel(32, 20, image::Rgb([9, 99, 199])).save(&file).unwrap();
        let attachment = from_path(3, file.to_str().unwrap()).unwrap().unwrap();
        assert_eq!((attachment.id, attachment.name.as_str()), (3, "dot.png"));
        assert!((attachment.aspect - 1.6).abs() < 0.01);
        let goal: serde_json::Value = serde_json::from_str(&pack("what is it [Image #3]", &[&attachment])).unwrap();
        assert_eq!(goal["type"], "kvist-image-goal");
        assert!(goal["images"][0]["data_url"].as_str().unwrap().starts_with("data:image/png;base64,"));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_request_without_pictures_is_sent_as_plain_text() {
        assert_eq!(pack("hello", &[]), "hello");
    }
}
