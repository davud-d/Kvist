# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import time

import numpy as np

from config import (
    CONTINUATION_MIN_WORDS, CONTINUATION_SECONDS, FILLERS, FOLLOW_UP_MIN_WORDS, FOLLOW_UP_SECONDS, FS, GATE_BEAM_SIZE,
    GATE_HEAD_SECONDS, GATE_VAD_FILTER, INITIAL_PROMPT, INITIAL_PROMPTS, JOIN_GRACE_MAX_SECONDS, JOIN_GRACE_PER_SECOND,
    JOIN_WAIT_MAX_SECONDS, LANGUAGE, LANGUAGE_SWITCH_FACTOR, LANGUAGE_SWITCH_MIN, LANGUAGES, MAIN_BEAM_SIZE,
    NOISE_PHRASES, BLOCK_SECONDS, VOICE_CHECK_EVERY_SECONDS, VOICE_CHECK_MIN_SECONDS, VOICE_CHECK_TAIL_SECONDS,
)
from endpointing import has_voice
from events import emit_heard, emit_missed, emit_state, emit_text
from wake import strip_wake_word


class TranscriptionMixin:
    def handle_phrase(self, audio: np.ndarray, started: float) -> None:
        """Recognize one phrase heard in the room and act on it if it is addressed to Kvist.

        It is addressed to Kvist when it starts with the wake word, or when a
        conversation is open (after the wake word alone, after Kvist answered)
        and it is a real phrase, not a stray word or the noise Whisper writes down.

        `started` is when the person began to say it (time.monotonic). A conversation
        covers a phrase that began inside it: a long sentence ends, and is recognized,
        well after the window it started in has closed, and judging it by that later
        moment threw away everything but short, hurried phrases.
        """
        language = self.pick_language(audio)
        head = audio[: round(GATE_HEAD_SECONDS * FS)]
        heard, command = self.listen_for_wake_word(head, language)
        if command is not None:
            logging.info("Wake word heard")
            self.follow_up_deadline = 0.0
            self.pause_audio()
            if command:
                # The gate's wording is a fallback only when it heard the whole command.
                self.deliver_command(audio, language, wake_heard=True, gate_command=command if len(head) == len(audio) else "")
                return
            self.converse(FOLLOW_UP_SECONDS, FOLLOW_UP_MIN_WORDS)
            return

        if started < self.follow_up_deadline and self.is_real_phrase(heard):
            self.follow_up_deadline = 0.0
            self.deliver_command(audio, language, wake_heard=False)
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

    def deliver_command(self, audio: np.ndarray, language: str | None, wake_heard: bool, gate_command: str = "") -> None:
        """Recognize the command with the main model and emit its text as a `text` event.

        `gate_command` is the gate model's wording of the whole command after the
        wake word; it is empty when the gate read only the start of a long phrase.
        """
        emit_state("transcribing")
        try:
            text = self.recognize_command(audio, language)
            if wake_heard:
                # The main model spells the wake word its own way. If it is not found, the
                # gate model's wording of the command is the fallback; of a long phrase the
                # gate has only the start, and the main model's text is kept with the odd name in it.
                stripped = strip_wake_word(text)
                if stripped is not None:
                    text = stripped
                elif gate_command:
                    text = gate_command

            if not text:
                logging.info("Nothing recognized")
                return

            self.held.append(text)
            self.held_seconds += len(audio) / FS
            self.converse(CONTINUATION_SECONDS, CONTINUATION_MIN_WORDS, announce=False)
        except Exception:
            logging.exception("Transcription failed")
            # The person spoke to Kvist and nothing will come of it: say so, or they wait in silence.
            emit_missed()
        finally:
            if self.held:
                self.release_held()
            else:
                self.restore_audio()
                emit_state("idle")

    def release_held(self) -> None:
        """Hand over the request once the person has finished it: every phrase of it, as one text."""
        if not self.held or self.person_goes_on():
            return
        text = " ".join(self.held)
        self.held.clear()
        self.held_seconds = 0.0
        emit_text(text)
        self.restore_audio()
        emit_state("idle")

    def person_goes_on(self) -> bool:
        """Whether more of the held request may still be coming."""
        if not self.phrases.empty():
            return True
        if self.phrase is not None:
            # Somebody speaks. A phrase that began after the conversation closed needs the
            # wake word to be for Kvist, so it cannot add to the request: no reason to wait for it.
            # Nor is a noise a reason, or a film, that never lets the phrase end.
            if self.phrase_started >= self.follow_up_deadline:
                return False
            return time.monotonic() - self.phrase_started < JOIN_WAIT_MAX_SECONDS and self.phrase_has_voice()
        grace = min(JOIN_GRACE_MAX_SECONDS, JOIN_GRACE_PER_SECOND * self.held_seconds)
        return time.monotonic() - self.phrase_ended < grace

    def phrase_has_voice(self) -> bool:
        """Whether the phrase being said holds a voice; too short to tell counts as one, the next look decides."""
        chunks = list(self.phrase)  # the microphone thread keeps appending to it
        if len(chunks) * BLOCK_SECONDS < VOICE_CHECK_MIN_SECONDS:
            return True
        now = time.monotonic()
        if now - self.voice_checked < VOICE_CHECK_EVERY_SECONDS:
            return self.voice_found
        self.voice_checked = now
        self.voice_found = has_voice(self.join_phrase(chunks[-round(VOICE_CHECK_TAIL_SECONDS / BLOCK_SECONDS):]))
        return self.voice_found

    def recognize_command(self, audio: np.ndarray, language: str | None) -> str:
        """The command's text by the main model; when that model fails, the small one takes over for good.

        The main model can load and pass its trial and still run out of video memory
        in the middle of somebody's phrase: another program took the memory since, or
        a real phrase needs more than the trial did. CTranslate2 reports that as a
        RuntimeError. The phrase is not lost: the small model, which is known to
        work (it has just read this phrase for the wake word), recognizes it, and
        the main one is let go so that its memory is free again.
        """
        try:
            return self.transcribe(self.model, audio, MAIN_BEAM_SIZE, language)
        except RuntimeError:
            if self.model is self.gate_model:
                raise
            logging.exception("The main model failed; the small model recognizes commands from now on")
            self.model = self.gate_model
            return self.transcribe(self.model, audio, MAIN_BEAM_SIZE, language)

    def expire_follow_up(self) -> None:
        """Give up waiting for a command that did not follow the bare wake word."""
        if not self.follow_up_deadline or time.monotonic() < self.follow_up_deadline:
            return
        # Somebody is speaking, or their phrase waits to be recognized: it began inside the
        # conversation and still belongs to it.
        if self.phrase is not None or not self.phrases.empty():
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
