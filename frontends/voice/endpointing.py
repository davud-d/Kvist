# -*- coding: utf-8 -*-
"""Finds the end of a phrase in a stream of audio blocks."""
from __future__ import annotations

import numpy as np
from faster_whisper.vad import VadOptions, get_speech_timestamps

from config import (
    END_SILENCE_COMMAND_SECONDS, END_SILENCE_MAX_SECONDS, END_SILENCE_MIN_SECONDS, END_SILENCE_PER_DICTATED_SECOND,
    END_SILENCE_PER_SPEECH_SECOND, FS, MAX_RECORDING_SECONDS, SPEECH_RMS_THRESHOLD,
)

END_OF_SPEECH = "end_of_speech"
TOO_LONG = "too_long"


# The loudness scale shown on the screen: its ends in decibels relative to full scale.
QUIET_DB = -50.0
LOUD_DB = -10.0


def rms(chunk: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(chunk))))


def is_speech(chunk: np.ndarray) -> bool:
    """Energy gate: a block counts as speech when its RMS reaches the threshold."""
    return rms(chunk) >= SPEECH_RMS_THRESHOLD


def has_voice(audio: np.ndarray) -> bool:
    """Whether a human voice is in `audio` (mono, FS): the energy gate cannot tell speech from a fan or the keys."""
    return bool(get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300), sampling_rate=FS))


def loudness(chunk: np.ndarray) -> float:
    """How loud a block is, 0..1 on a decibel scale: the ear hears ratios, so a linear RMS would sit near zero."""
    level = rms(chunk)
    if level <= 0.0:
        return 0.0
    decibels = 20.0 * float(np.log10(level))
    return min(1.0, max(0.0, (decibels - QUIET_DB) / (LOUD_DB - QUIET_DB)))


def silence_to_end(speech_seconds: float) -> float:
    """The pause that ends a phrase in which `speech_seconds` were said."""
    command = END_SILENCE_MIN_SECONDS + END_SILENCE_PER_SPEECH_SECOND * speech_seconds
    if command <= END_SILENCE_COMMAND_SECONDS:
        return command
    command_speech = (END_SILENCE_COMMAND_SECONDS - END_SILENCE_MIN_SECONDS) / END_SILENCE_PER_SPEECH_SECOND
    dictated = END_SILENCE_COMMAND_SECONDS + END_SILENCE_PER_DICTATED_SECOND * (speech_seconds - command_speech)
    return min(END_SILENCE_MAX_SECONDS, dictated)


class Endpointer:
    """Tracks one phrase: counts its speech and ends it after a pause that grows with it."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Forget the previous phrase."""
        self.elapsed = 0.0
        self.silence = 0.0
        self.speech = 0.0

    def pause_share(self) -> float:
        """How much of the pause that ends the phrase has passed, 0..1."""
        return min(1.0, self.silence / silence_to_end(self.speech))

    def feed(self, chunk: np.ndarray) -> str | None:
        """Take the next block; return a stop reason, or None while the phrase goes on."""
        seconds = len(chunk) / FS
        self.elapsed += seconds

        if is_speech(chunk):
            self.speech += seconds
            self.silence = 0.0
        else:
            self.silence += seconds

        if self.elapsed >= MAX_RECORDING_SECONDS:
            return TOO_LONG
        if self.silence >= silence_to_end(self.speech):
            return END_OF_SPEECH
        return None
