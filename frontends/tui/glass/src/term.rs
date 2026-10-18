// Takes the terminal over (alternate screen, raw input, mouse, paste) and
// gives it back exactly as it was, also when the program panics.

use std::io::{self, Write};

use crossterm::cursor::{Hide, Show};
use crossterm::event::{
    DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture,
    KeyboardEnhancementFlags, PopKeyboardEnhancementFlags, PushKeyboardEnhancementFlags,
};
use crossterm::execute;
use crossterm::terminal::{self, EnterAlternateScreen, LeaveAlternateScreen, SetTitle};

pub fn enter() -> io::Result<()> {
    terminal::enable_raw_mode()?;
    let mut out = io::stdout();
    execute!(
        out,
        EnterAlternateScreen,
        EnableMouseCapture,
        EnableBracketedPaste,
        PushKeyboardEnhancementFlags(KeyboardEnhancementFlags::DISAMBIGUATE_ESCAPE_CODES),
        Hide,
        SetTitle("Kvist"),
    )?;
    install_panic_restore();
    Ok(())
}

pub fn leave() -> io::Result<()> {
    let mut out = io::stdout();
    // Every picture this program placed with its data, the pointer shape, and
    // the default background (changed to blend the padding with the photo).
    out.write_all(b"\x1b_Ga=d,d=A,q=2\x1b\\\x1b]22;default\x1b\\\x1b]111\x1b\\")?;
    execute!(
        out,
        PopKeyboardEnhancementFlags,
        DisableBracketedPaste,
        DisableMouseCapture,
        LeaveAlternateScreen,
        Show,
    )?;
    terminal::disable_raw_mode()
}

fn install_panic_restore() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        if let Err(error) = leave() {
            eprintln!("kvist-glass: could not restore the terminal: {error}");
        }
        previous(info);
    }));
}

/// The pointer shape (OSC 22): a hand over what can be clicked.
pub fn pointer_shape(clickable: bool) -> &'static [u8] {
    if clickable { b"\x1b]22;pointer\x1b\\" } else { b"\x1b]22;default\x1b\\" }
}
