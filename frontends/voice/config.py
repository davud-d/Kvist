# -*- coding: utf-8 -*-
from __future__ import annotations

import os

FS = 16000
CHANNELS = 1
# WHISPER_LANGUAGE pins the language (an ISO code such as "ru" or "en"). Otherwise
# the language of each phrase is chosen among WHISPER_LANGUAGES (default Russian
# and English, comma separated; empty means any language). Letting Whisper pick
# from all of its languages mistakes a short Russian phrase for Korean or Japanese.
LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "").strip() or None
LANGUAGES = tuple(
    code.strip() for code in os.environ.get("WHISPER_LANGUAGES", "ru,en").split(",") if code.strip()
)

# The first of WHISPER_LANGUAGES is the one the person mostly speaks. Another language is
# chosen only when it clearly wins: this many times more likely than the first, and at least
# this likely in itself. A short phrase in a noisy room can score 0.35 against 0.30; the
# likelier of the two is then a coin toss, and English text for a Russian "как дела" is the cost.
LANGUAGE_SWITCH_FACTOR = 3.0
LANGUAGE_SWITCH_MIN = 0.4

# The microphone is read in fixed blocks so the pre-roll length is exact.
BLOCK_SECONDS = 0.1
# Audio kept from before the first loud block, so the start of the wake word is not cut.
PRE_ROLL_SECONDS = 0.5

# A phrase starts at the first block above the RMS threshold and ends after a pause.
# The RMS threshold is for float32 samples in [-1, 1] and depends on the microphone
# gain and room noise: raise it if background noise keeps starting phrases.
SPEECH_RMS_THRESHOLD = 0.015
# A phrase ends after a pause that grows with how much was said: a short command is
# finished at once, while a long sentence has thinking pauses in it ("неправильно
# поняла задачу... надо было открыть не там, а тут"). A fixed 1.2 s cut such a sentence
# in two, and the second half, without the wake word, was thrown away.
END_SILENCE_MIN_SECONDS = 0.9
END_SILENCE_PER_SPEECH_SECOND = 0.35
# The longest pause inside a command (reached after about four seconds of speech).
END_SILENCE_COMMAND_SECONDS = 2.4
# Somebody who dictates for half a minute stops to think for longer than somebody who
# gives a command, so beyond a command's length the pause keeps growing, slowly: waiting
# four seconds after thirty seconds of speech is not felt, after a five-second command it is.
END_SILENCE_PER_DICTATED_SECOND = 0.06
END_SILENCE_MAX_SECONDS = 4.0
# One phrase is at most this long. It is not a limit on what a person may say, only a guard
# against a room that never goes quiet: a minute cut a dictated request in two.
MAX_RECORDING_SECONDS = 300.0
# Phrases with less speech than this (clicks, coughs) are dropped without recognition.
MIN_SPEECH_SECONDS = 0.4
# At most this many phrases wait for recognition; later ones are dropped.
MAX_QUEUED_PHRASES = 4

# The wake word is "Kvist". Whisper writes the name in Cyrillic when it hears Russian
# speech, so both spellings are listed; Latin and Cyrillic never match each other.
# Add the spelling of another script here if Whisper uses it. A phrase counts as
# addressed to Kvist when its first word is this close (difflib ratio) to one of them.
# Whisper spells the name as it hears it, so an exact match would miss "Квис" or
# "Квиста"; a lower ratio starts matching "квест".
WAKE_WORDS = ("kvist", "квист")
WAKE_MATCH_RATIO = 0.85
# After the wake word alone, the next phrase within this time is the command.
FOLLOW_UP_SECONDS = 8.0
# Right after a command is handed over, the person may be going on ("...и поставь это в календаре"):
# for this long a further phrase of at least this many words is taken as part of the conversation.
CONTINUATION_SECONDS = 6.0
CONTINUATION_MIN_WORDS = 2
# A request is handed over only when the person has finished it. The pause above ends a phrase,
# but somebody dictating stops for longer than that in the middle of a thought, and a request
# sent at that moment starts the assistant on half of it (and its spoken answer then mutes the
# microphone over the other half). So what was recognized is held for a little longer; if the
# person goes on, the next phrase is joined to it, and one request leaves when they are quiet.
# The wait is a share of what was said, so a short command is not kept waiting: recognizing
# it already takes longer than its share.
JOIN_GRACE_PER_SECOND = 0.15
JOIN_GRACE_MAX_SECONDS = 1.2
# What keeps the request waiting longer than that is a phrase already being said, and only a voice
# counts: a fan, the keys or a film in the next room hold the energy gate open for as long as they
# last, and a request that waited for them left twelve to forty seconds late. The voice detector
# (the one Whisper brings) looks at the last few seconds of the phrase, not oftener than this.
VOICE_CHECK_MIN_SECONDS = 0.6
VOICE_CHECK_TAIL_SECONDS = 4.0
VOICE_CHECK_EVERY_SECONDS = 0.3
# Even a voice does not hold the request for more than this: a film that goes on talking is no continuation.
JOIN_WAIT_MAX_SECONDS = 30.0
# After the bare wake word a single word is a command ("стоп"); in a conversation (below) it is not.
FOLLOW_UP_MIN_WORDS = 1
# What Whisper writes for noise and silence; it must never be taken for a command.
NOISE_PHRASES = (
    "продолжение следует", "субтитры", "спасибо за просмотр", "подписывайтесь", "редактор субтитров",
    "thanks for watching", "thank you for watching", "subtitles by", "please subscribe", "amara.org",
)
# Sounds people make while thinking: alone, they are not a request.
FILLERS = frozenset({"ну", "эм", "э", "хм", "ага", "угу", "м", "мм", "ммм", "эээ", "uh", "um", "hmm", "mm", "ah", "oh"})

# Every phrase heard in the room is recognized by the small gate model to look for the
# wake word; only phrases that start with it reach the main model.
# Measured on spoken Russian: with the silence filter on and a beam of 1 the small model
# drops a short first word ("Квист") in a longer phrase; with the filter off and a beam
# of 5 it keeps it. A phrase always arrives with silence round it (the pre-roll and the
# pause that ended it), so the filter has nothing to cut and only costs the first word.
# The wake word is the first word, so the gate model reads only the start of a long phrase:
# reading three minutes of dictation twice to find one word would keep the person waiting.
GATE_HEAD_SECONDS = 8.0
GATE_BEAM_SIZE = 5
GATE_VAD_FILTER = False
MAIN_BEAM_SIZE = 5

# Whisper is a recognition model, not an LLM: initial_prompt only hints at style and
# vocabulary. Long instructions provoke repeats, loops and prompt text leaking into
# the result, so it stays short. A prompt in the language being spoken, with the
# wake word spelled the way that language writes it, helps the name come out right.
INITIAL_PROMPTS = {
    "ru": "Квист, открой YouTube. Создай файл, поставь в календаре. Kvist.",
    "en": "Kvist, open YouTube. Create a file, put it in the calendar.",
}
INITIAL_PROMPT = (
    "Python, Linux, OpenAI, DeepSeek, Claude, Gemini, GitHub, Ollama, "
    "Whisper, JSON, API, CLI, VS Code, PyCharm, YouTube, Fiverr, Kvist."
)
