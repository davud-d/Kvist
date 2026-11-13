# Kvist voice

The voice side of Kvist: ears that wait for the wake word, a voice that speaks the answers, and a screen with an orb that shows what is going on. Three parts:

| Part | Where | Does |
|---|---|---|
| Ears | `main.py` | The microphone stays open; say "Kvist" followed by a command and the recognized phrase is printed to stdout as a JSON line |
| Voice | `speak.py`, `moss_nano.py` | Speaks the lines it is sent (MOSS-TTS-Nano, on the CPU) and reports its loudness |
| Screen | [`screen/`](screen/README.md) (`kvist-voice-screen`, Rust) | The orb, the words, the menu; runs the core and both helpers |

Start everything with `frontends/voice/kvist-voice` (see "Run the voice screen"), or `kvist --frontend voice` / `/tui voice`: the CLI installer builds the screen and packs these files into the `kvist` binary, but not the models or the Python environments of the ears and the voice (see "Run" below).

[Русская версия](README.ru.md)

## How it works

1. The microphone is read continuously and cut into phrases by an energy gate: a phrase starts at the first loud block (half a second of audio before it is kept) and ends after a pause that grows with what was said (see "A long request" below).
2. A small Whisper model (the gate model, `small`) recognizes every phrase heard in the room and checks whether it starts with the wake word. Audio is passed in memory and never written to disk. Phrases without the wake word are discarded and nothing is emitted.
3. A phrase that starts with the wake word is recognized again by the main model (`large-v3`, CUDA if available, otherwise CPU), the wake word is removed, and a playing media player is paused (`playerctl`) until the command is delivered.
4. "Kvist" alone puts the program into the `listening` state for `FOLLOW_UP_SECONDS`; the next phrase is the command and needs no wake word.

The wake word is matched by spelling, not by sound: the first word of the phrase must be close to `WAKE_WORDS` (`WAKE_MATCH_RATIO` in `config.py`). A lower ratio catches more misheard variants and starts matching real words such as "квест".

## Events

One JSON object per line on stdout; logs go to stderr.

```json
{"event": "state", "state": "loading"}
{"event": "state", "state": "idle"}
{"event": "state", "state": "listening"}
{"event": "state", "state": "transcribing"}
{"event": "text", "text": "открой ютуб"}
{"event": "heard", "text": "хвост, открой ютуб"}
{"event": "phrase", "on": true}
{"event": "level", "value": 0.62, "pause": 0.0}
{"event": "phrase", "on": false}
{"event": "missed"}
```

`state` is one of `loading` (the models are loading), `idle` (waiting for the wake word), `listening` (the wake word was heard alone, the command is expected), `transcribing` (a request is being recognized; it stays so while the person goes on with it). `text` is one whole request, however many phrases it was said in; it comes before the `idle` that ends it and is absent when nothing was recognized. `heard` reports a phrase that was not addressed to Kvist (what the small model wrote down), so the screen can show what the room sounds like and a misheard wake word can be seen.

`phrase` says that somebody in the room began to speak (`on: true`) or that the phrase is over. While it lasts, `level` comes ten times a second: `value` is the loudness of the last block (0..1 on a decibel scale from -50 to -10 dB), and `pause` is how much of the pause that ends the phrase has passed (0 while they speak, 1 when the phrase ends). With these the screen shows the person that they are heard and how long they may think before the phrase is taken as finished. `missed` admits that words meant for Kvist could not be recognized, so that the person repeats them instead of waiting.

## Run

```sh
pip install -r requirements.txt
python main.py
```

Everything runs locally. Phrases are recognized on your machine and only those that start with the wake word are emitted. Music or video playing through speakers is picked up by the microphone and recognized as well; headphones avoid that.

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `WHISPER_MODEL` | `large-v3` | faster-whisper model for commands |
| `WHISPER_LANGUAGE` | none | pins one language (`ru`, `en`, …) |
| `WHISPER_LANGUAGES` | `ru,en` | the languages a phrase may be in; the likeliest is chosen per phrase |
| `WHISPER_GATE_MODEL` | `small` | faster-whisper model that listens for the wake word |

Thresholds and timings are in `config.py`. `SPEECH_RMS_THRESHOLD` depends on the microphone and room noise and may need tuning.

## Run the voice screen

```sh
cd frontends/voice/screen && cargo build --release      # once; needs kitty or Ghostty to run
frontends/voice/kvist-voice --workspace /path/to/project
```

`kvist-voice` starts `kvist-voice-screen`, which starts the core with `AGENT_VOICE_MODE=1` (short spoken answers and a narrator while the strong model thinks, see `docs/UI_API.md`, event `narration`), the ears and the voice. It reads these variables:

| Variable | Default | Meaning |
|---|---|---|
| `KVIST_VOICE_STT_PYTHON` | `frontends/voice/.venv/bin/python`, else `python3` | Python with `requirements.txt` installed |
| `KVIST_VOICE_TTS_PYTHON` | `$KVIST_VOICE_HOME/venv-tts/bin/python` | Python of the voice engine; without it the screen shows answers and stays silent |
| `KVIST_VOICE_HOME` | `$XDG_DATA_HOME/kvist/voice` | Data folder of the voice engine |
| `KVIST_TTS_ENGINE` | `silero` | `silero` or `moss` |
| `KVIST_VOICE_SCREEN` | the checkout's release build | The `kvist-voice-screen` binary |

A launch opens a new session (the core would otherwise continue the last one and answer from its context); `kvist-voice --continue` keeps the last one. `Tab` opens the menu: new session, sessions, model, provider, API key, web search, thinking effort, role, style, API settings, file changes, revert, interface (`/tui`); typing opens an input line for requests and `/commands`; `Esc` switches the microphone, `Ctrl+C` twice quits (see [`screen/README.md`](screen/README.md)).

`kvist-voice-screen --demo` shows every orb state without any of this.

## The voice

`tts_setup.sh [silero|moss|all]` installs an engine into `KVIST_VOICE_HOME` (a Python 3.12 environment and the engine's files; needs `uv`). The engine is chosen by `KVIST_TTS_ENGINE`:

| Engine | Voice | Size | Speed | Notes |
|---|---|---|---|---|
| `silero` (default) | Russian; `xenia`, `kseniya`, `baya` are women, `aidar`, `eugene` men | about 150 MB | 15 to 20 times faster than speech on the CPU | The model's licence is CC BY-NC 4.0 (non-commercial); it is downloaded for the person who runs the script and not bundled. It reads only Cyrillic, so Latin words are spelled in Russian (`YouTube` becomes "ютуб") |
| `moss` | [MOSS-TTS-Nano](https://github.com/OpenMOSS/MOSS-TTS-Nano), Apache-2.0; built-in voices are Chinese, English and Japanese | about 700 MB | slightly slower than speech | English is clear; Russian is garbled or only partly intelligible; a reference recording (`KVIST_TTS_VOICE_FILE`) may help |

Silero's settings: `KVIST_TTS_SPEAKER` (default `baya`), `KVIST_TTS_RATE` (`x-slow`, `slow`, `medium`, `fast`, `x-fast`; default `medium`), `KVIST_TTS_THREADS`, `KVIST_TTS_SILERO_MODEL`. Every sentence is followed by a short pause and brought to the same loudness.

**The model drops what it cannot read.** A digit, a symbol or a Latin word is lost without a sound: "3 файла в 15:30" was heard as "файла в", "45% заряда, 12 минут" as "заряда. Минут." That is what made a voice unintelligible "sometimes", and a slower rate or pauses did not help (measured: the same accuracy at every rate). So `speech_ru.py` writes everything out before synthesis: numbers in words ("один"/"два" agree with common nouns: "одна минута", "две строки"), times ("пятнадцать тридцать"), percentages, versions, file names and domains by how they are said (`hello.txt` is "хелло точка тэ-икс-тэ"), Latin names as they sound, abbreviations by letters, symbols by their names. A formula the model writes in LaTeX is turned into text by the screen (`screen/src/math.rs`: `√6646021 ≈ 2577,9878`, a minus as U+2212, a fraction as "разделить на") and `speech_ru.py` says the symbols ("корень из … приблизительно равно …", "в квадрате"); nothing is drawn, the screen has no transcript. Cases are not tracked ("в две тысячи двадцать шесть году" is read as "…год"), which is understood where a dropped number is not. The larger MOSS-TTS v1.5 (8B parameters, about 17 GB) does not fit a GPU with 8 GB that the ears share.

`speak.py` reads one JSON object per line on stdin and writes events on stdout:

```json
{"type": "speak", "id": 7, "kind": "answer", "text": "Готово."}
{"type": "stop"}
```
```json
{"event": "ready"}
{"event": "speaking"}
{"event": "level", "value": 0.4}
{"event": "idle"}
```

An `answer` cuts off whatever is being said. A `narration` line is dropped when the voice is busy: a late line is worse than none. Speech is made one sentence at a time, so it starts after the first sentence. The screen mutes the ears while the voice talks: send `{"type": "mute", "value": true}` to `main.py` on its stdin (the voice screen does), and `{"type": "converse", "seconds": n, "min_words": n}` to open a conversation (below). Another engine replaces `silero_tts.py` or `moss_nano.py` without touching the screen.

## What the ears do with languages and the wake word

- The language of a phrase is chosen among `WHISPER_LANGUAGES` (default `ru,en`), not among all of Whisper's: from all of them, a short Russian phrase was taken for Korean or Japanese. `WHISPER_LANGUAGE=ru` pins one; empty `WHISPER_LANGUAGES` allows any. The hint given to Whisper is in the language chosen and spells the wake word the way that language does.
- Whisper sometimes drops a short first word. Measured on 36 spoken variants of the same Russian phrases, one pass kept "Квист" in 78 to 83 % of them and two passes (without and with the hint) in all of them, so the second pass runs when the first heard no wake word. The silence filter is off for this pass: it cost the first word too.
- The large model runs only on the GPU and is not even loaded when `nvidia-smi` shows too little free video memory; the small model then recognizes the commands as well. Another program on the GPU decides what fits.
- A model that loaded can still run out of video memory in the middle of a phrase (seen with another program holding half of an 8 GB GPU: the trial on a second of silence passed, a real command failed, and the command was lost without a word). When the main model fails on a phrase, the small one recognizes that phrase and takes over for good, and the main model's memory is freed. If the small one fails too, the ears send `missed`.
- The wake word is the first word, so the gate model reads only the first `GATE_HEAD_SECONDS` (8 s) of a long phrase; the main model reads all of it.

## A long request, a stray word and a conversation

- **The pause that ends a phrase grows with what was said**: `END_SILENCE_MIN_SECONDS` (0.9 s) plus 0.35 s for every second of speech up to 2.4 s (a command), then 0.06 s for every further second up to 4 s (somebody dictating for half a minute stops to think for longer than somebody giving a command). A phrase may last five minutes (`MAX_RECORDING_SECONDS`; a minute cut a dictated request in two).
- **A request leaves when the person has finished it, as one text.** A pause longer than the one above still ends a phrase, and a request sent at that moment would start the assistant on half of it, and its spoken answer would then mute the microphone over the other half. So what was recognized is held for a little longer (`JOIN_GRACE_PER_SECOND`: 0.15 s for every second said, at most 1.2 s; recognizing a short command already takes longer than its share, so it is not kept waiting). If the person goes on, the next phrase is joined to it, and so on. Measured in real time with the real models on synthesized Russian speech: a request of 26 s in three phrases with pauses of 2.2 and 2.8 s, said after the bare wake word, and one of 40 s behind the wake word with pauses up to 3 s, each arrived as one text; before, the first was thrown away whole and the second came as three separate requests.
- **Only a voice keeps the request waiting.** What holds a recognized request longer than that is a phrase already being said, and for such a phrase the voice detector Whisper brings (a few milliseconds for the last four seconds, looked at three times a second) must find a voice in it: a fan, keys or a film in the next room hold the energy gate open for as long as they last, and a request that waited for them left twelve to forty seconds late (seen in the logs of a real run). A phrase too short to judge (under 0.6 s) is waited for; a voice that goes on is waited for at most `JOIN_WAIT_MAX_SECONDS` (30 s). Measured with the real models: a command followed by 25 s of steady noise left 30 s after it was said before, 0.7 s after.
- **A conversation covers a phrase that began inside it.** A window used to be checked when a phrase had been recognized, so only a short, hurried phrase got through: eight seconds after the bare wake word had to hold the whole command and its recognition. Now the moment the person began to speak counts, and a window does not close while somebody is speaking.
- **After a request is handed over, a quiet window of 6 s** still takes what the person adds after a longer silence (`CONTINUATION_SECONDS`, at least two words, no wake word, nothing changes on the screen); the core queues it as the next request.
- **A conversation**: when Kvist has finished speaking an answer, the screen sends `{"type": "converse", "seconds": 12, "min_words": 2}`; for that time a phrase needs no wake word (the orb shows "listening"). After a question the agent asked it is 20 s and one word ("да") is enough. A stray word, a thinking sound ("ну", "эм") or the noise Whisper writes for silence ("Продолжение следует", "Субтитры сделал…") is not a reply: it is reported as `heard` and the window stays open. The wake word still works at any moment.
- **A room that never goes quiet** (music, a video the assistant itself has opened) is one endless phrase to an energy threshold: a phrase that has not stopped is not recognized until there is a pause. Headphones avoid that. A noise that begins *after* a command no longer delays it (see above).

## Tests

They need no microphone or model download:

```sh
python -m unittest test_pipeline test_speak
```

The repository's root `pytest` does not collect this directory, because it needs the Whisper dependencies.

## License

MIT, see [LICENSE](LICENSE).
