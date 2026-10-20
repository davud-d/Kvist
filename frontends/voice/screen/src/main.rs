// Kvist's voice screen: an orb in the middle of a photograph, what it is doing, what was
// heard or said, and a menu for what can be changed. It runs the Kvist core
// (`--frontend stdio-multi`) with `AGENT_VOICE_MODE=1` and, as helper processes, the ears
// (the microphone and the wake word) and the voice. See voice.rs for the protocol and
// README.md for how to run it.
//
//   kvist-voice-screen [--workspace DIR]   start the screen (default: this folder)
//   kvist-voice-screen --continue          in the last session instead of a new one
//   kvist-voice-screen --demo              no process at all: every orb state in turn
//   kvist-voice-screen --check             only check the terminal: exit 0 if it can show
//                                          the screen, 3 (with the reason) if not
//   kvist-voice-screen --orb-preview FILE  write a PNG with the orb in every state
//
// The screen is a separate program with no code shared with the conversation screen
// (frontends/tui/glass). anim, backdrop, core, dialog, diff, highlight, kitty, layout, probe,
// slash, strings, term, text and theme began as copies of that screen's files and are maintained
// here from now on.

#[allow(dead_code)]
mod anim;
#[allow(dead_code)]
mod backdrop;
#[allow(dead_code)]
mod core;
#[allow(dead_code)]
mod dialog;
#[allow(dead_code)]
mod diff;
#[allow(dead_code)]
mod highlight;
#[allow(dead_code)]
mod kitty;
#[allow(dead_code)]
mod layout;
#[allow(dead_code)]
mod probe;
#[allow(dead_code)]
mod slash;
#[allow(dead_code)]
mod strings;
#[allow(dead_code)]
mod term;
#[allow(dead_code)]
mod text;
#[allow(dead_code)]
mod theme;

mod farewell;
mod icons;
mod math;
mod orb;
mod voice;
mod voice_overlay;
mod voice_strings;

use std::io;
use std::path::PathBuf;
use std::process::ExitCode;

use crossterm::terminal;

use crate::layout::Grid;

/// Exit status when the terminal cannot show the screen.
const REFUSED: u8 = 3;
/// Exit status for a setting or argument with an invalid value.
const BAD_SETTING: u8 = 2;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Some(at) = args.iter().position(|arg| arg == "--orb-preview") {
        return orb_preview(args.get(at + 1));
    }
    let workspace = match workspace(&args) {
        Ok(path) => path,
        Err(message) => {
            eprintln!("{message}");
            return ExitCode::from(BAD_SETTING);
        }
    };
    let fps = match anim::frame_rate(std::env::var("KVIST_VOICE_FPS").ok().as_deref()) {
        Ok(fps) => fps,
        Err(value) => {
            eprintln!(
                "KVIST_VOICE_FPS=\"{value}\": expected whole frames per second from {} to {}, such as 30 or 60.",
                anim::FPS_RANGE.start(),
                anim::FPS_RANGE.end()
            );
            return ExitCode::from(BAD_SETTING);
        }
    };
    let caps = match probe::probe() {
        Ok(caps) => caps,
        Err(refusal) => {
            eprintln!("{}", refusal.message());
            return ExitCode::from(REFUSED);
        }
    };
    if args.iter().any(|arg| arg == "--check") {
        return ExitCode::SUCCESS;
    }
    let demo = args.iter().any(|arg| arg == "--demo");
    let keep_session = args.iter().any(|arg| arg == "--continue");
    match voice::run(caps, fps, &workspace, demo, keep_session) {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("kvist-voice-screen: {error}");
            ExitCode::FAILURE
        }
    }
}

/// The window's size in cells and the size of a cell in pixels.
pub(crate) fn grid_now() -> io::Result<Grid> {
    let size = terminal::window_size()?;
    let cols = size.columns.max(1);
    let rows = size.rows.max(1);
    Ok(Grid { cols, rows, cw: (size.width / cols).max(1) as u32, ch: (size.height / rows).max(1) as u32 })
}

/// `--workspace DIR` (an existing folder), else the current folder.
fn workspace(args: &[String]) -> Result<PathBuf, String> {
    let given = args.iter().position(|arg| arg == "--workspace").map(|i| args.get(i + 1).cloned().unwrap_or_default());
    let Some(raw) = given else {
        return std::env::current_dir().map_err(|error| error.to_string());
    };
    let path = PathBuf::from(&raw);
    if !path.is_dir() {
        return Err(format!("--workspace \"{raw}\": no such folder."));
    }
    std::fs::canonicalize(&path).map_err(|error| error.to_string())
}

/// Writes the orb in every state to a PNG, so its look can be checked without a terminal.
fn orb_preview(target: Option<&String>) -> ExitCode {
    let Some(path) = target else {
        eprintln!("--orb-preview needs a file name");
        return ExitCode::from(BAD_SETTING);
    };
    let (width, height, rgb) = orb::contact_sheet(2.5, 256);
    match image::save_buffer(path, &rgb, width, height, image::ColorType::Rgb8) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("kvist-voice-screen: could not write {path}: {error}");
            ExitCode::FAILURE
        }
    }
}
