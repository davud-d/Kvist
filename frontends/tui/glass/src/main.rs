// Kvist Glass: a terminal interface drawn over a photograph, with frosted
// glass cards, a live browser preview and the helpers' work in view. It runs
// the Kvist core as a child process (`--frontend stdio-multi`).
//
//   kvist-glass [--workspace DIR]   start the interface (default: this folder)
//   kvist-glass --check             only check the terminal: exit 0 if it can
//                                   show the interface, 3 (with the reason) if not

mod anim;
mod app;
mod backdrop;
mod core;
mod dialog;
mod kitty;
mod layout;
mod model;
mod models;
mod pictures;
mod probe;
mod protocol;
mod screen;
mod strings;
mod term;
mod text;
mod theme;
mod view;

use std::path::PathBuf;
use std::process::ExitCode;

/// Exit status when the terminal cannot show the interface.
const REFUSED: u8 = 3;
/// Exit status for a setting or argument with an invalid value.
const BAD_SETTING: u8 = 2;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let check_only = args.iter().any(|arg| arg == "--check");
    let workspace = match workspace(&args) {
        Ok(path) => path,
        Err(message) => {
            eprintln!("{message}");
            return ExitCode::from(BAD_SETTING);
        }
    };
    let fps = match anim::frame_rate(std::env::var("KVIST_GLASS_FPS").ok().as_deref()) {
        Ok(fps) => fps,
        Err(value) => {
            let message = strings::strings()
                .bad_fps
                .replace("{value}", &value)
                .replace("{min}", &anim::FPS_RANGE.start().to_string())
                .replace("{max}", &anim::FPS_RANGE.end().to_string());
            eprintln!("{message}");
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
    if check_only {
        return ExitCode::SUCCESS;
    }
    match app::run(caps, fps, &workspace) {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("kvist-glass: {error}");
            ExitCode::FAILURE
        }
    }
}

/// `--workspace DIR` (an existing folder), else the current folder.
fn workspace(args: &[String]) -> Result<PathBuf, String> {
    let given = args.iter().position(|arg| arg == "--workspace").map(|i| args.get(i + 1).cloned().unwrap_or_default());
    let Some(raw) = given else {
        return std::env::current_dir().map_err(|error| error.to_string());
    };
    let path = PathBuf::from(&raw);
    if !path.is_dir() {
        return Err(strings::strings().bad_workspace.replace("{value}", &raw));
    }
    std::fs::canonicalize(&path).map_err(|error| error.to_string())
}
