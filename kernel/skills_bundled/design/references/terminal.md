# Terminal interfaces: CLI output and full-screen TUIs

A terminal is a grid of character cells, a byte stream in both directions,
and a set of conventions older than most of the people using it. Its users
are often experts with strong habits, often on a remote machine, sometimes
with a screen reader, sometimes in a 80×24 window. Design for all of them.

Part 1 covers command-line programs (help, output, prompts, progress,
errors). Part 2 covers full-screen TUIs. Part 3 is shared: colour, glyphs,
width, input. Part 4 is how to test.

## Part 1. Command-line programs

### Help

- `-h` and `--help` always show help, whatever else is on the line, and exit 0.
- Running a command that needs arguments with none shows a short help: one
  line on what it does, one or two examples, the most common flags, and how
  to get the full help. Do not hang waiting for stdin when stdin is a
  terminal and the program expected a pipe; show help instead.
- Full help: usage line, description, **examples first**, then flags grouped
  by purpose with the common ones on top, then environment variables and
  config files, then a link to docs and to the issue tracker.
- Git-style tools support `help`, `help <cmd>` and `<cmd> --help`.
- On a likely typo, suggest the right command ("Did you mean `status`?") and
  let the person run it; never silently run the guess.
- Help must read well without colour and when piped to `less` or a file.

### Arguments, flags and naming

- Flags over positional arguments when there is more than one thing; a flag
  says what it is and can be added later without breaking anyone.
- Every flag has a long form; short forms only for the frequent ones.
- Standard names where they exist: `-a/--all`, `-d/--debug`, `-f/--force`,
  `-n/--dry-run`, `-o/--output`, `-q/--quiet`, `-v/--verbose`, `--json`,
  `--version`, `--no-input`, `--no-color`. Do not make `-v` mean version.
- `-` means stdin or stdout where a file is expected.
- Secrets never as flag values (they land in shell history and `ps`): read
  them from a file, stdin, a prompt with no echo, or the system keychain.
- Command names: short, lowercase, easy to type, no clash with common tools;
  subcommands consistently `noun verb` or `verb noun`, never both; no near
  synonyms side by side (`update` and `upgrade`).
- The default behaviour is the right one for most people; do not hide the
  common case behind a flag.

### Output

- **Human first when stdout is a TTY; machine-friendly when it is not.**
  Detect with `isatty` on each stream separately.
- Results to stdout; progress, warnings and errors to stderr, so a pipe gets
  only data and the person still sees what is going on.
- `--json` for structured output (stable field names, documented, versioned
  by addition only); `--plain` for one record per line without decoration
  when the pretty table would break `grep` and `awk`.
- Say something within about 100 ms. Before a network request, print what is
  being fetched; silence looks like a hang.
- On success, be brief: one line saying what changed and the new state. A
  program that says nothing on success makes people wonder; one that prints
  forty lines buries the result. `-q` silences the rest.
- After a state change, suggest the natural next command
  (`Created project "atlas". Next: cd atlas && atlas dev`).
- A `status` style command shows where things stand and what to do next.
- Say when the program reaches beyond what the person named: writing files
  they did not mention, contacting a server, modifying a shell config.
- Tables: align columns, right-align numbers, truncate long cells with an
  ellipsis (in the middle for paths: `~/proj/…/src/main.rs`), keep headers
  short, and never wrap a row onto two lines without making it obvious.
- Use a pager for long output only when stdout is a TTY (`less -FIRX` is a
  good default: quits if it fits, keeps colour, does not clear the screen).
- Dates: relative for recent events in human output ("3 min ago"), absolute
  ISO 8601 in machine output and logs.

### Errors

- Rewrite expected errors into plain language with the fix: not
  `ENOENT: no such file or directory, open 'kvist.json'` but
  `No kvist.json in /home/a/proj. Run "kvist init" to create one.`
- The most important line goes last, where the eye lands after the output
  stops.
- Group repeated errors ("12 files could not be read: …") instead of printing
  twelve stack traces.
- Unexpected errors: a short summary, where the full trace went (a log
  file), and how to report it; ideally a prefilled issue URL.
- Exit codes: 0 success, non-zero failure, distinct codes for distinct
  failure kinds the caller might handle (usage error conventionally 2), and
  document them. 130 after Ctrl+C.
- Red only for errors; yellow for warnings; never an error message that is
  only coloured.

### Prompts and interactivity

- Prompt only when stdin is a TTY. Every prompt has a flag or argument that
  answers it, so the command works in scripts and CI. With `--no-input` (or
  no TTY) fail fast with the flag the person should pass.
- Show the default in the prompt and accept Enter for it: `Overwrite? [y/N]`
  (the capital marks the default, and the default is the safe answer).
- Passwords and keys: no echo, or masked with the last four characters
  visible so the person can check what they pasted. Accept a paste.
- Confirm in proportion to damage: deleting one local file needs no prompt;
  deleting a directory or a remote resource asks; deleting a whole
  environment asks the person to type its name, and offers
  `--confirm=<name>` for scripts. Offer `--dry-run` for anything large.
- Selection lists: arrows and j/k, typing filters the list, Enter chooses,
  Esc cancels, the current value is preselected, the list shows its position
  ("11–18 of 41") when it scrolls.

### Progress

- Under about 1 s: nothing. 1–10 s: a spinner with a word on what is
  happening. Over 10 s, or when the size is known: a progress bar with a count
  ("34/120 files"), the current item, elapsed time and, once it is stable,
  remaining time.
- Show running output in absolute terms when the total is unknown ("4 812
  rows imported").
- A bar that stops moving looks like a crash; if a step can stall, say so
  after a few seconds ("Still waiting for registry.npmjs.org, 12 s").
- Hide detailed logs behind progress on success, show them on failure.
- No spinners, bars or cursor movement when the stream is not a TTY or
  `TERM=dumb`: print one line per milestone instead. A spinner redrawn ten
  times a second also floods screen readers; offer `--plain` or detect it.
- Parallel tasks: one line per task with its own state, completed lines
  collapse to a summary.

### Ctrl+C and signals

- The first Ctrl+C stops as soon as possible: print what is happening
  ("Stopping… press Ctrl+C again to force"), clean up with a timeout, exit 130.
- A second Ctrl+C skips cleanup; say first what that leaves behind.
- Any interrupted operation can be run again: idempotent steps, temporary
  files renamed into place, cleanup of leftovers on the next start.
- Restore the terminal on every exit path, including crashes: cursor visible,
  echo and canonical mode back, mouse and bracketed-paste modes off, the
  alternate screen left. A program that leaves the shell in raw mode makes
  the person type `reset` blind.

### Configuration

- Precedence, highest first: flags, environment variables, project config,
  user config (`$XDG_CONFIG_HOME/<app>/` or `~/.config/<app>/`, `%APPDATA%` on
  Windows), system config, built-in defaults.
- Ask before editing a file that is not yours (a shell rc file, a global git
  config), say exactly which line, mark what you add with a comment, make the
  edit idempotent, and make it reversible by an uninstall.
- No telemetry without consent; if any is collected, say what, where it goes
  and how to turn it off, on first run and in the docs.

## Part 2. Full-screen TUIs

### Layout

- Design for 80×24 as the minimum that must work and for 200×50 as the size
  experts actually use. Below the minimum, show one clear line ("Window too
  small: 60×18, needs 80×24") rather than a broken layout.
- Plan what goes when space runs out. When the window is too short, drop the
  least important content first (decoration, then explanations, then
  secondary panels) and never the actions or the input. When it is too
  narrow, stack panels, hide the sidebar behind a key, and truncate with an
  ellipsis rather than wrap labels.
- Reflow on resize (SIGWINCH, or the framework's resize event) immediately and
  without losing scroll position or selection.
- Regions: a header line with where you are, the main area, a status line,
  and a key-hint footer is the shape people expect. Borders cost two columns
  and two rows each; use them to separate regions that scroll independently,
  not around everything. A blank column or a dim rule is often enough.
- Align to cell columns: right-align numbers, keep a consistent left edge,
  pad labels to a fixed width in lists of key/value pairs.

### Focus, selection and navigation

- One focused element at a time, always visible **without colour alone**:
  reverse video, a `›` or `▌` marker, a bold border, or a changed border
  style. A theme with low contrast, or `NO_COLOR`, must still show focus.
- Tab and Shift+Tab move between regions; arrows move within one; Enter
  opens or confirms; Esc goes back one level or closes; `q` quits from the
  top level (and asks only if work would be lost).
- Familiar keys: arrows and `j`/`k`/`h`/`l`, `g`/`G` or Home/End, PgUp/PgDn,
  `/` to search, `n`/`N` next and previous match, `?` for help, Space to
  toggle, `:` or Ctrl+P for a command palette in larger tools. Do not reassign
  Ctrl+C, Ctrl+Z or Ctrl+D to something surprising.
- Keys for the current context are on the screen (a footer like
  `↑↓ move  Enter open  / search  ? help  q quit`). A key the person must
  remember from documentation is a key they will not use.
- `?` opens a full key list for the current screen; the list closes on Esc
  or `?`.
- A destructive key needs a second step: a confirmation line, or an "arm then
  confirm" pattern (press once to show "press Esc again to stop", which
  expires on its own after a few seconds).

### Feedback in a TUI

- Every key press changes something visible within 100 ms, even if the work
  behind it takes longer (a spinner in the status line, a dimmed row).
- Status messages stay long enough to be read: hold each one for at least
  about 2 seconds before the next replaces it. Implement the hold in the UI
  with a timer, never as a sleep in the worker; the work must not wait for
  the message.
- Long-running work shows elapsed time and what it is doing now, and after a
  few seconds of no change says how long it has been waiting.
- When text streams in faster than people read (model output, logs), reveal
  it at a steady pace or in whole lines; jittery bursts are hard to follow.
- Keep finished output readable: if a live region is height-limited, move
  completed blocks into the scrollback or transcript as they finish, so the
  beginning of a long answer is not scrolled out of a box with no way back.
- A finished turn or job ends with a quiet line that says it is finished, how
  long it took, and what to do next.

### Rendering without flicker

- Never clear the whole screen and redraw it on every update. Compute the
  rows that changed and write only those (cursor to the row, write, erase to
  end of line). Most flicker reports are full redraws.
- Wrap each frame in synchronized output (`CSI ? 2026 h` … `CSI ? 2026 l`);
  terminals that support it paint the frame at once, others ignore it.
- Hide the cursor while drawing, show it again at the input position.
- Throttle redraws to what the eye can use (about 30 fps for animation, a few
  per second for counters) and coalesce updates that arrive within one frame.
- A spinner or animation keeps running only while something is actually
  happening.

### Alternate screen or inline

- **Inline** (writing into the normal scrollback, redrawing only a small live
  region at the bottom) keeps the history in the terminal's own scrollback,
  copy and search. Good for REPLs, chat-like tools and progress output.
- **Alternate screen** (full-screen, like `less`, `htop`, editors) gives you
  the whole window but no terminal scrollback: you must implement scrolling,
  search and copy yourself. When it exits, everything shown is gone, so print
  a short summary on the normal screen afterwards (what happened, how to
  resume, where the log is).
- Choose per product and keep it. Mixing them mid-session confuses people
  and terminal multiplexers.

### Mouse

- Optional, never required: every mouse action also has a key.
- If the app takes the mouse (SGR mode 1006 with 1000/1002/1003), the terminal
  stops doing native selection; then implement selection and copy yourself,
  and keep Shift+drag passing through to the terminal (most terminals do this
  by default).
- Clicks act on release over the same target, so a press that slides off
  cancels; show hover and pressed states when the terminal reports motion.
- Wheel scrolls the region under the pointer, not always the main one.

### Images and rich graphics

- Some terminals display real images (the kitty graphics protocol in kitty,
  Ghostty, Konsole; Sixel in Windows Terminal 1.22+, WezTerm, foot, mintty).
  Detect them (environment variables for kitty, a device attributes query for
  Sixel, with a short timeout), and keep a text or cell-art fallback; never
  depend on images for meaning.
- Query the terminal (cell size, colours, capabilities) before your input
  loop owns stdin, with a timeout of a few hundred milliseconds, and always
  end the query with a request every terminal answers (DA1, `CSI c`) so you
  know when to stop waiting.
- Under tmux and screen, many queries and graphics need passthrough or do not
  work at all; treat them as unsupported unless detected.

## Part 3. Shared terminal facts

### Colour

- Decide colour per stream: off when the stream is not a TTY, when `NO_COLOR`
  is set to anything non-empty, when `TERM=dumb`, or with `--no-color`; on
  when `FORCE_COLOR` is set or with `--color=always`. Offer
  `--color=auto|always|never`.
- Depth: `COLORTERM=truecolor` or `24bit` means 24-bit colour; a `TERM`
  ending in `-256color` means 256; otherwise assume the 16 ANSI colours. ssh
  often drops `COLORTERM`, so a 256-colour fallback for every truecolor value
  matters. Map to the nearest xterm-256 entry, not by truncating.
- **The 16 ANSI colours are the person's theme.** Their actual values come
  from the terminal's palette, so text in "red" or "blue" follows a light or
  dark theme, a high-contrast theme, a colour-blind-friendly theme. Use them
  for semantic colour (errors, warnings, success, links). Use truecolor for
  brand surfaces only when you also control the background behind it.
- Prefer the terminal's default foreground and background. Painting a
  background colour on a full-screen app is fine when the whole palette is
  designed around it; painting it on an inline tool fights the person's theme.
- Light and dark backgrounds both exist. If you need to know, query the
  background (OSC 11) with a timeout, or read `COLORFGBG`; otherwise choose
  colours that work on both (bright yellow on white is unreadable; dark blue
  on black is unreadable).
- `dim`/faint and italic are not supported everywhere (some consoles show
  dim as normal); never let dim be the only difference between two states.
- 16-colour terminals lose subtle shades: two greys that differ at truecolor
  collapse into one. Anything that carries meaning must survive at 16 colours
  and at no colour at all.
- Keep red for errors and destructive actions; give success a word as well as
  green; focus and selection use reverse video or markers as well as colour.

### Glyphs and width

- A cell holds one narrow character. East Asian wide characters and most
  emoji take two cells; combining marks take zero; ZWJ emoji sequences and
  characters with variation selectors are measured differently by different
  terminals. Measure with a `wcwidth` implementation (Python `wcwidth`,
  JavaScript `string-width`), never with `len()`.
- Truncate by cells, never in the middle of a wide character, a combining
  sequence or an escape sequence.
- Safe almost everywhere with a UTF-8 locale: box drawing (`─│┌┐└┘├┤`),
  block elements (`█▌▐▀▄`), arrows (`←↑→↓`), `✓ ✗ • … ›`. Riskier: braille
  spinners (font dependent), quadrant blocks (`▖▗▘▝`), octants (Unicode 16,
  very few fonts), Nerd Font icons (only when the person has a Nerd Font:
  detect a known terminal or let them opt in), emoji (width and colour vary).
- Provide an ASCII fallback for `LANG=C`, `TERM=linux` (the Linux console has
  a 256- or 512-glyph font) and Windows legacy consoles: `-` `|` `+` for boxes,
  `*` for bullets, `[x]` for checks, `...` for the ellipsis.
- Wrap at word boundaries by cell width; keep URLs and paths unbroken where
  possible; hyperlinks via OSC 8 where supported, with the URL also visible in
  plain output.

### Input

- Escape is also the prefix of Alt combinations and of every arrow key. Wait
  briefly (about 25–50 ms) after a lone Esc before treating it as Escape; and
  inside a text field consider Esc as "clear" or "cancel" only when the field
  is empty or the meaning is unambiguous.
- Many combinations cannot be told apart in a classic terminal: Ctrl+I is
  Tab, Ctrl+M is Enter, Ctrl+[ is Esc, Ctrl+Shift+letter is Ctrl+letter, and
  Ctrl+Enter or Shift+Enter often send plain Enter. Bind essential actions to
  keys that work everywhere; the kitty keyboard protocol adds the rest where
  supported.
- Enable bracketed paste so a pasted text with newlines is inserted, not
  executed line by line; accept large pastes without freezing.
- Respect the person's editor (`$VISUAL`, `$EDITOR`) for long text input.
- Input methods (IME) for Chinese, Japanese and Korean need the cursor at the
  real insertion point; keep the terminal cursor where the text goes.
- Keyboard layouts: letters shift on AZERTY, Cyrillic and Dvorak; `j`/`k`
  under a Russian layout arrive as `о`/`л`. Consider accepting both, and never
  make a key that needs AltGr on common layouts the only path.

### Accessibility in terminals

- Screen readers read the terminal's text buffer line by line. Constant
  redraws, spinners and moving cursors make them speak noise. Offer a plain
  mode (`--plain`, or detect `TERM=dumb`) with one line per event and no
  cursor movement.
- Contrast still counts: the ANSI palette of the person's theme helps; fixed
  truecolor text on a fixed background must meet the same contrast as on the
  web (see `visual.md`).
- No information in colour or position alone; status has a word.
- Never require the mouse, rapid key repetition or timed responses.

## Part 4. Testing a terminal UI

### Seeing what the program shows

Reading a program's raw output is not seeing its screen: cursor moves,
partial redraws and the alternate screen make the byte stream lie. Run it in
a pseudo-terminal and replay the stream into a screen. In Kvist, every
`run_shell` call is its own sandbox, so a `tmux` session started by one call
is gone by the next, and `python3 -c` takes one argument of at most about
2000 characters; the harness below is therefore a file. Write it once to
`.design-check/tui_snapshot.py` (tell the person in the report that this
helper folder exists and can be deleted), then run it with one JSON argument:

```
python3 .design-check/tui_snapshot.py '{"command": ["node", "dist/cli.js"], "size": [24, 80], "steps": [["", 2], ["\u001b[B", 0.5], ["\r", 1], ["q", 1]], "env": {"NO_COLOR": "1"}}'
```

Each step sends its keys, waits, and prints the screen. It answers the
terminal queries prompt_toolkit and similar libraries wait for (cursor
position, device attributes), marks reverse video `⟦…⟧` and bold `⟨…⟩` so you
can check that focus and selection are visible without colour, and at exit
reports the terminal modes the program left on (alternate screen, mouse
reporting, bracketed paste, a hidden cursor), each of which leaves the
person's shell broken. Colours are not shown; check those with `NO_COLOR` and
by reading the code.

```python
"""Run a terminal program in a pseudo-terminal and print the screens a person would see.

python3 tui_snapshot.py '{"command": ["less", "README.md"], "size": [24, 80],
                          "steps": [["", 2], ["j", 0.5], ["q", 1]], "env": {"NO_COLOR": "1"}}'

Each step sends its keys ("\\u001b" is Esc, "\\r" Enter, "\\u0003" Ctrl+C, "\\u001b[A" Up), waits
the given seconds, then prints the screen. ⟦…⟧ marks reverse video, ⟨…⟩ bold. Colours are not shown.
"""
import codecs, fcntl, json, os, pty, re, select, signal, struct, sys, termios, time, unicodedata

SPEC = json.loads(sys.argv[1])
COMMAND = SPEC["command"]
ROWS, COLS = SPEC.get("size", [24, 80])
STEPS = SPEC.get("steps", [["", 2]])
ENV = {"TERM": "xterm-256color", **SPEC.get("env", {})}
MARKS = "\x01\x02"
BRACKETS = {"\x01": ("⟦", "⟧"), "\x02": ("⟨", "⟩")}
CSI = re.compile(r"\x1b\[([?>=!]?)([0-9;:]*)[ -/]*([@-~])")
STRING = re.compile(r"\x1b[\]P_^X].*?(?:\x07|\x1b\\)", re.S)


def cells(ch):
    if unicodedata.combining(ch) or ch in "​‌‍︎️":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


class Screen:
    def __init__(self):
        self.main = None
        self.modes = set()
        self.cursor_hidden = False
        self.reverse = self.bold = False
        self.reset()

    def reset(self):
        self.grid = [[" "] * COLS for _ in range(ROWS)]
        self.y = self.x = 0
        self.top, self.bottom = 0, ROWS - 1
        self.saved = (0, 0)

    def scroll(self, n=1, up=True):
        for _ in range(n):
            if up:
                del self.grid[self.top]
                self.grid.insert(self.bottom, [" "] * COLS)
            else:
                del self.grid[self.bottom]
                self.grid.insert(self.top, [" "] * COLS)

    def newline(self):
        if self.y == self.bottom:
            self.scroll()
        else:
            self.y = min(ROWS - 1, self.y + 1)

    def put(self, ch):
        w = cells(ch)
        if w == 0:
            if self.x:
                self.grid[self.y][self.x - 1] += ch
            return
        if self.x + w > COLS:
            self.x = 0
            self.newline()
        self.grid[self.y][self.x] = (MARKS[0] if self.reverse else MARKS[1] if self.bold else "") + ch
        if w == 2 and self.x + 1 < COLS:
            self.grid[self.y][self.x + 1] = ""
        self.x += w

    def erase(self, row, start, end):
        for col in range(max(0, start), min(COLS, end)):
            self.grid[row][col] = " "

    def csi(self, private, params, final, reply):
        args = [int(p) if p.isdigit() else 0 for p in params.replace(":", ";").split(";")] if params else []
        n = args[0] if args and args[0] else 1
        if private == "?" and final in "hl":
            for mode in args:
                (self.modes.add if final == "h" else self.modes.discard)(mode)
                if mode == 25:
                    self.cursor_hidden = final == "l"
                if mode in (47, 1047, 1049):
                    if final == "h" and self.main is None:
                        self.main = (self.grid, self.y, self.x)
                        self.reset()
                    elif final == "l" and self.main is not None:
                        self.grid, self.y, self.x = self.main
                        self.main = None
            return
        if final == "m" and not private:
            codes, i = args or [0], 0
            while i < len(codes):
                if codes[i] in (38, 48, 58):
                    i += 3 if codes[i + 1:i + 2] == [5] else 5
                    continue
                code = codes[i]
                if code in (0, 27):
                    self.reverse = False
                if code in (0, 22):
                    self.bold = False
                if code in (7, 1):
                    setattr(self, "reverse" if code == 7 else "bold", True)
                i += 1
            return
        if private:
            return
        if final == "A": self.y = max(self.top if self.y >= self.top else 0, self.y - n)
        elif final == "B": self.y = min(self.bottom if self.y <= self.bottom else ROWS - 1, self.y + n)
        elif final == "C": self.x = min(COLS - 1, self.x + n)
        elif final == "D": self.x = max(0, self.x - n)
        elif final == "E": self.y, self.x = min(ROWS - 1, self.y + n), 0
        elif final == "F": self.y, self.x = max(0, self.y - n), 0
        elif final == "G": self.x = min(COLS - 1, n - 1)
        elif final == "d": self.y = min(ROWS - 1, n - 1)
        elif final in "Hf":
            self.y = min(ROWS - 1, (args[0] if args and args[0] else 1) - 1)
            self.x = min(COLS - 1, (args[1] if len(args) > 1 and args[1] else 1) - 1)
        elif final == "J":
            mode = args[0] if args else 0
            if mode == 0:
                self.erase(self.y, self.x, COLS)
                for row in range(self.y + 1, ROWS): self.erase(row, 0, COLS)
            elif mode == 1:
                self.erase(self.y, 0, self.x + 1)
                for row in range(0, self.y): self.erase(row, 0, COLS)
            else:
                for row in range(ROWS): self.erase(row, 0, COLS)
        elif final == "K":
            mode = args[0] if args else 0
            self.erase(self.y, *((self.x, COLS), (0, self.x + 1), (0, COLS))[min(mode, 2)])
        elif final == "X": self.erase(self.y, self.x, self.x + n)
        elif final == "P":
            row = self.grid[self.y]
            del row[self.x:self.x + n]
            row.extend([" "] * (COLS - len(row)))
        elif final == "@":
            row = self.grid[self.y]
            row[self.x:self.x] = [" "] * n
            del row[COLS:]
        elif final in "LM" and self.top <= self.y <= self.bottom:
            saved_top, self.top = self.top, self.y
            self.scroll(n, up=final == "M")
            self.top = saved_top
        elif final == "S": self.scroll(n)
        elif final == "T": self.scroll(n, up=False)
        elif final == "r":
            self.top = (args[0] if args and args[0] else 1) - 1
            self.bottom = (args[1] if len(args) > 1 and args[1] else ROWS) - 1
            self.y = self.x = 0
        elif final == "s": self.saved = (self.y, self.x)
        elif final == "u": self.y, self.x = self.saved
        elif final == "n" and args == [6]: reply(f"\x1b[{self.y + 1};{self.x + 1}R")
        elif final == "c": reply("\x1b[?62;22c")

    def feed(self, text, reply):
        text = STRING.sub("", text)
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b":
                match = CSI.match(text, i)
                if match:
                    self.csi(*match.groups(), reply)
                    i = match.end()
                    continue
                nxt = text[i + 1:i + 2]
                if nxt in "()*+":
                    i += 3
                    continue
                if nxt == "7": self.saved = (self.y, self.x)
                elif nxt == "8": self.y, self.x = self.saved
                elif nxt == "M":
                    if self.y == self.top: self.scroll(up=False)
                    else: self.y = max(0, self.y - 1)
                elif nxt in "DE":
                    self.newline()
                    if nxt == "E": self.x = 0
                elif nxt == "c":
                    self.reset()
                i += 2
                continue
            if ch == "\r": self.x = 0
            elif ch in "\n\x0b\x0c": self.newline()
            elif ch == "\b": self.x = max(0, self.x - 1)
            elif ch == "\t": self.x = min(COLS - 1, (self.x // 8 + 1) * 8)
            elif ch >= " ": self.put(ch)
            i += 1

    def text(self):
        lines = []
        for row in self.grid:
            line, inside = "", ""
            for cell in row:
                mark = cell[:1] if cell[:1] in MARKS else ""
                if mark != inside:
                    line += BRACKETS.get(inside, ("", ""))[1] + BRACKETS.get(mark, ("", ""))[0]
                    inside = mark
                line += cell[len(mark):]
            lines.append((line + BRACKETS.get(inside, ("", ""))[1]).rstrip())
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)


pid, fd = pty.fork()
if pid == 0:
    fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    try:
        os.execvpe(COMMAND[0], COMMAND, {**os.environ, "LINES": str(ROWS), "COLUMNS": str(COLS), **ENV})
    except OSError as error:
        print(f"cannot start {COMMAND[0]}: {error}", flush=True)
    os._exit(127)
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
screen, alive = Screen(), True
decoder = codecs.getincrementaldecoder("utf-8")("replace")


def pump(seconds):
    global alive
    end = time.monotonic() + seconds
    while alive and time.monotonic() < end:
        ready, _, _ = select.select([fd], [], [], max(0.0, end - time.monotonic()))
        if not ready:
            continue
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        if not data:
            alive = False
            break
        screen.feed(decoder.decode(data), lambda answer: os.write(fd, answer.encode()))


for keys, seconds in STEPS:
    if keys and alive:
        os.write(fd, keys.encode())
    pump(seconds)
    label = repr(keys) if keys else "start"
    print(f"=== after {label}, {seconds}s{'' if alive else ', program exited'}"
          f"{', alternate screen' if screen.main is not None else ''} ===")
    print(screen.text())
if alive:
    os.kill(pid, signal.SIGTERM)
    time.sleep(0.3)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
status = os.waitpid(pid, 0)[1]
if alive:
    print("=== still running after the last step; stopped by the harness. End with a quit key to check the exit ===")
else:
    left = sorted(screen.modes & {47, 1000, 1002, 1003, 1006, 1047, 1049, 2004})
    left += ["cursor hidden"] if screen.cursor_hidden else []
    print(f"=== exit status {os.waitstatus_to_exitcode(status)}; terminal modes left on: {left or 'none'} ===")
```

It runs on Linux and macOS (it needs `pty`), with the system `python3` and
nothing installed. It does not emulate everything a terminal does (no
colours, no double-width line modes, no images); when its screen looks wrong
in a way the program's own code cannot explain, suspect the harness and say
so instead of "fixing" the program.

### What to run

- Sizes: 80×24, 120×40, and one very wide, each as its own run (the harness
  does not resize a running program; ask the person to try a live resize).
- Environments: `NO_COLOR=1`; `TERM=dumb`; `TERM=xterm-256color` without
  `COLORTERM`; `LANG=C`; a light terminal theme and a dark one (the last one
  only the person can check).
- Plain output: run the CLI with stdout not a TTY (an ordinary `run_shell`
  call already is one) and read it as a script or a screen reader would.
- Keys: walk every action with the keyboard only; press Esc (`\u001b`),
  Ctrl+C (`\u0003`) and Ctrl+D (`\u0004`) in every state, including in the
  middle of long work; paste a multi-line text as a bracketed paste
  (`\u001b[200~first\rsecond\u001b[201~`); type Cyrillic.
- Exit paths: normal quit, Ctrl+C, a crash (raise in the middle). After each,
  the harness's last line must say no terminal modes were left on.
- Snapshot the rendered screens of the main states as text in tests, so a
  layout regression shows as a diff.
- Platforms when you can: a Linux terminal, macOS Terminal.app (256 colours
  on older macOS versions, and it may send the old `ESC [ M` mouse reports
  instead of SGR ones), Windows Terminal; and over ssh.
