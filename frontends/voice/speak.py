# -*- coding: utf-8 -*-
"""The voice of Kvist: speaks the lines it is sent and reports what it is doing.

Reads one JSON object per line on stdin:

    {"type": "speak", "id": 7, "kind": "answer" | "narration", "text": "..."}
    {"type": "stop"}

and writes events on stdout (logs go to stderr):

    {"event": "ready"}
    {"event": "speaking"}            audio starts
    {"event": "level", "value": 0.4}  loudness of what plays, about 20 times a second
    {"event": "idle"}                everything queued has been said, or was stopped

The engine is KVIST_TTS_ENGINE: silero (default) or moss.

An answer cuts off what is being said. A narration line is dropped when the
voice is busy: it is company while the model thinks, and a line that arrives
late is worse than none. The engine (moss_nano.py) turns text into audio one
sentence at a time, so speech starts after the first sentence rather than the
whole reply.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Protocol

import numpy as np

from events import emit

BLOCK_SECONDS = 0.05
# RMS of a speech block is small next to full scale; the orb wants 0..1.
LEVEL_GAIN = 4.0


class Engine(Protocol):
    sample_rate: int
    channels: int

    def chunks(self, text: str) -> Iterator[np.ndarray]:
        """Audio of `text`, sentence by sentence, float32 shaped (samples, channels)."""


class Player(Protocol):
    def write(self, block: np.ndarray) -> None: ...

    def close(self) -> None: ...


class SoundDevicePlayer:
    def __init__(self, sample_rate: int, channels: int) -> None:
        import sounddevice as sd

        self._stream = sd.OutputStream(samplerate=sample_rate, channels=channels, dtype="float32")
        self._stream.start()

    def write(self, block: np.ndarray) -> None:
        self._stream.write(block)

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


class Speaker:
    def __init__(self, engine: Engine, open_player: Callable[[int, int], Player], say_event: Callable[..., None] = emit) -> None:
        self._engine = engine
        self._open_player = open_player
        self._emit = say_event
        self._jobs: queue.Queue[tuple[int, str]] = queue.Queue()
        self._lock = threading.Lock()
        # Bumped by every stop; a job or a sentence of an older generation is dropped.
        self._generation = 0
        self._pending = 0

    def start(self) -> None:
        threading.Thread(target=self._work, daemon=True, name="kvist-speaker").start()

    def handle(self, command: dict) -> None:
        kind = command.get("type")
        if kind == "stop":
            self._cancel()
        elif kind == "speak":
            self._speak(str(command.get("text", "")).strip(), str(command.get("kind", "answer")))

    def _speak(self, text: str, kind: str) -> None:
        if not text:
            return
        with self._lock:
            if kind == "narration" and self._pending:
                return
            if kind != "narration":
                self._generation += 1
            self._pending += 1
            self._jobs.put((self._generation, text))

    def _cancel(self) -> None:
        with self._lock:
            self._generation += 1

    def _work(self) -> None:
        player: Player | None = None
        while True:
            generation, text = self._jobs.get()
            try:
                if generation == self._generation:
                    player = player or self._open_player(self._engine.sample_rate, self._engine.channels)
                    self._say(generation, text, player)
            except Exception:  # one bad line must not silence every later one
                logging.exception("Could not say: %s", text)
            finally:
                self._finish()

    def _finish(self) -> None:
        with self._lock:
            self._pending -= 1
            quiet = self._pending == 0
        if quiet:
            self._emit(event="idle")

    def _say(self, generation: int, text: str, player: Player) -> None:
        block = max(1, int(self._engine.sample_rate * BLOCK_SECONDS))
        announced = False
        for audio in self._engine.chunks(text):
            for start in range(0, len(audio), block):
                if generation != self._generation:
                    return
                piece = audio[start : start + block]
                if not announced:
                    announced = True
                    self._emit(event="speaking")
                self._emit(event="level", value=min(1.0, float(np.sqrt(np.mean(np.square(piece)))) * LEVEL_GAIN))
                player.write(piece)


def load_engine(name: str) -> Engine:
    """The engine called `name`: "silero" (Russian, fast, a woman) or "moss" (MOSS-TTS-Nano)."""
    if name == "silero":
        from silero_tts import SileroEngine

        return SileroEngine()
    if name == "moss":
        from moss_nano import MossNanoEngine

        return MossNanoEngine()
    raise ValueError(f"KVIST_TTS_ENGINE must be silero or moss, not {name!r}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    speaker = Speaker(load_engine(os.environ.get("KVIST_TTS_ENGINE", "silero")), SoundDevicePlayer)
    speaker.start()
    emit(event="ready")
    for line in sys.stdin:
        try:
            command = json.loads(line)
        except json.JSONDecodeError:
            logging.warning("Not a command: %s", line.strip())
            continue
        if isinstance(command, dict):
            speaker.handle(command)


if __name__ == "__main__":
    main()
