# -*- coding: utf-8 -*-
"""Checks of the voice process that need no model and no sound card."""
from __future__ import annotations

import threading
import time
import unittest
import unittest.mock

import numpy as np

from speak import BLOCK_SECONDS, Speaker

RATE = 1000
BLOCK = int(RATE * BLOCK_SECONDS)


class FakeEngine:
    sample_rate = RATE
    channels = 1

    def __init__(self, sentences: int = 2, blocks: int = 4, level: float = 0.1) -> None:
        self.sentences, self.blocks, self.level = sentences, blocks, level
        self.texts: list[str] = []

    def chunks(self, text):
        self.texts.append(text)
        for _ in range(self.sentences):
            yield np.full((BLOCK * self.blocks, 1), self.level, dtype=np.float32)


class FakePlayer:
    def __init__(self, gate: threading.Event | None = None) -> None:
        self.blocks = 0
        self.gate = gate

    def write(self, block):
        if self.gate is not None:
            self.gate.wait(2)
        self.blocks += 1

    def close(self):
        pass


class Events:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def __call__(self, **fields):
        self.items.append(fields)

    def names(self):
        return [item["event"] for item in self.items]


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return False


def speaker(engine=None, player=None):
    events = Events()
    player = player or FakePlayer()
    voice = Speaker(engine or FakeEngine(), lambda rate, channels: player, events)
    voice.start()
    return voice, events, player


class SpeakerTests(unittest.TestCase):
    def test_an_answer_is_spoken_with_levels_and_ends_in_idle(self):
        voice, events, player = speaker()
        voice.handle({"type": "speak", "id": 1, "kind": "answer", "text": "Готово."})
        self.assertTrue(wait_for(lambda: events.items and events.items[-1]["event"] == "idle"))
        self.assertEqual(events.names()[0], "speaking")
        self.assertEqual(events.names().count("speaking"), 1)
        self.assertEqual(player.blocks, 8)
        levels = [item["value"] for item in events.items if item["event"] == "level"]
        self.assertEqual(len(levels), 8)
        self.assertTrue(all(abs(level - 0.4) < 1e-6 for level in levels))

    def test_the_level_is_clamped_to_one(self):
        voice, events, _ = speaker(FakeEngine(sentences=1, blocks=1, level=0.9))
        voice.handle({"type": "speak", "kind": "answer", "text": "громко"})
        self.assertTrue(wait_for(lambda: events.names()[-1:] == ["idle"]))
        self.assertEqual([item["value"] for item in events.items if item["event"] == "level"], [1.0])

    def test_a_narration_line_is_dropped_while_the_voice_is_busy(self):
        gate = threading.Event()
        engine = FakeEngine()
        voice, events, _ = speaker(engine, FakePlayer(gate))
        voice.handle({"type": "speak", "kind": "answer", "text": "первое"})
        self.assertTrue(wait_for(lambda: "speaking" in events.names()))
        voice.handle({"type": "speak", "kind": "narration", "text": "так..."})
        gate.set()
        self.assertTrue(wait_for(lambda: events.names()[-1:] == ["idle"]))
        self.assertEqual(engine.texts, ["первое"])

    def test_a_narration_line_is_spoken_when_the_voice_is_free(self):
        engine = FakeEngine(sentences=1, blocks=1)
        voice, events, _ = speaker(engine)
        voice.handle({"type": "speak", "kind": "narration", "text": "так..."})
        self.assertTrue(wait_for(lambda: events.names()[-1:] == ["idle"]))
        self.assertEqual(engine.texts, ["так..."])

    def test_an_answer_cuts_off_what_is_being_said(self):
        gate = threading.Event()
        engine = FakeEngine(sentences=1, blocks=20)
        player = FakePlayer(gate)
        voice, events, _ = speaker(engine, player)
        voice.handle({"type": "speak", "kind": "narration", "text": "рассуждаю"})
        self.assertTrue(wait_for(lambda: "speaking" in events.names()))
        voice.handle({"type": "speak", "kind": "answer", "text": "готово"})
        gate.set()
        self.assertTrue(wait_for(lambda: events.names()[-1:] == ["idle"]))
        self.assertEqual(engine.texts, ["рассуждаю", "готово"])
        # The first line stopped after a block or two; the answer played whole.
        self.assertLess(player.blocks, 20 + 4)
        self.assertEqual(events.names().count("idle"), 1)

    def test_stop_silences_the_voice_and_reports_idle(self):
        gate = threading.Event()
        engine = FakeEngine(sentences=1, blocks=40)
        player = FakePlayer(gate)
        voice, events, _ = speaker(engine, player)
        voice.handle({"type": "speak", "kind": "answer", "text": "длинная фраза"})
        self.assertTrue(wait_for(lambda: "speaking" in events.names()))
        voice.handle({"type": "stop"})
        gate.set()
        self.assertTrue(wait_for(lambda: events.names()[-1:] == ["idle"]))
        self.assertLess(player.blocks, 5)

    def test_empty_text_says_nothing(self):
        voice, events, _ = speaker()
        voice.handle({"type": "speak", "kind": "answer", "text": "   "})
        voice.handle({"type": "unknown"})
        time.sleep(0.05)
        self.assertEqual(events.items, [])

    def test_a_failing_engine_does_not_silence_later_lines(self):
        class Flaky(FakeEngine):
            def chunks(self, text):
                if text == "сломай":
                    raise RuntimeError("model failed")
                yield from super().chunks(text)

        voice, events, _ = speaker(Flaky(sentences=1, blocks=1))
        voice.handle({"type": "speak", "kind": "answer", "text": "сломай"})
        self.assertTrue(wait_for(lambda: events.names()[-1:] == ["idle"]))
        voice.handle({"type": "speak", "kind": "answer", "text": "дальше"})
        self.assertTrue(wait_for(lambda: events.names().count("idle") == 2 and "speaking" in events.names()))


class LoudnessTests(unittest.TestCase):
    def test_a_quiet_sentence_is_brought_up_to_the_target(self):
        import numpy as np

        from silero_tts import PEAK_LIMIT, TARGET_RMS, evened

        quiet = np.full((1000, 1), 0.02, dtype=np.float32)
        self.assertAlmostEqual(float(np.sqrt(np.mean(np.square(evened(quiet))))), min(TARGET_RMS, PEAK_LIMIT), places=3)

    def test_a_loud_peak_is_never_pushed_past_the_limit(self):
        import numpy as np

        from silero_tts import PEAK_LIMIT, evened

        spiky = np.zeros((1000, 1), dtype=np.float32)
        spiky[10] = 0.9
        spiky[500] = 0.01
        self.assertLessEqual(float(np.abs(evened(spiky)).max()), PEAK_LIMIT + 1e-6)

    def test_silence_stays_silence(self):
        import numpy as np

        from silero_tts import evened

        silence = np.zeros((100, 1), dtype=np.float32)
        self.assertEqual(float(np.abs(evened(silence)).max()), 0.0)


class SileroEngineTests(unittest.TestCase):
    def engine(self, rate="medium"):
        import silero_tts

        engine = silero_tts.SileroEngine.__new__(silero_tts.SileroEngine)
        engine._model = unittest.mock.Mock()
        engine._model.apply_tts.return_value = [0.1] * 4800
        engine._speaker = "baya"
        engine._rate = rate
        return engine

    def test_each_sentence_is_read_in_words_and_followed_by_a_pause(self):
        import numpy as np

        engine = self.engine()
        pieces = list(engine.chunks("Готово: 3 файла. Открыла YouTube."))
        spoken = [call.kwargs["text"] for call in engine._model.apply_tts.call_args_list]
        self.assertEqual(spoken, ["Готово: три файла.", "Открыла ютуб."])
        self.assertEqual(len(pieces), 4)
        self.assertEqual(float(np.abs(pieces[1]).max()), 0.0)

    def test_a_slower_rate_goes_through_ssml(self):
        engine = self.engine("slow")
        list(engine.chunks("Тесты & сборка."))
        call = engine._model.apply_tts.call_args
        self.assertNotIn("text", call.kwargs)
        self.assertIn('<prosody rate="slow">', call.kwargs["ssml_text"])
        self.assertIn("Тесты и сборка.", call.kwargs["ssml_text"])


class EngineChoiceTests(unittest.TestCase):
    def test_an_unknown_engine_is_refused_by_name(self):
        from speak import load_engine

        with self.assertRaises(ValueError) as refused:
            load_engine("espeak")
        self.assertIn("espeak", str(refused.exception))

    def test_the_engines_are_loaded_by_name(self):
        from unittest.mock import patch

        from speak import load_engine

        with patch.dict("sys.modules", {"silero_tts": unittest.mock.MagicMock(), "moss_nano": unittest.mock.MagicMock()}):
            import silero_tts
            import moss_nano

            self.assertIs(load_engine("silero"), silero_tts.SileroEngine.return_value)
            self.assertIs(load_engine("moss"), moss_nano.MossNanoEngine.return_value)


if __name__ == "__main__":
    unittest.main()
