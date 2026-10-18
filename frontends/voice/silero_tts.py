# -*- coding: utf-8 -*-
"""Silero TTS (Russian, v5) as the voice engine: about 150 MB, the CPU, much faster than speech.

The model is not part of this repository and its licence is CC BY-NC 4.0
(non-commercial): tts_setup.sh downloads it for the person who runs it. It needs
torch, so the engine runs in the environment that script creates.

Settings (environment):
    KVIST_VOICE_HOME        data folder; default $XDG_DATA_HOME/kvist/voice
    KVIST_TTS_SPEAKER       aidar, baya, kseniya, eugene or xenia; default "baya" (a woman)
    KVIST_TTS_RATE          x-slow, slow, medium, fast or x-fast; default medium
    KVIST_TTS_SILERO_MODEL  path of the model file; default $KVIST_VOICE_HOME/silero/v5_6_ru.pt
    KVIST_TTS_THREADS       CPU threads; default 4
"""
from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

from memory import trim_memory
from moss_nano import data_dir
from speech_ru import speakable_russian

DEFAULT_SPEAKER = "baya"
DEFAULT_THREADS = 4
SAMPLE_RATE = 48000
# One sentence at a time: speech starts after the first, and the model reads
# a long run of text worse than a short one.
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


# After each sentence: a breath, so the next one does not run on.
SENTENCE_PAUSE_SECONDS = 0.25
# Every sentence is brought to the same loudness (RMS), without clipping.
TARGET_RMS = 0.10
PEAK_LIMIT = 0.95
RATES = ("x-slow", "slow", "medium", "fast", "x-fast")


def evened(audio: np.ndarray) -> np.ndarray:
    """`audio` scaled to the target loudness, and never past the peak limit."""
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    if peak <= 0.0 or rms <= 0.0:
        return audio
    return audio * min(TARGET_RMS / rms, PEAK_LIMIT / peak)


class SileroEngine:
    sample_rate = SAMPLE_RATE
    channels = 1

    def __init__(self) -> None:
        path = Path(os.environ.get("KVIST_TTS_SILERO_MODEL") or data_dir() / "silero" / "v5_6_ru.pt")
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing: run tts_setup.sh silero first")
        import torch

        started = time.perf_counter()
        torch.set_num_threads(int(os.environ.get("KVIST_TTS_THREADS", DEFAULT_THREADS)))
        self._model = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
        self._model.to("cpu")
        self._rate = os.environ.get("KVIST_TTS_RATE", "medium")
        if self._rate not in RATES:
            raise ValueError(f"KVIST_TTS_RATE must be one of {', '.join(RATES)}, not {self._rate!r}")
        self._speaker = os.environ.get("KVIST_TTS_SPEAKER", DEFAULT_SPEAKER)
        if self._speaker not in self._model.speakers:
            raise ValueError(f"No speaker {self._speaker!r}; this model has {', '.join(self._model.speakers)}")
        # The first run builds caches: do it now, not on the first answer.
        self._model.apply_tts(text="Привет.", speaker=self._speaker, sample_rate=SAMPLE_RATE)
        trim_memory()
        logging.info("Silero ready in %.1f s (speaker %s)", time.perf_counter() - started, self._speaker)

    def _say(self, sentence: str) -> np.ndarray:
        if self._rate == "medium":
            audio = self._model.apply_tts(text=sentence, speaker=self._speaker, sample_rate=SAMPLE_RATE)
        else:
            ssml = f'<speak><prosody rate="{self._rate}">{escape(sentence)}</prosody></speak>'
            audio = self._model.apply_tts(ssml_text=ssml, speaker=self._speaker, sample_rate=SAMPLE_RATE)
        return evened(np.asarray(audio, dtype=np.float32).reshape(-1, 1))

    def chunks(self, text: str) -> Iterator[np.ndarray]:
        pause = np.zeros((int(SAMPLE_RATE * SENTENCE_PAUSE_SECONDS), 1), dtype=np.float32)
        for sentence in SENTENCE_END.split(speakable_russian(text)):
            if sentence.strip():
                yield self._say(sentence)
                yield pause
