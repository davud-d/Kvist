from __future__ import annotations

import pytest

from banner_frames import (
    BOOT_MS,
    BOOT_RAMP_MS,
    CURSOR_GLYPH,
    CURSOR_MID,
    CURSOR_ON_MS,
    CURSOR_PERIOD_MS,
    DECAY_MS,
    DECAY_RAMP,
    DELETE_LETTER_MS,
    HOLD_MS,
    LETTER_MS,
    PROMPT_GLYPH,
    PROMPT_WHITE,
    STEADY_TEXT_RGB,
    TYPE_RAMP,
    cursor_color_at,
    cycle_ms,
    frame_at,
)

TEXT = "Kvist"
LETTERS = len(TEXT)
CYCLE_MS = cycle_ms(LETTERS)


def at(ms: int):
    """Frame at an absolute millisecond offset."""
    return frame_at(ms / 1000, TEXT)


def test_durations_match_the_prototype():
    assert BOOT_RAMP_MS == 135
    assert BOOT_MS == 735
    assert LETTER_MS == 140
    assert DELETE_LETTER_MS == 180
    assert CURSOR_PERIOD_MS == 1210
    assert CYCLE_MS == 700 + 2500 + 900 + 900 == 5000


def test_boot_ramps_the_prompt_with_the_cursor_parked():
    frame = at(0)
    assert frame[0] == (PROMPT_GLYPH, TYPE_RAMP[0])
    assert frame[1] == (CURSOR_GLYPH, CURSOR_MID)
    assert len(frame) == 2

    assert at(45)[0] == (PROMPT_GLYPH, TYPE_RAMP[1])
    assert at(90)[0] == (PROMPT_GLYPH, PROMPT_WHITE)


def test_prompt_stays_white_after_boot():
    for ms in (BOOT_RAMP_MS, 1000, 3700, 42000):
        assert at(ms)[0] == (PROMPT_GLYPH, PROMPT_WHITE)


def test_boot_blink_has_no_letters():
    frame = at(BOOT_RAMP_MS + 100)
    assert frame[1] == (CURSOR_GLYPH, CURSOR_MID)
    assert set(frame) == {0, 1}


def test_typing_blooms_then_cools():
    assert at(BOOT_MS)[1] == ("K", TYPE_RAMP[0])
    assert at(BOOT_MS + 45)[1] == ("K", TYPE_RAMP[1])
    assert at(BOOT_MS + 90)[1] == ("K", TYPE_RAMP[2])
    assert TYPE_RAMP[2] == STEADY_TEXT_RGB


def test_cursor_leads_the_typed_letters():
    for typed in range(LETTERS):
        frame = at(BOOT_MS + typed * LETTER_MS)
        assert frame[typed + 2] == (CURSOR_GLYPH, CURSOR_MID)
        for settled in range(1, typed + 1):
            assert frame[settled] == (TEXT[settled - 1], STEADY_TEXT_RGB)


def test_hold_shows_the_whole_word():
    frame = at(BOOT_MS + LETTERS * LETTER_MS + 100)
    for slot in range(1, LETTERS + 1):
        assert frame[slot] == (TEXT[slot - 1], STEADY_TEXT_RGB)
    assert frame[1 + LETTERS] == (CURSOR_GLYPH, CURSOR_MID)


def test_delete_burns_letters_back_to_front():
    start = BOOT_MS + LETTERS * LETTER_MS + HOLD_MS

    frame = at(start)
    assert frame[LETTERS] == (TEXT[-1], DECAY_RAMP[0])
    assert frame[LETTERS + 1] == (CURSOR_GLYPH, CURSOR_MID)

    assert at(start + 50)[LETTERS] == (TEXT[-1], DECAY_RAMP[1])
    assert at(start + 100)[LETTERS] == (TEXT[-1], DECAY_RAMP[2])

    frame = at(start + DECAY_MS)
    assert frame[LETTERS] == (CURSOR_GLYPH, CURSOR_MID)
    assert LETTERS + 1 not in frame


def test_delete_keeps_earlier_letters_intact():
    start = BOOT_MS + LETTERS * LETTER_MS + HOLD_MS
    frame = at(start + 2 * DELETE_LETTER_MS)
    for kept in range(1, LETTERS - 2):
        assert frame[kept] == (TEXT[kept - 1], STEADY_TEXT_RGB)


def test_idle_has_no_letters():
    start = BOOT_MS + LETTERS * LETTER_MS + HOLD_MS + LETTERS * DELETE_LETTER_MS
    frame = at(start + 100)
    assert frame[1] == (CURSOR_GLYPH, CURSOR_MID)
    assert set(frame) == {0, 1}


def test_cursor_blink_is_asymmetric():
    assert cursor_color_at(0) == CURSOR_MID
    assert cursor_color_at(CURSOR_ON_MS - 1) == CURSOR_MID
    assert cursor_color_at(CURSOR_ON_MS) == DECAY_RAMP[0]
    assert cursor_color_at(CURSOR_ON_MS + 50) == DECAY_RAMP[1]
    assert cursor_color_at(CURSOR_ON_MS + 100) == DECAY_RAMP[2]
    assert cursor_color_at(CURSOR_ON_MS + DECAY_MS) is None
    assert cursor_color_at(CURSOR_PERIOD_MS) == CURSOR_MID


def test_cycle_repeats_exactly():
    for offset in (0, 300, 1700, 3200, 4999):
        base = at(BOOT_MS + offset)
        assert base == at(BOOT_MS + offset + CYCLE_MS)
        assert base == at(BOOT_MS + offset + 10 * CYCLE_MS)
        assert base == at(BOOT_MS + offset + 1000 * CYCLE_MS)


def test_never_raises_across_two_full_cycles():
    for ms in range(0, BOOT_MS + 2 * CYCLE_MS):
        at(ms)


def test_slots_stay_within_the_grid():
    """The panel reserves 2 + len(text) slots; nothing may fall outside."""
    num_slots = 2 + LETTERS
    for ms in range(0, BOOT_MS + 2 * CYCLE_MS):
        for slot in at(ms):
            assert 0 <= slot < num_slots, f"slot {slot} at {ms} ms"


def test_every_glyph_is_renderable():
    """Every glyph emitted must have a bitmap, or the renderer KeyErrors."""
    allowed = set(TEXT) | {PROMPT_GLYPH, CURSOR_GLYPH}
    for ms in range(0, BOOT_MS + 2 * CYCLE_MS):
        for glyph, _ in at(ms).values():
            assert glyph in allowed, f"stray glyph {glyph!r} at {ms} ms"


def test_letters_never_appear_in_wrong_slots():
    for ms in range(0, BOOT_MS + 2 * CYCLE_MS):
        for slot, (glyph, _) in at(ms).items():
            if glyph not in (PROMPT_GLYPH, CURSOR_GLYPH):
                assert glyph == TEXT[slot - 1], f"slot {slot} at {ms} ms"


def test_exactly_one_cursor_per_frame():
    for ms in range(0, BOOT_MS + 2 * CYCLE_MS):
        cursors = [s for s, (g, _) in at(ms).items() if g == CURSOR_GLYPH]
        assert len(cursors) <= 1, f"{len(cursors)} cursors at {ms} ms"


@pytest.mark.parametrize("text", ["K", "Kv", "Kvist", "abcdefgh"])
def test_works_for_any_word_length(text):
    for ms in range(0, BOOT_MS + cycle_ms(len(text)), 7):
        frame = frame_at(ms / 1000, text)
        for slot in frame:
            assert 0 <= slot < 2 + len(text)


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        frame_at(-0.1, TEXT)
    with pytest.raises(ValueError):
        frame_at(0.0, "")
