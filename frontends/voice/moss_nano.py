# -*- coding: utf-8 -*-
"""MOSS-TTS-Nano (OpenMOSS, Apache-2.0) as the voice engine: 0.1B, ONNX, CPU.

The model is not part of this repository. tts_setup.sh puts the runtime source
and the ONNX weights under the data folder below. Its runtime imports torch, so
the engine runs in the environment that script creates, not in the one of the
microphone.

Settings (environment):
    KVIST_VOICE_HOME        data folder; default $XDG_DATA_HOME/kvist/voice
    KVIST_TTS_VOICE         a built-in voice; default "Zhiming"
    KVIST_TTS_VOICE_FILE    a reference recording of the voice to speak with
                            (a few seconds of clean speech); overrides the built-in voice
    KVIST_TTS_THREADS       CPU threads for the model; default 4
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from memory import trim_memory

# Measured on this model: the built-in voices are Chinese, English and Japanese.
# English text is clear in English voices; Russian is only partly intelligible
# in a Chinese voice and garbled in an English one. A reference recording in the
# language to speak is the way to a better voice.
DEFAULT_VOICE = "Zhiming"
DEFAULT_THREADS = 4
# Tokens per synthesized piece: the runtime's own limit for voice cloning.
MAX_PIECE_TOKENS = 75
# The runtime joins sentences into one piece up to that limit, and a piece is
# heard only when it is whole. One sentence per piece starts the speech sooner.
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def data_dir() -> Path:
    home = os.environ.get("KVIST_VOICE_HOME")
    if home:
        return Path(home).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "kvist" / "voice"


class MossNanoEngine:
    def __init__(self) -> None:
        home = data_dir()
        source = home / "moss-tts-nano-src"
        if not source.is_dir():
            raise FileNotFoundError(f"{source} is missing: run tts_setup.sh first")
        sys.path.insert(0, str(source))
        from onnx_tts_runtime import OnnxTtsRuntime

        started = time.perf_counter()
        self._runtime = OnnxTtsRuntime(
            model_dir=home / "nano-models",
            thread_count=int(os.environ.get("KVIST_TTS_THREADS", DEFAULT_THREADS)),
            output_dir=home / "out",
        )
        self._voice = os.environ.get("KVIST_TTS_VOICE", DEFAULT_VOICE)
        reference = os.environ.get("KVIST_TTS_VOICE_FILE")
        # The voice is turned into codes once; every sentence reuses them.
        self._prompt_codes = self._runtime.resolve_prompt_audio_codes(
            voice=self._voice, prompt_audio_path=reference or None
        )
        self._runtime.warmup()
        trim_memory()
        config = self._runtime.codec_meta["codec_config"]
        self.sample_rate = int(config["sample_rate"])
        self.channels = int(config["channels"])
        logging.info("MOSS-TTS-Nano ready in %.1f s (voice %s)", time.perf_counter() - started, reference or self._voice)

    def chunks(self, text: str) -> Iterator[np.ndarray]:
        prepared = self._runtime.prepare_synthesis_text(
            text=text, voice=self._voice, enable_wetext=False, enable_normalize_tts_text=True
        )
        for sentence in SENTENCE_END.split(str(prepared["text"])):
            for piece in self._runtime.split_voice_clone_text(sentence, max_tokens=MAX_PIECE_TOKENS):
                result = self._runtime.synthesize_single_chunk(
                    text=piece, prompt_audio_codes=self._prompt_codes, streaming=False
                )
                yield np.asarray(result["waveform"], dtype=np.float32)
