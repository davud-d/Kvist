# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import queue
import subprocess

import numpy as np

from config import MIN_SPEECH_SECONDS
from endpointing import is_speech


class AudioMixin:
    def audio_callback(self, indata, frames, time_info, status) -> None:
        """Cut the always-open microphone stream into phrases and queue them for recognition."""
        if status:
            logging.warning("Audio status: %s", status)

        if self.muted:
            # The assistant is speaking: its own voice must not become a phrase.
            self.phrase = None
            self.pre_roll.clear()
            return

        chunk = indata.copy()

        if self.phrase is None:
            self.pre_roll.append(chunk)
            if not is_speech(chunk):
                return
            self.phrase = list(self.pre_roll)
            self.pre_roll.clear()
            self.endpointer.reset()
            self.endpointer.feed(chunk)
            return

        self.phrase.append(chunk)
        if self.endpointer.feed(chunk) is None:
            return

        phrase, self.phrase = self.phrase, None
        if self.endpointer.speech < MIN_SPEECH_SECONDS:
            return
        try:
            self.phrases.put_nowait(phrase)
        except queue.Full:
            logging.warning("Recognition is behind; dropped a phrase")

    def pause_audio (self) -> None: # Pause the player while a command is spoken
        self.was_playing = False

        try:
            result = subprocess.run(
                ["playerctl", "status"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )

            if result.returncode == 0:
                status = result.stdout.strip()

                if status == "Playing":
                    logging.warning("Player paused while Kvist listens")

                    pause_result = subprocess.run(
                        ["playerctl", "pause"],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=2,
                    )

                    if pause_result.returncode == 0:
                        self.was_playing = True
                    else:
                        logging.warning(
                            "playerctl pause failed: %s",
                            pause_result.stderr.strip() or "unknown error",
                        )

                else:
                    logging.info("Player not playing, nothing to pause")

        except subprocess.TimeoutExpired:
            logging.warning("playerctl timed out, skipping pause")
        except Exception:
            logging.exception("Failed to pause audio")

    def restore_audio(self) -> None: # Resume the player after the command
        if not self.was_playing:
            return

        self.was_playing = False

        try:
            logging.info("Resuming audio playback...")
            subprocess.run(
                ["playerctl", "play"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )

        except subprocess.TimeoutExpired:
            logging.warning("playerctl timed out, skipping resume")
        except Exception:
            logging.exception("Failed to resume audio")

    @staticmethod
    def join_phrase(chunks: list[np.ndarray]) -> np.ndarray:
        return np.concatenate(chunks, axis=0).squeeze().astype(np.float32)
