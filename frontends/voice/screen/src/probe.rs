// Decides, before the interface takes over the screen, whether this terminal
// can show it at all. The interface is pictures first, so a terminal without
// the kitty graphics protocol is refused with a message instead of being given
// a half-working fallback.

use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use crossterm::terminal;

use crate::strings::strings;

const PROBE_TIMEOUT: Duration = Duration::from_millis(1200);
/// kitty only reads temporary files whose name carries this marker.
pub const TEMP_MARKER: &str = "tty-graphics-protocol";

pub struct Caps {
    /// Where pictures are written for the terminal to read; `None` means they
    /// travel inline over the tty (slower, but works over ssh).
    pub temp_dir: Option<PathBuf>,
}

#[derive(Debug)]
pub enum Refusal {
    NotTerminal,
    Multiplexer(&'static str),
    NoGraphics,
    NoPixelSize,
}

impl Refusal {
    pub fn message(&self) -> String {
        let s = strings();
        let reason = match self {
            Refusal::NotTerminal => s.refuse_tty.to_string(),
            Refusal::Multiplexer(name) => s.refuse_multiplexer.replace("{mux}", name),
            Refusal::NoGraphics => s.refuse_graphics.to_string(),
            Refusal::NoPixelSize => s.refuse_pixels.to_string(),
        };
        format!("{reason}\n{}", s.refuse_next)
    }
}

pub fn probe() -> Result<Caps, Refusal> {
    // SAFETY: isatty only inspects the descriptor.
    let ttys = unsafe { libc::isatty(0) == 1 && libc::isatty(1) == 1 };
    if !ttys {
        return Err(Refusal::NotTerminal);
    }
    if let Some(name) = multiplexer() {
        return Err(Refusal::Multiplexer(name));
    }
    let temp_dir = temp_dir();
    let probe_file = temp_dir.join(format!("kvist-voice-{TEMP_MARKER}-probe-{}", std::process::id()));
    let temp_ok = fs::write(&probe_file, [0u8, 0, 0]).is_ok();

    terminal::enable_raw_mode().map_err(|_| Refusal::NotTerminal)?;
    let reply = ask(&query(temp_ok.then_some(probe_file.as_path())), PROBE_TIMEOUT);
    terminal::disable_raw_mode().map_err(|_| Refusal::NotTerminal)?;
    forget_probe_file(&probe_file);

    let reply = reply.map_err(|_| Refusal::NotTerminal)?;
    let answers = Answers::parse(&reply);
    if !answers.graphics {
        return Err(Refusal::NoGraphics);
    }
    let size = terminal::window_size().map_err(|_| Refusal::NoPixelSize)?;
    if size.width == 0 || size.height == 0 {
        return Err(Refusal::NoPixelSize);
    }
    Ok(Caps { temp_dir: answers.temp_file.then_some(temp_dir) })
}

fn multiplexer() -> Option<&'static str> {
    if std::env::var_os("TMUX").is_some() {
        return Some("tmux");
    }
    if std::env::var_os("STY").is_some() || std::env::var("TERM").is_ok_and(|t| t.starts_with("screen")) {
        return Some("GNU screen");
    }
    if std::env::var_os("ZELLIJ").is_some() {
        return Some("Zellij");
    }
    None
}

fn temp_dir() -> PathBuf {
    let shm = Path::new("/dev/shm");
    if shm.is_dir() { shm.to_path_buf() } else { std::env::temp_dir() }
}

fn forget_probe_file(path: &Path) {
    match fs::remove_file(path) {
        Ok(()) => {}
        // The terminal deletes a temporary file once it has read it.
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => eprintln!("kvist-voice: could not remove {}: {error}", path.display()),
    }
}

/// Two 1×1 graphics queries (inline and temporary file) and a Device
/// Attributes request. Every terminal answers the last one, so its reply ends
/// the wait whether or not the graphics queries were understood.
fn query(temp_file: Option<&Path>) -> Vec<u8> {
    let mut out = b"\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\".to_vec();
    if let Some(path) = temp_file {
        let encoded = STANDARD.encode(path.as_os_str().as_encoded_bytes());
        out.extend_from_slice(format!("\x1b_Gi=32,s=1,v=1,a=q,t=t,f=24;{encoded}\x1b\\").as_bytes());
    }
    out.extend_from_slice(b"\x1b[c");
    out
}

fn ask(query: &[u8], timeout: Duration) -> io::Result<Vec<u8>> {
    let mut stdout = io::stdout();
    stdout.write_all(query)?;
    stdout.flush()?;
    let deadline = Instant::now() + timeout;
    let mut reply = Vec::new();
    let mut stdin = io::stdin();
    while !has_device_attributes(&reply) {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() || !readable(left)? {
            break;
        }
        let mut chunk = [0u8; 512];
        let read = stdin.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        reply.extend_from_slice(&chunk[..read]);
    }
    Ok(reply)
}

fn readable(timeout: Duration) -> io::Result<bool> {
    let mut fd = libc::pollfd { fd: 0, events: libc::POLLIN, revents: 0 };
    // SAFETY: one valid pollfd for the duration of the call.
    let ready = unsafe { libc::poll(&mut fd, 1, timeout.as_millis() as libc::c_int) };
    if ready < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(ready > 0)
}

fn has_device_attributes(reply: &[u8]) -> bool {
    let Some(start) = find(reply, b"\x1b[?") else { return false };
    reply[start + 3..].iter().any(|&b| b == b'c')
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|window| window == needle)
}

#[derive(Debug, PartialEq)]
struct Answers {
    graphics: bool,
    temp_file: bool,
}

impl Answers {
    fn parse(reply: &[u8]) -> Self {
        Answers {
            graphics: find(reply, b"\x1b_Gi=31;OK").is_some(),
            temp_file: find(reply, b"\x1b_Gi=32;OK").is_some(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kitty_answers_both_queries() {
        let reply = b"\x1b_Gi=31;OK\x1b\\\x1b_Gi=32;OK\x1b\\\x1b[?62;c";
        assert!(has_device_attributes(reply));
        assert_eq!(Answers::parse(reply), Answers { graphics: true, temp_file: true });
    }

    #[test]
    fn a_terminal_without_graphics_only_answers_attributes() {
        let reply = b"\x1b[?65;1;9c";
        assert!(has_device_attributes(reply));
        assert_eq!(Answers::parse(reply), Answers { graphics: false, temp_file: false });
    }

    #[test]
    fn a_refused_temporary_file_keeps_inline_graphics() {
        let reply = b"\x1b_Gi=31;OK\x1b\\\x1b_Gi=32;EBADF:no such file\x1b\\\x1b[?62;c";
        assert_eq!(Answers::parse(reply), Answers { graphics: true, temp_file: false });
    }

    #[test]
    fn the_wait_is_not_over_before_the_attributes_reply() {
        assert!(!has_device_attributes(b"\x1b_Gi=31;OK\x1b\\"));
        assert!(!has_device_attributes(b"\x1b[?62;"));
    }
}
