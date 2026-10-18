# -*- coding: utf-8 -*-
"""Finds the end of a phrase in a stream of audio blocks."""
from __future__ import annotations

import numpy as np

from config import (
    END_SILENCE_MAX_SECONDS, END_SILENCE_MIN_SECONDS, END_SILENCE_PER_SPEECH_SECOND, FS, MAX_RECORDING_SECONDS,
    SPEECH_RMS_THRESHOLD,
)

END_OF_SPEECH = "end_of_speech"
TOO_LONG = "too_long"


def is_speech(chunk: np.ndarray) -> bool:
    """Energy gate: a block counts as speech when its RMS reaches the threshold."""
    return float(np.sqrt(np.mean(np.square(chunk)))) >= SPEECH_RMS_THRESHOLD


def silence_to_end(speech_seconds: float) -> float:
    """The pause that ends a phrase in which `speech_seconds` were said."""
    return min(END_SILENCE_MAX_SECONDS, END_SILENCE_MIN_SECONDS + END_SILENCE_PER_SPEECH_SECOND * speech_seconds)


class Endpointer:
    """Tracks one phrase: counts its speech and ends it after a pause that grows with it."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Forget the previous phrase."""
        self.elapsed = 0.0
        self.silence = 0.0
        self.speech = 0.0

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
