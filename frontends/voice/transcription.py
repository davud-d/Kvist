# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import time

import numpy as np

from config import (
    CONTINUATION_MIN_WORDS, CONTINUATION_SECONDS, FILLERS, FOLLOW_UP_MIN_WORDS, FOLLOW_UP_SECONDS, FS, GATE_BEAM_SIZE, GATE_VAD_FILTER, INITIAL_PROMPT, INITIAL_PROMPTS, LANGUAGE,
    LANGUAGE_SWITCH_FACTOR, LANGUAGE_SWITCH_MIN, LANGUAGES, MAIN_BEAM_SIZE, NOISE_PHRASES,
)
from events import emit_heard, emit_state, emit_text
from wake import strip_wake_word


class TranscriptionMixin:
    def handle_phrase(self, audio: np.ndarray) -> None:
        """Recognize one phrase heard in the room and act on it if it is addressed to Kvist.

        It is addressed to Kvist when it starts with the wake word, or when a
        conversation is open (after the wake word alone, after Kvist answered)
        and it is a real phrase, not a stray word or the noise Whisper writes down.
        """
        language = self.pick_language(audio)
        heard, command = self.listen_for_wake_word(audio, language)
        if command is not None:
            logging.info("Wake word heard")
            self.follow_up_deadline = 0.0
            self.pause_audio()
            if command:
                self.deliver_command(audio, language, wake_heard=True, gate_text=command)
                return
            self.converse(FOLLOW_UP_SECONDS, FOLLOW_UP_MIN_WORDS)
            return

        if time.monotonic() < self.follow_up_deadline and self.is_real_phrase(heard):
            self.follow_up_deadline = 0.0
            self.deliver_command(audio, language, wake_heard=False, gate_text=heard)
            return
        if heard:
            emit_heard(heard)

    def converse(self, seconds: float, min_words: int, announce: bool = True) -> None:
        """Open a conversation: for `seconds`, a phrase of at least `min_words` words needs no wake word.

        `announce` tells the screen (state "listening"); a quiet window changes nothing on it.
        """
        self.follow_up_deadline = time.monotonic() + seconds
        self.follow_up_min_words = min_words
        if announce:
            emit_state("listening")

    def is_real_phrase(self, heard: str) -> bool:
        """Whether `heard` is somebody talking to Kvist, not a stray word, a thinking sound or noise."""
        lowered = heard.lower()
        if any(phrase in lowered for phrase in NOISE_PHRASES):
            return False
        words = re.findall(r"[\w'-]+", lowered)
        if not words or all(word in FILLERS for word in words):
            return False
        return len(words) >= self.follow_up_min_words

    def listen_for_wake_word(self, audio: np.ndarray, language: str | None) -> tuple[str, str | None]:
        """What the gate model heard, and what follows the wake word (None if it did not hear one).

        Whisper sometimes drops a short first word, and which of two ways of
        asking drops it varies with the phrase: measured on 36 spoken variants,
        one pass kept "Квист" 28 to 30 times, the two passes together every time.
        The second pass runs only when the first heard no wake word.
        """
        heard = ""
        for with_prompt in (False, True):
            heard = self.transcribe(self.gate_model, audio, GATE_BEAM_SIZE, language, GATE_VAD_FILTER, with_prompt)
            command = strip_wake_word(heard)
            if command is not None:
                return heard, command
        return heard, None

    def deliver_command(self, audio: np.ndarray, language: str | None, wake_heard: bool, gate_text: str) -> None:
        """Recognize the command with the main model and emit its text as a `text` event."""
        emit_state("transcribing")
        try:
            text = self.transcribe(self.model, audio, MAIN_BEAM_SIZE, language)
            if wake_heard:
                # The main model spells the wake word its own way; if it is not found,
                # the gate model's wording of the command is the fallback.
                stripped = strip_wake_word(text)
                text = stripped if stripped is not None else gate_text

            if not text:
                logging.info("Nothing recognized")
                return

            emit_text(text)
            self.converse(CONTINUATION_SECONDS, CONTINUATION_MIN_WORDS, announce=False)
        except Exception:
            logging.exception("Transcription failed")
        finally:
            self.restore_audio()
            emit_state("idle")

    def expire_follow_up(self) -> None:
        """Give up waiting for a command that did not follow the bare wake word."""
        if not self.follow_up_deadline or time.monotonic() < self.follow_up_deadline:
            return
        self.follow_up_deadline = 0.0
        self.restore_audio()
        emit_state("idle")

    def pick_language(self, audio: np.ndarray) -> str | None:
        """The language of a phrase: the pinned one, else the first allowed one unless another clearly wins."""
        if LANGUAGE:
            return LANGUAGE
        if len(LANGUAGES) <= 1:
            return LANGUAGES[0] if LANGUAGES else None
        _best, _probability, scores = self.gate_model.detect_language(audio=audio)
        by_language = dict(scores)
        chosen = LANGUAGES[0]
        for code in LANGUAGES[1:]:
            likelihood = by_language.get(code, 0.0)
            clearly_better = likelihood >= LANGUAGE_SWITCH_MIN and likelihood >= LANGUAGE_SWITCH_FACTOR * by_language.get(LANGUAGES[0], 0.0)
            if clearly_better and likelihood > by_language.get(chosen, 0.0):
                chosen = code
        logging.info("Language %s (%s)", chosen, ", ".join(f"{code} {by_language.get(code, 0.0):.2f}" for code in LANGUAGES))
        return chosen

    def transcribe(
        self,
        model,
        audio: np.ndarray,
        beam_size: int,
        language: str | None,
        vad_filter: bool = True,
        with_prompt: bool = True,
    ) -> str:
        logging.info("Processing audio with duration %.3f sec", len(audio) / FS)
        # The array is passed directly, so audio from the room never touches the disk.
        segments, info = model.transcribe(
            audio,
            language=language,
            task="transcribe",
            beam_size=beam_size,
            best_of=beam_size,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=vad_filter,
            no_repeat_ngram_size=3,
            repetition_penalty=1.15,
            initial_prompt=INITIAL_PROMPTS.get(language or "", INITIAL_PROMPT) if with_prompt else None,
        )
        parts = [segment.text.strip() for segment in segments if segment.text.strip()]
        text = " ".join(parts).strip()
        logging.info("Recognized: %s", text)
        return text

    @staticmethod
    def _dedupe_text(text: str) -> str:
        if not text:
            return text

        # 1) Collapse consecutive identical words: "привет привет" -> "привет"
        words = text.split()
        collapsed: list[str] = []
        for word in words:
            if collapsed and collapsed[-1].lower() == word.lower():
                continue
            collapsed.append(word)
        result = " ".join(collapsed)

        # 2) Drop an exact doubling of the whole text: "фраза фраза" -> "фраза"
        stripped = result.strip()
        half = len(stripped) // 2
        if len(stripped) % 2 == 1 and stripped[half] == " ":
            left = stripped[:half].strip()
            right = stripped[half + 1:].strip()
            if left and left == right:
                result = left

        return result.strip()
