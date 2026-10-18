# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import os
import queue
import stat
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from faster_whisper.utils import download_model

from audio import AudioMixin
from config import BLOCK_SECONDS, CHANNELS, FS, MAX_QUEUED_PHRASES, PRE_ROLL_SECONDS
from endpointing import Endpointer
from events import emit_state
from memory import trim_memory
from transcription import TranscriptionMixin


# Video memory a model needs, as a share of its weights file (model.bin is float16),
# plus room for the compute libraries' workspace. A GPU with less free than that is
# not tried: loading reads the whole model into RAM first, and a failure leaves
# those gigabytes taken from everything else.
GPU_SHARE = {"float16": 1.15, "int8_float16": 0.6}
GPU_WORKSPACE_MB = 300


def stdin_is_pipe() -> bool:
    """True when whoever started this process holds the other end of a pipe on stdin."""
    try:
        return stat.S_ISFIFO(os.fstat(sys.stdin.fileno()).st_mode)
    except (AttributeError, OSError, ValueError):
        return False


def free_gpu_mb() -> int | None:
    """Free video memory of the first GPU, or None when nvidia-smi cannot say."""
    try:
        reply = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        return int(reply.splitlines()[0])
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def gpu_has_room(name: str, compute_type: str) -> bool:
    """False only when the model is known to need more video memory than is free."""
    free = free_gpu_mb()
    if free is None:
        return True
    try:
        weights = Path(download_model(name, local_files_only=True), "model.bin").stat().st_size / 1e6
    except (OSError, ValueError, RuntimeError):  # not downloaded yet: its size is unknown
        return True
    return weights * GPU_SHARE[compute_type] + GPU_WORKSPACE_MB <= free


# Ways to run a model, best first. int8_float16 needs about half the GPU memory of float16.
GPU_CHAIN = (("cuda", "float16"), ("cuda", "int8_float16"))
# The small model may also run on the CPU: it takes a few hundred megabytes of memory.
LOAD_CHAIN = GPU_CHAIN + (("cpu", "int8"),)


class VoiceToTextApp(AudioMixin, TranscriptionMixin):
    def __init__(self) -> None:
        emit_state("loading")

        self.running = True
        self.muted = False
        self.was_playing = False
        self.follow_up_deadline = 0.0
        self.follow_up_min_words = 1

        self.endpointer = Endpointer()
        self.pre_roll: deque[np.ndarray] = deque(maxlen=round(PRE_ROLL_SECONDS / BLOCK_SECONDS))
        self.phrase: Optional[list[np.ndarray]] = None
        self.phrases: queue.Queue[list[np.ndarray]] = queue.Queue(maxsize=MAX_QUEUED_PHRASES)

        logging.info("Loading models...")
        # The larger model first: it gets the GPU memory, the small one takes what is left.
        # The larger one never runs on the CPU: it would take gigabytes of memory
        # (and swap) from everything else that runs. Without room on the GPU the
        # small model recognizes the commands too.
        main = os.environ.get("WHISPER_MODEL", "large-v3").strip()
        try:
            self.model = self.load_model(main, GPU_CHAIN)
        except RuntimeError:
            self.model = None
        self.gate_model = self.load_model(os.environ.get("WHISPER_GATE_MODEL", "small").strip(), LOAD_CHAIN)
        if self.model is None:
            logging.warning("%s does not fit the GPU: the small model recognizes commands as well", main)
            self.model = self.gate_model
        trim_memory()

    def load_model(self, name: str, chain: tuple[tuple[str, str], ...]) -> WhisperModel:
        """The first way of `chain` that loads and then really transcribes.

        Loading alone proves little: a missing cuBLAS or a full GPU shows only
        when the first audio is processed, and by then the process would die
        on somebody's phrase. So every way is tried on a second of silence.
        """
        for device, compute_type in chain:
            if device == "cuda" and not gpu_has_room(name, compute_type):
                logging.info("Model %s does not fit the free GPU memory (%s)", name, compute_type)
                continue
            try:
                model = WhisperModel(name, device=device, compute_type=compute_type)
                segments, _info = model.transcribe(np.zeros(FS, dtype=np.float32), language="en", beam_size=1)
                list(segments)
            except Exception:  # CTranslate2 reports a full GPU or a missing library as RuntimeError, others as ValueError
                logging.exception("Model %s cannot run on %s (%s)", name, device, compute_type)
                continue
            logging.info("Model %s runs on %s (%s)", name, device, compute_type)
            return model
        raise RuntimeError(f"Model {name} cannot run on any device")

    def read_commands(self) -> None:
        """Obey the process that launched this one, one JSON line each:
        {"type": "mute", "value": bool} and {"type": "converse", "seconds": n, "min_words": n}."""
        for line in sys.stdin:
            try:
                command = json.loads(line)
            except json.JSONDecodeError:
                logging.warning("Not a command: %s", line.strip())
                continue
            if not isinstance(command, dict):
                continue
            if command.get("type") == "mute":
                self.muted = bool(command.get("value"))
            elif command.get("type") == "converse":
                self.converse(float(command.get("seconds", 0)), int(command.get("min_words", 1)))
        # The end of a pipe means the launcher is gone: without it nobody reads the
        # events, and the models would stay in memory. A terminal or /dev/null is not that.
        if stdin_is_pipe():
            self.running = False

    def shutdown(self) -> None:
        self.running = False
        self.restore_audio()

    def run(self) -> None:
        threading.Thread(target=self.read_commands, daemon=True).start()
        with sd.InputStream(
            samplerate=FS,
            channels=CHANNELS,
            dtype="float32",
            blocksize=round(FS * BLOCK_SECONDS),
            callback=self.audio_callback,
        ):
            emit_state("idle")
            logging.info("Listening for the wake word.")

            while self.running:
                try:
                    chunks = self.phrases.get(timeout=0.5)
                except queue.Empty:
                    self.expire_follow_up()
                    continue
                try:
                    self.handle_phrase(self.join_phrase(chunks))
                except Exception:  # a model or driver failure on one phrase must not end the listening
                    logging.exception("Could not handle a phrase")
                    emit_state("idle")
