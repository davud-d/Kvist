// Takes the terminal over (alternate screen, raw input, mouse, paste) and
// gives it back exactly as it was, also when the program panics.

use std::io::{self, Write};

use crossterm::cursor::{Hide, SetCursorStyle, Show};
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
        SetCursorStyle::BlinkingBar,
        SetTitle("Kvist"),
    )?;
    // The cursor takes the interface's moonlight colour (OSC 12).
    out.write_all(b"\x1b]12;#EFE3C2\x1b\\")?;
    install_panic_restore();
    Ok(())
}

pub fn leave() -> io::Result<()> {
    let mut out = io::stdout();
    // Every picture this program placed with its data, the pointer shape, the cursor colour, and
    // the default background (changed to blend the padding with the photo).
    out.write_all(b"\x1b_Ga=d,d=A,q=2\x1b\\\x1b]22;default\x1b\\\x1b]111\x1b\\\x1b]112\x1b\\")?;
    execute!(
        out,
        SetCursorStyle::DefaultUserShape,
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

/// The pointer shape (OSC 22): a hand over what can be clicked, a bar over text.
pub fn pointer_shape(clickable: bool, text: bool) -> &'static [u8] {
    match (clickable, text) {
        (true, _) => b"\x1b]22;pointer\x1b\\",
        (false, true) => b"\x1b]22;text\x1b\\",
        (false, false) => b"\x1b]22;default\x1b\\",
    }
}

/// Puts `text` on the system clipboard (OSC 52), through the terminal.
pub fn copy_to_clipboard(text: &str) -> Vec<u8> {
    use base64::Engine;
    let encoded = base64::engine::general_purpose::STANDARD.encode(text.as_bytes());
    format!("\x1b]52;c;{encoded}\x1b\\").into_bytes()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_clipboard_gets_the_text_in_base64() {
        assert_eq!(copy_to_clipboard("hi"), b"\x1b]52;c;aGk=\x1b\\");
    }

    #[test]
    fn the_pointer_is_a_hand_before_a_bar() {
        assert_eq!(pointer_shape(true, true), b"\x1b]22;pointer\x1b\\");
        assert_eq!(pointer_shape(false, true), b"\x1b]22;text\x1b\\");
    }
}
