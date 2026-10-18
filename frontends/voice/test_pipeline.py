# -*- coding: utf-8 -*-
"""Checks that need no microphone, model download or desktop session."""
from __future__ import annotations

import io
import json
import queue
import time
import unittest
from collections import deque
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np

import app
import events
import main
import memory
from config import (
    END_SILENCE_MAX_SECONDS, FS, INITIAL_PROMPT, MAX_RECORDING_SECONDS, MIN_SPEECH_SECONDS,
)
from editor import MAX_INPUT_CHARS, TextEditor
from endpointing import END_OF_SPEECH, TOO_LONG, Endpointer, is_speech, silence_to_end
from transcription import TranscriptionMixin
from wake import strip_wake_word


def chunk(seconds: float, level: float) -> np.ndarray:
    return np.full((int(FS * seconds), 1), level, dtype=np.float32)


class EditorTests(unittest.TestCase):
    def response(self, text="Готовый текст.", **extra):
        data = {"done": True, "response": json.dumps({"text": text})}
        data.update(extra)
        response = Mock(status=200)
        response.read.return_value = json.dumps(data).encode()
        return response

    def test_success_and_gpu_request(self):
        with patch("editor.http.client.HTTPConnection") as factory:
            connection = factory.return_value
            connection.getresponse.return_value = self.response()
            self.assertEqual(TextEditor().edit("готовый текст"), "Готовый текст.")
            payload = json.loads(connection.request.call_args.args[2])
            self.assertEqual(payload["model"], "qwen2.5:1.5b")
            self.assertEqual(payload["options"]["num_gpu"], 99)
            self.assertEqual(payload["options"]["num_ctx"], 4096)
            connection.close.assert_called_once()

    def test_invalid_responses_preserve_original(self):
        responses = [self.response(""), self.response(done=False),
                     self.response(done_reason="length"), Mock(status=500)]
        malformed = self.response()
        malformed.read.return_value = b"not json"
        responses.append(malformed)
        for response in responses:
            with self.subTest(response=response), patch(
                    "editor.http.client.HTTPConnection") as factory:
                factory.return_value.getresponse.return_value = response
                self.assertEqual(TextEditor().edit("исходный текст"), "исходный текст")

    def test_connection_failure_preserves_original(self):
        with patch("editor.http.client.HTTPConnection") as factory:
            factory.return_value.request.side_effect = OSError("offline")
            self.assertEqual(TextEditor().edit("исходный текст"), "исходный текст")
            factory.return_value.close.assert_called_once()

    def test_bypass_does_not_connect(self):
        with patch("editor.http.client.HTTPConnection") as factory:
            for text in ("", " ", "я" * (MAX_INPUT_CHARS + 1)):
                self.assertEqual(TextEditor().edit(text), text)
            self.assertEqual(TextEditor(enabled=False).edit("текст"), "текст")
            factory.assert_not_called()

    def test_environment_disable(self):
        with patch.dict("os.environ", {"TEXT_EDITOR_ENABLED": "0"}):
            self.assertFalse(TextEditor.from_environment().enabled)


class EndpointerTests(unittest.TestCase):
    def test_a_short_phrase_ends_after_a_short_pause(self):
        endpointer = Endpointer()
        need = silence_to_end(0.5)
        self.assertIsNone(endpointer.feed(chunk(0.5, 0.2)))
        self.assertIsNone(endpointer.feed(chunk(need - 0.2, 0.0)))
        self.assertEqual(endpointer.feed(chunk(0.3, 0.0)), END_OF_SPEECH)

    def test_a_long_phrase_tolerates_the_pauses_of_somebody_thinking(self):
        # "неправильно поняла задачу... надо было открыть не там, а тут": a pause in the middle.
        long_phrase = Endpointer()
        long_phrase.feed(chunk(4.0, 0.2))
        self.assertIsNone(long_phrase.feed(chunk(1.3, 0.0)), "1.3 s is not the end of four seconds of speech")
        self.assertIsNone(long_phrase.feed(chunk(0.2, 0.2)), "and the speaker goes on")
        short_phrase = Endpointer()
        short_phrase.feed(chunk(0.5, 0.2))
        self.assertEqual(short_phrase.feed(chunk(1.3, 0.0)), END_OF_SPEECH, "the same pause ends a short command")

    def test_the_pause_grows_with_the_speech_up_to_a_limit(self):
        self.assertLess(silence_to_end(0.5), silence_to_end(3.0))
        self.assertEqual(silence_to_end(100.0), END_SILENCE_MAX_SECONDS)

    def test_speech_resets_the_pause(self):
        endpointer = Endpointer()
        endpointer.feed(chunk(0.5, 0.2))
        endpointer.feed(chunk(silence_to_end(0.5) - 0.2, 0.0))
        self.assertIsNone(endpointer.feed(chunk(0.2, 0.2)))
        self.assertIsNone(endpointer.feed(chunk(silence_to_end(0.9) - 0.4, 0.0)))

    def test_long_phrase_is_cut(self):
        self.assertEqual(Endpointer().feed(chunk(MAX_RECORDING_SECONDS, 0.2)), TOO_LONG)

    def test_speech_is_counted_and_reset(self):
        endpointer = Endpointer()
        endpointer.feed(chunk(0.5, 0.2))
        endpointer.feed(chunk(0.5, 0.0))
        self.assertAlmostEqual(endpointer.speech, 0.5)
        endpointer.reset()
        self.assertEqual(endpointer.speech, 0.0)

    def test_energy_gate(self):
        self.assertTrue(is_speech(chunk(0.1, 0.2)))
        self.assertFalse(is_speech(chunk(0.1, 0.001)))


class WakeWordTests(unittest.TestCase):
    def test_command_after_the_wake_word(self):
        self.assertEqual(strip_wake_word("Квист, открой ютуб"), "открой ютуб")
        self.assertEqual(strip_wake_word("  Квист!  Привет."), "Привет.")
        self.assertEqual(strip_wake_word("Kvist open youtube"), "open youtube")

    def test_wake_word_alone(self):
        self.assertEqual(strip_wake_word("Квист."), "")

    def test_close_spellings_match(self):
        self.assertEqual(strip_wake_word("Квис открой"), "открой")
        self.assertEqual(strip_wake_word("Квиста, открой"), "открой")

    def test_other_phrases_do_not_match(self):
        for text in ("открой квист", "квест начинается", "привет", "", "...", "квиз"):
            with self.subTest(text=text):
                self.assertIsNone(strip_wake_word(text))


class CommandTests(unittest.TestCase):
    def test_the_launcher_can_mute_and_unmute(self):
        instance = bare_app()
        lines = ['{"type": "mute", "value": true}\n', "not json\n", '{"type": "other"}\n', "[1]\n"]
        with patch("app.sys.stdin", lines):
            instance.read_commands()
        self.assertTrue(instance.muted)
        with patch("app.sys.stdin", ['{"type": "mute", "value": false}\n']):
            instance.read_commands()
        self.assertFalse(instance.muted)


class TrimMemoryTests(unittest.TestCase):
    def test_freed_memory_is_handed_back_after_the_models_load(self):
        with patch("memory.ctypes.CDLL") as cdll:
            memory.trim_memory()
        cdll.return_value.malloc_trim.assert_called_once_with(0)

    def test_a_library_without_malloc_trim_is_not_an_error(self):
        with patch("memory.ctypes.CDLL", side_effect=OSError("no libc.so.6")):
            memory.trim_memory()


class LauncherGoneTests(unittest.TestCase):
    def test_the_end_of_a_pipe_stops_the_listening_but_a_terminal_does_not(self):
        for is_pipe, expected in ((True, False), (False, True)):
            with self.subTest(is_pipe=is_pipe):
                instance = bare_app()
                instance.running = True
                with patch("app.sys.stdin", []), patch("app.stdin_is_pipe", return_value=is_pipe):
                    instance.read_commands()
                self.assertEqual(instance.running, expected)

    def test_stdin_kinds_are_told_apart(self):
        import os

        read_end, write_end = os.pipe()
        try:
            with patch("app.sys.stdin") as stdin:
                stdin.fileno.return_value = read_end
                self.assertTrue(app.stdin_is_pipe())
                stdin.fileno.return_value = os.open(os.devnull, os.O_RDONLY)
                self.assertFalse(app.stdin_is_pipe())
                os.close(stdin.fileno.return_value)
            with patch("app.sys.stdin", []):
                self.assertFalse(app.stdin_is_pipe())
        finally:
            os.close(read_end)
            os.close(write_end)


class CudaLibsTests(unittest.TestCase):
    def test_libraries_found_in_site_packages_are_loaded_globally(self):
        import cuda_libs
        import ctypes
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as root:
            lib = Path(root, "nvidia", "cublas", "lib")
            lib.mkdir(parents=True)
            (lib / "libcublas.so.12").write_bytes(b"")
            with patch("cuda_libs.site.getsitepackages", return_value=[root]), patch(
                    "cuda_libs.site.getusersitepackages", return_value="/nonexistent"), patch(
                    "cuda_libs.ctypes.CDLL") as cdll:
                loaded = cuda_libs.preload()
        self.assertEqual(loaded, [str(lib / "libcublas.so.12")])
        cdll.assert_called_once_with(str(lib / "libcublas.so.12"), mode=ctypes.RTLD_GLOBAL)

    def test_a_library_that_does_not_load_is_reported_not_fatal(self):
        import cuda_libs
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as root:
            lib = Path(root, "nvidia", "cudnn", "lib")
            lib.mkdir(parents=True)
            (lib / "libcudnn.so.9").write_bytes(b"")
            with patch("cuda_libs.site.getsitepackages", return_value=[root]), patch(
                    "cuda_libs.site.getusersitepackages", return_value="/nonexistent"), patch(
                    "cuda_libs.ctypes.CDLL", side_effect=OSError("bad ELF")):
                self.assertEqual(cuda_libs.preload(), [])


class EventTests(unittest.TestCase):
    def test_events_are_json_lines(self):
        out = io.StringIO()
        with redirect_stdout(out):
            events.emit_state("listening")
            events.emit_text("открой ютуб")
            events.emit_heard("хвост, открой ютуб")
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(lines, [
            {"event": "state", "state": "listening"},
            {"event": "text", "text": "открой ютуб"},
            {"event": "heard", "text": "хвост, открой ютуб"},
        ])


def bare_app():
    instance = app.VoiceToTextApp.__new__(app.VoiceToTextApp)
    instance.endpointer = Endpointer()
    instance.pre_roll = deque(maxlen=5)
    instance.phrase = None
    instance.phrases = queue.Queue(maxsize=2)
    instance.follow_up_deadline = 0.0
    instance.follow_up_min_words = 1
    instance.muted = False
    instance.was_playing = False
    instance.gate_model = Mock()
    instance.gate_model.detect_language.return_value = ("ru", 0.9, [("ru", 0.9), ("en", 0.1)])
    instance.model = Mock()
    instance.pause_audio = Mock()
    instance.restore_audio = Mock()
    return instance


def recognized(*texts):
    return ([SimpleNamespace(text=text) for text in texts], None)


class PhraseCuttingTests(unittest.TestCase):
    def feed(self, instance, *chunks):
        for block in chunks:
            instance.audio_callback(block, 0, None, None)

    def test_silence_queues_nothing(self):
        instance = bare_app()
        self.feed(instance, *[chunk(0.1, 0.0)] * 20)
        self.assertTrue(instance.phrases.empty())
        self.assertIsNone(instance.phrase)

    def test_phrase_is_queued_after_the_pause_with_pre_roll(self):
        instance = bare_app()
        quiet = chunk(0.1, 0.0)
        loud = chunk(0.1, 0.2)
        self.feed(instance, quiet, quiet, *[loud] * 6, *[quiet] * 13)
        self.assertEqual(instance.phrases.qsize(), 1)
        phrase = instance.phrases.get()
        self.assertEqual(len(phrase), 2 + 6 + 12, "the pre-roll, the speech and the pause that ended it")
        self.assertIsNone(instance.phrase)

    def test_short_click_is_dropped(self):
        instance = bare_app()
        quiet = chunk(0.1, 0.0)
        self.feed(instance, chunk(0.1, 0.2), *[quiet] * 13)
        self.assertLess(0.1, MIN_SPEECH_SECONDS)
        self.assertTrue(instance.phrases.empty())

    def test_nothing_is_cut_into_phrases_while_muted(self):
        instance = bare_app()
        loud = chunk(0.1, 0.2)
        quiet = chunk(0.1, 0.0)
        self.feed(instance, *[loud] * 3)
        self.assertIsNotNone(instance.phrase)
        instance.muted = True
        self.feed(instance, *[loud] * 6, *[quiet] * 13)
        self.assertIsNone(instance.phrase)
        self.assertEqual(len(instance.pre_roll), 0)
        self.assertTrue(instance.phrases.empty())
        instance.muted = False
        self.feed(instance, *[loud] * 6, *[quiet] * 13)
        self.assertEqual(instance.phrases.qsize(), 1)

    def test_full_queue_drops_the_new_phrase(self):
        instance = bare_app()
        quiet = chunk(0.1, 0.0)
        loud = chunk(0.1, 0.2)
        for _ in range(3):
            self.feed(instance, *[loud] * 6, *[quiet] * 13)
        self.assertEqual(instance.phrases.qsize(), 2)


class WakeFlowTests(unittest.TestCase):
    audio = np.zeros(1600, dtype=np.float32)

    def run_phrase(self, instance, *, gate, main=None):
        instance.gate_model.transcribe.return_value = recognized(gate)
        if main is not None:
            instance.model.transcribe.return_value = recognized(main)
        with patch("transcription.emit_text") as emit_text, patch(
                "transcription.emit_state") as emit_state:
            instance.handle_phrase(self.audio)
        return emit_text, emit_state

    def test_phrase_without_the_wake_word_is_ignored(self):
        instance = bare_app()
        with patch("transcription.emit_heard") as emit_heard:
            emit_text, emit_state = self.run_phrase(instance, gate="привет как дела")
        emit_text.assert_not_called()
        emit_state.assert_not_called()
        # The screen is told what was heard, so a misheard wake word can be seen.
        emit_heard.assert_called_once_with("привет как дела")
        instance.pause_audio.assert_not_called()
        instance.model.transcribe.assert_not_called()

    def test_a_wake_word_the_first_pass_dropped_is_found_by_the_second(self):
        instance = bare_app()
        instance.gate_model.transcribe.side_effect = [
            recognized("Создай файл с тремя строками."),
            recognized("Квист, создай файл с тремя строками."),
        ]
        instance.model.transcribe.return_value = recognized("Квист, создай файл с тремя строками.")
        with patch("transcription.emit_text") as emit_text, patch("transcription.emit_state"), patch(
                "transcription.emit_heard") as emit_heard:
            instance.handle_phrase(self.audio)
        emit_text.assert_called_once_with("создай файл с тремя строками.")
        emit_heard.assert_not_called()
        first, second = (call.kwargs for call in instance.gate_model.transcribe.call_args_list)
        self.assertIsNone(first["initial_prompt"])
        self.assertIn("Квист", second["initial_prompt"])

    def test_the_second_pass_is_skipped_when_the_first_heard_the_wake_word(self):
        instance = bare_app()
        self.run_phrase(instance, gate="Квист, открой ютуб", main="Квист, открой YouTube.")
        instance.gate_model.transcribe.assert_called_once()

    def test_a_phrase_for_nobody_is_asked_about_twice_and_reported_once(self):
        instance = bare_app()
        with patch("transcription.emit_heard") as emit_heard:
            self.run_phrase(instance, gate="привет как дела")
        self.assertEqual(instance.gate_model.transcribe.call_count, 2)
        emit_heard.assert_called_once_with("привет как дела")

    def test_silence_is_not_reported_as_heard(self):
        instance = bare_app()
        with patch("transcription.emit_heard") as emit_heard:
            self.run_phrase(instance, gate="")
        emit_heard.assert_not_called()

    def test_wake_word_with_command_is_delivered_without_the_wake_word(self):
        instance = bare_app()
        emit_text, emit_state = self.run_phrase(
            instance, gate="Квист открой ютуб", main="Квист, открой YouTube.")
        emit_text.assert_called_once_with("открой YouTube.")
        self.assertEqual(emit_state.call_args_list, [call("transcribing"), call("idle")])
        instance.pause_audio.assert_called_once()
        instance.restore_audio.assert_called_once()

    def test_gate_wording_is_the_fallback_when_main_model_misses_the_wake_word(self):
        instance = bare_app()
        emit_text, _ = self.run_phrase(
            instance, gate="Квист открой ютуб", main="Хвост, открой YouTube.")
        emit_text.assert_called_once_with("открой ютуб")

    def test_bare_wake_word_waits_for_the_command(self):
        instance = bare_app()
        emit_text, emit_state = self.run_phrase(instance, gate="Квист.")
        emit_text.assert_not_called()
        emit_state.assert_called_once_with("listening")
        self.assertGreater(instance.follow_up_deadline, time.monotonic())

        instance.gate_model.transcribe.return_value = recognized("это слово в команде")
        instance.model.transcribe.return_value = recognized("Квист, это слово в команде.")
        with patch("transcription.emit_text") as emit_text, patch(
                "transcription.emit_state") as emit_state:
            instance.handle_phrase(self.audio)
        emit_text.assert_called_once_with("Квист, это слово в команде.")

    def test_unanswered_wake_word_expires(self):
        instance = bare_app()
        instance.follow_up_deadline = time.monotonic() - 1
        with patch("transcription.emit_state") as emit_state:
            instance.expire_follow_up()
        emit_state.assert_called_once_with("idle")
        instance.restore_audio.assert_called_once()

        with patch("transcription.emit_state") as emit_state:
            instance.expire_follow_up()
        emit_state.assert_not_called()

    def test_command_text_is_emitted_as_recognized(self):
        for pieces, expected in (
            (["Квист, исходный текст"], "исходный текст"),
            (["Квист, привет привет ", "раз два раз два"], "привет привет раз два раз два"),
            (["Квист. Причина простая: телефон разрядился."], "Причина простая: телефон разрядился."),
        ):
            with self.subTest(pieces=pieces):
                instance = bare_app()
                instance.model.transcribe.return_value = recognized(*pieces)
                emit_text, _ = self.run_phrase(instance, gate="Квист исходный текст")
                emit_text.assert_called_once_with(expected)

    def test_empty_command_after_the_wake_word_returns_to_idle(self):
        instance = bare_app()
        instance.follow_up_deadline = time.monotonic() + 5
        instance.gate_model.transcribe.return_value = recognized("да ладно")
        instance.model.transcribe.return_value = ([], None)
        with patch("transcription.emit_text") as emit_text, patch(
                "transcription.emit_state") as emit_state:
            instance.handle_phrase(self.audio)
        emit_text.assert_not_called()
        self.assertEqual(emit_state.call_args_list, [call("transcribing"), call("idle")])
        instance.restore_audio.assert_called_once()


class ConversationTests(unittest.TestCase):
    """After Kvist answered, the person talks on without the wake word; stray words do not count."""
    audio = np.zeros(1600, dtype=np.float32)

    def talk(self, instance, gate, main="что-то"):
        instance.gate_model.transcribe.return_value = recognized(gate)
        instance.model.transcribe.return_value = recognized(main)
        with patch("transcription.emit_text") as emit_text, patch("transcription.emit_state") as emit_state, patch(
                "transcription.emit_heard") as emit_heard:
            instance.handle_phrase(self.audio)
        return emit_text, emit_state, emit_heard

    def test_the_launcher_opens_a_conversation(self):
        instance = bare_app()
        with patch("app.sys.stdin", ['{"type": "converse", "seconds": 12, "min_words": 2}\n']), patch(
                "transcription.emit_state") as emit_state:
            instance.read_commands()
        self.assertGreater(instance.follow_up_deadline, time.monotonic() + 10)
        self.assertEqual(instance.follow_up_min_words, 2)
        emit_state.assert_called_once_with("listening")

    def test_a_real_phrase_in_a_conversation_needs_no_wake_word(self):
        instance = bare_app()
        instance.converse(12, 2)
        text, _, heard = self.talk(instance, "ты неправильно поняла задачу, надо было открыть не там, а тут",
                                   main="Ты неправильно поняла задачу, надо было открыть не там, а тут.")
        text.assert_called_once_with("Ты неправильно поняла задачу, надо было открыть не там, а тут.")
        heard.assert_not_called()

    def test_a_stray_word_is_not_a_task_and_the_conversation_stays_open(self):
        instance = bare_app()
        instance.converse(12, 2)
        text, _, heard = self.talk(instance, "да")
        text.assert_not_called()
        heard.assert_called_once_with("да")
        self.assertGreater(instance.follow_up_deadline, time.monotonic())

    def test_thinking_sounds_and_the_noise_whisper_writes_are_not_a_task(self):
        for phrase in ("ну ммм", "эм, ну", "Продолжение следует...", "Субтитры сделал DimaTorzok", "Thanks for watching!"):
            with self.subTest(phrase=phrase):
                instance = bare_app()
                instance.converse(12, 2)
                text, _, heard = self.talk(instance, phrase)
                text.assert_not_called()
                heard.assert_called_once_with(phrase)

    def test_without_a_conversation_a_phrase_for_nobody_stays_nobodys(self):
        instance = bare_app()
        text, _, heard = self.talk(instance, "ты неправильно поняла задачу, надо было открыть не там")
        text.assert_not_called()
        heard.assert_called_once()

    def test_a_command_is_followed_by_a_short_quiet_window_for_the_rest_of_a_long_sentence(self):
        instance = bare_app()
        before = time.monotonic()
        with patch("transcription.emit_text"), patch("transcription.emit_state") as emit_state:
            instance.gate_model.transcribe.return_value = recognized("Квист ты неправильно поняла задачу")
            instance.model.transcribe.return_value = recognized("Квист, ты неправильно поняла задачу")
            instance.handle_phrase(self.audio)
        self.assertGreater(instance.follow_up_deadline, before + 5)
        self.assertEqual(instance.follow_up_min_words, 2)
        self.assertNotIn(call("listening"), emit_state.call_args_list, "the screen is not told: nothing changes there")

        # The rest of the sentence arrives as a second phrase, without the wake word.
        text, _, heard = self.talk(instance, "надо было открыть не там, а тут", main="Надо было открыть не там, а тут.")
        text.assert_called_once_with("Надо было открыть не там, а тут.")
        heard.assert_not_called()

    def test_a_single_word_is_enough_when_the_question_is_yes_or_no(self):
        instance = bare_app()
        instance.converse(20, 1)
        text, _, _ = self.talk(instance, "да", main="Да.")
        text.assert_called_once_with("Да.")

    def test_the_wake_word_still_works_inside_a_conversation(self):
        instance = bare_app()
        instance.converse(12, 2)
        text, _, _ = self.talk(instance, "Квист открой ютуб", main="Квист, открой YouTube.")
        text.assert_called_once_with("открой YouTube.")


class LanguageTests(unittest.TestCase):
    def test_the_language_is_pinned_or_chosen_among_the_allowed_ones(self):
        instance = bare_app()
        audio = np.zeros(1600, dtype=np.float32)
        # Whisper's own favourite is Korean; the person speaks Russian or English.
        instance.gate_model.detect_language.return_value = (
            "ko", 0.6, [("ko", 0.6), ("ru", 0.3), ("en", 0.1)])
        with patch("transcription.LANGUAGE", None), patch("transcription.LANGUAGES", ("ru", "en")):
            self.assertEqual(instance.pick_language(audio), "ru")
        instance.gate_model.detect_language.return_value = (
            "ja", 0.5, [("ja", 0.5), ("en", 0.4), ("ru", 0.05)])
        with patch("transcription.LANGUAGE", None), patch("transcription.LANGUAGES", ("ru", "en")):
            self.assertEqual(instance.pick_language(audio), "en")
        with patch("transcription.LANGUAGE", "de"):
            self.assertEqual(instance.pick_language(audio), "de")
        with patch("transcription.LANGUAGE", None), patch("transcription.LANGUAGES", ("ru",)):
            self.assertEqual(instance.pick_language(audio), "ru")
        with patch("transcription.LANGUAGE", None), patch("transcription.LANGUAGES", ()):
            self.assertIsNone(instance.pick_language(audio))

    def test_the_main_language_is_kept_unless_another_wins_clearly(self):
        instance = bare_app()
        audio = np.zeros(1600, dtype=np.float32)
        cases = (
            ("a coin toss in a noisy room", [("ru", 0.35), ("en", 0.30)], "ru"),
            ("English a little ahead", [("ru", 0.20), ("en", 0.45)], "ru"),   # 2.25 times: not enough
            ("English clearly ahead", [("ru", 0.02), ("en", 0.90)], "en"),
            ("a bare 'Kvist'", [("ru", 0.05), ("en", 0.50)], "en"),
            ("English likely but weak", [("ru", 0.01), ("en", 0.30)], "ru"),    # under the minimum
        )
        for name, scores, expected in cases:
            with self.subTest(name), patch("transcription.LANGUAGE", None), patch("transcription.LANGUAGES", ("ru", "en")):
                instance.gate_model.detect_language.return_value = (scores[0][0], scores[0][1], scores)
                self.assertEqual(instance.pick_language(audio), expected)

    def test_the_chosen_language_and_its_prompt_reach_both_models(self):
        instance = bare_app()
        instance.gate_model.transcribe.return_value = recognized("Квист открой ютуб")
        instance.model.transcribe.return_value = recognized("Квист, открой YouTube.")
        with patch("transcription.LANGUAGE", None), patch("transcription.LANGUAGES", ("ru", "en")), patch(
                "transcription.emit_text"), patch("transcription.emit_state"):
            instance.handle_phrase(np.zeros(1600, dtype=np.float32))
        for model in (instance.gate_model, instance.model):
            kwargs = model.transcribe.call_args.kwargs
            self.assertEqual(kwargs["language"], "ru")
        self.assertIn("Квист", instance.model.transcribe.call_args.kwargs["initial_prompt"])

    def test_the_gate_keeps_a_short_first_word_by_not_filtering_silence(self):
        instance = bare_app()
        instance.gate_model.transcribe.return_value = recognized("Квист открой ютуб")
        instance.model.transcribe.return_value = recognized("Квист, открой YouTube.")
        with patch("transcription.emit_text"), patch("transcription.emit_state"):
            instance.handle_phrase(np.zeros(1600, dtype=np.float32))
        gate, main = instance.gate_model.transcribe.call_args.kwargs, instance.model.transcribe.call_args.kwargs
        self.assertFalse(gate["vad_filter"])
        self.assertEqual(gate["beam_size"], 5)
        self.assertTrue(main["vad_filter"])

    def test_a_language_without_its_own_prompt_gets_the_neutral_one(self):
        instance = bare_app()
        model = Mock()
        model.transcribe.return_value = recognized("text")
        instance.transcribe(model, np.zeros(1600, dtype=np.float32), 1, "de")
        self.assertEqual(model.transcribe.call_args.kwargs["initial_prompt"], INITIAL_PROMPT)


class AppTests(unittest.TestCase):
    def test_entrypoint_compatibility(self):
        self.assertIs(main.VoiceToTextApp, app.VoiceToTextApp)
        self.assertTrue(callable(main.main))

    @staticmethod
    def model_factory(fails=()):
        """A WhisperModel stand-in: `fails` lists (device, compute type) pairs that break on first use."""
        loaded = []

        def factory(name, device, compute_type):
            loaded.append((name, device, compute_type))
            model = Mock()
            if (device, compute_type) in fails:
                model.transcribe.side_effect = RuntimeError("Library libcublas.so.12 is not found")
            else:
                model.transcribe.return_value = ([], None)
            return model

        return factory, loaded

    def build(self, fails=()):
        factory, loaded = self.model_factory(fails)
        with patch("app.WhisperModel", side_effect=factory), patch("app.gpu_has_room", return_value=True), redirect_stdout(io.StringIO()):
            instance = app.VoiceToTextApp()
        return instance, loaded

    def test_the_large_model_loads_first_on_cuda_without_the_text_editor(self):
        with patch("editor.TextEditor.from_environment") as editor:
            instance, loaded = self.build()
        editor.assert_not_called()
        self.assertFalse(hasattr(instance, "text_editor"))
        self.assertEqual(loaded, [("large-v3", "cuda", "float16"), ("small", "cuda", "float16")])

    def test_a_full_gpu_falls_back_to_the_smaller_way_to_run(self):
        _, loaded = self.build(fails={("cuda", "float16")})
        self.assertEqual(
            loaded,
            [
                ("large-v3", "cuda", "float16"),
                ("large-v3", "cuda", "int8_float16"),
                ("small", "cuda", "float16"),
                ("small", "cuda", "int8_float16"),
            ],
        )

    def test_the_large_model_never_runs_on_the_cpu(self):
        gpu_broken = {("cuda", "float16"), ("cuda", "int8_float16")}
        instance, loaded = self.build(fails=gpu_broken)
        self.assertNotIn(("large-v3", "cpu", "int8"), loaded)
        self.assertEqual(loaded[-1], ("small", "cpu", "int8"))
        # The small model recognizes the commands when the large one has no room.
        self.assertIs(instance.model, instance.gate_model)

    def test_the_large_model_is_kept_when_it_fits(self):
        instance, _ = self.build()
        self.assertIsNot(instance.model, instance.gate_model)

    def test_no_way_to_run_the_small_model_stops_loudly(self):
        factory, _ = self.model_factory(fails={("cuda", "float16"), ("cuda", "int8_float16"), ("cpu", "int8")})
        with patch("app.WhisperModel", side_effect=factory), patch("app.gpu_has_room", return_value=True), redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                app.VoiceToTextApp()

    def test_a_model_that_cannot_fit_the_free_gpu_is_not_even_loaded(self):
        factory, loaded = self.model_factory()
        full = lambda name, compute_type: name != "large-v3"
        with patch("app.WhisperModel", side_effect=factory), patch("app.gpu_has_room", side_effect=full), redirect_stdout(io.StringIO()):
            instance = app.VoiceToTextApp()
        self.assertEqual(loaded, [("small", "cuda", "float16")])
        self.assertIs(instance.model, instance.gate_model)

    def test_room_is_judged_from_the_weights_and_the_free_memory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            with open(Path(folder, "model.bin"), "wb") as weights:
                weights.truncate(3_000_000_000)  # a sparse 3 GB file: nothing is written
            with patch("app.download_model", return_value=folder):
                for free, compute, expected in (
                    (2_100, "int8_float16", True),   # 3000 * 0.6 + 300
                    (2_099, "int8_float16", False),
                    (3_750, "float16", True),        # 3000 * 1.15 + 300
                    (3_749, "float16", False),
                ):
                    with self.subTest(free=free, compute=compute), patch("app.free_gpu_mb", return_value=free):
                        self.assertEqual(app.gpu_has_room("m", compute), expected)

    def test_unknown_memory_or_size_never_blocks_a_model(self):
        with patch("app.free_gpu_mb", return_value=None):
            self.assertTrue(app.gpu_has_room("m", "float16"))
        with patch("app.free_gpu_mb", return_value=10), patch("app.download_model", side_effect=OSError("offline")):
            self.assertTrue(app.gpu_has_room("m", "float16"))

    def test_one_failing_phrase_does_not_end_the_listening(self):
        instance = bare_app()
        instance.running = True
        instance.read_commands = Mock()
        instance.phrases.put([chunk(0.5, 0.2)])

        def fail(audio):
            instance.running = False
            raise RuntimeError("cuBLAS is gone")

        instance.handle_phrase = fail
        with patch("app.sd.InputStream"), patch("app.emit_state") as emit_state:
            instance.run()
        emit_state.assert_called_with("idle")

    def test_existing_deduplication(self):
        self.assertEqual(TranscriptionMixin._dedupe_text("привет привет"), "привет")
        self.assertEqual(TranscriptionMixin._dedupe_text("раз два раз два"), "раз два")


if __name__ == "__main__":
    unittest.main()
