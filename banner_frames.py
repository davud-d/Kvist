"""Banner animation as a pure function of time.

The prototype in `kvist_banner_demo_v5.py` lives the animation: it sleeps
between frames and writes each one to stdout. That is impossible under
prompt_toolkit, which owns the screen and decides when to redraw.

So the state machine is re-expressed as arithmetic. Give `frame_at` an elapsed
time and it returns the frame belonging to that instant. No sleeping, no
writing, no hidden state -- which also means it is testable without a terminal.

Timeline (BOOT runs once, the rest loops forever):

    0                     BOOT_RAMP_MS  BOOT_MS                BOOT + n*CYCLE
    |------ `>` ramp ----------|- blink -|--- TYPE HOLD DELETE IDLE ---| ...

Slot numbering: 0 holds the `>` prompt, slots 1..len(text) hold the letters,
and the cursor sits in whichever slot it has reached. Slots absent from the
returned mapping are background; the renderer clears the panel before drawing,
so erasing costs nothing here.

Every duration is an integer number of milliseconds. The prototype's float
seconds put phase boundaries on values like 0.56 / 0.14 == 3.9999999999999996,
which lands one step early and makes any boundary assertion flaky. Integers
remove that whole class of bug; 1 ms resolution against a 45 ms frame leaves a
45x margin.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Palette and glyphs, copied verbatim from the prototype.
# --------------------------------------------------------------------------- #

Rgb = tuple[int, int, int]

CURSOR_GLYPH = "\x00"
PROMPT_GLYPH = ">"

PROMPT_WHITE: Rgb = (255, 255, 255)
STEADY_TEXT_RGB: Rgb = (126, 238, 255)
CURSOR_MID: Rgb = (126, 190, 215)

# bloom: a freshly typed glyph flashes white, then cools to the settled cyan.
TYPE_RAMP: tuple[Rgb, ...] = ((255, 255, 255), (200, 240, 255), STEADY_TEXT_RGB)

# burn-in: an erased glyph decays toward the panel background, each step
# roughly halving the remaining distance, like CRT phosphor.
DECAY_RAMP: tuple[Rgb, ...] = ((96, 170, 205), (58, 112, 150), (36, 78, 118))

# --------------------------------------------------------------------------- #
# Timings, in milliseconds. Values match the prototype's float seconds.
# --------------------------------------------------------------------------- #

TYPE_FRAME_MS = 45
TYPE_GAP_MS = 50

DECAY_FRAME_MS = 50
DECAY_GAP_MS = 30

HOLD_MS = 2500
IDLE_MS = 900

CURSOR_ON_MS = 530      # VT100 cursor blink period.
CURSOR_OFF_MS = 530

BOOT_BLINK_MS = 600

# --------------------------------------------------------------------------- #
# Derived durations.
# --------------------------------------------------------------------------- #

# `boot`: the `>` flashes in through three frames, then settles to white.
BOOT_APPEARANCE: tuple[Rgb, ...] = (TYPE_RAMP[0], TYPE_RAMP[1], PROMPT_WHITE)
BOOT_RAMP_MS = len(BOOT_APPEARANCE) * TYPE_FRAME_MS          # 135
BOOT_MS = BOOT_RAMP_MS + BOOT_BLINK_MS                       # 735

# `type_letter`: two cooling frames plus the gap before the next letter.
LETTER_MS = 2 * TYPE_FRAME_MS + TYPE_GAP_MS                  # 140

# `delete_letter`: the decay ramp plus the gap before the next deletion.
DECAY_MS = len(DECAY_RAMP) * DECAY_FRAME_MS                  # 150
DELETE_LETTER_MS = DECAY_MS + DECAY_GAP_MS                   # 180

# `blink_cursor`: instant rising edge, phosphor tail, darkness.
CURSOR_PERIOD_MS = CURSOR_ON_MS + DECAY_MS + CURSOR_OFF_MS   # 1210


def cycle_ms(letters: int) -> int:
    """Length of one TYPE -> HOLD -> DELETE -> IDLE loop, in milliseconds."""
    return (
        letters * LETTER_MS
        + HOLD_MS
        + letters * DELETE_LETTER_MS
        + IDLE_MS
    )


# --------------------------------------------------------------------------- #
# The animation.
# --------------------------------------------------------------------------- #

Frame = dict[int, tuple[str, Rgb]]


def cursor_color_at(elapsed_ms: int) -> Rgb | None:
    """Cursor colour `elapsed_ms` into a blink, or None while dark.

    Asymmetric on purpose: the phosphor lights up instantly and then decays
    through DECAY_RAMP, instead of a hard on/off blink.
    """
    phase = elapsed_ms % CURSOR_PERIOD_MS

    if phase < CURSOR_ON_MS:
        return CURSOR_MID

    phase -= CURSOR_ON_MS
    if phase < DECAY_MS:
        return DECAY_RAMP[phase // DECAY_FRAME_MS]

    return None


def frame_at(elapsed: float, text: str) -> Frame:
    """Return {slot: (glyph, rgb)} for the animation state at `elapsed` seconds.

    Pure function of time. Slots not present in the result are background.
    """
    if elapsed < 0.0:
        raise ValueError(f"elapsed must not be negative, got {elapsed!r}")
    if not text:
        raise ValueError("text must not be empty")

    # round(), not int(): 0.029 * 1000 is 28.999999999999996 in binary float.
    now = round(elapsed * 1000)
    letters = len(text)
    frame: Frame = {}

    # BOOT ramp: the `>` flashes in with the cursor already parked at slot 1.
    if now < BOOT_RAMP_MS:
        frame[0] = (PROMPT_GLYPH, BOOT_APPEARANCE[now // TYPE_FRAME_MS])
        frame[1] = (CURSOR_GLYPH, CURSOR_MID)
        return frame

    # From here on the `>` is settled and never redrawn.
    frame[0] = (PROMPT_GLYPH, PROMPT_WHITE)

    # BOOT blink: a short blink before the first letter is typed.
    if now < BOOT_MS:
        color = cursor_color_at(now - BOOT_RAMP_MS)
        if color is not None:
            frame[1] = (CURSOR_GLYPH, color)
        return frame

    phase = (now - BOOT_MS) % cycle_ms(letters)

    # TYPE: letters bloom white and cool to cyan, one after another.
    type_ms = letters * LETTER_MS
    if phase < type_ms:
        settled, within = divmod(phase, LETTER_MS)

        for slot in range(1, settled + 1):
            frame[slot] = (text[slot - 1], STEADY_TEXT_RGB)

        slot = settled + 1
        step = min(within // TYPE_FRAME_MS, len(TYPE_RAMP) - 1)
        frame[slot] = (text[slot - 1], TYPE_RAMP[step])
        frame[slot + 1] = (CURSOR_GLYPH, CURSOR_MID)
        return frame
    phase -= type_ms

    # HOLD: the full word sits there while the cursor blinks past it.
    if phase < HOLD_MS:
        for slot in range(1, letters + 1):
            frame[slot] = (text[slot - 1], STEADY_TEXT_RGB)
        color = cursor_color_at(phase)
        if color is not None:
            frame[1 + letters] = (CURSOR_GLYPH, color)
        return frame
    phase -= HOLD_MS

    # DELETE: letters burn out back to front, the cursor retreating behind them.
    delete_ms = letters * DELETE_LETTER_MS
    if phase < delete_ms:
        removed, within = divmod(phase, DELETE_LETTER_MS)
        slot = letters - removed

        for kept in range(1, slot):
            frame[kept] = (text[kept - 1], STEADY_TEXT_RGB)

        if within < DECAY_MS:
            # Still fading. The cursor has not moved yet: it sits one slot
            # ahead, left there by the previous stage, always at CURSOR_MID.
            frame[slot] = (text[slot - 1], DECAY_RAMP[within // DECAY_FRAME_MS])
            frame[slot + 1] = (CURSOR_GLYPH, CURSOR_MID)
        else:
            # Faded out: the letter is gone and the cursor has taken its slot.
            frame[slot] = (CURSOR_GLYPH, CURSOR_MID)
        return frame
    phase -= delete_ms

    # IDLE: empty line, cursor blinking at slot 1, waiting to start over.
    color = cursor_color_at(phase)
    if color is not None:
        frame[1] = (CURSOR_GLYPH, color)
    return frame
