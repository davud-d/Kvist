#!/usr/bin/env bash
# Installs a voice engine for speak.py.
#
#   tts_setup.sh [silero|moss|all]     default: silero
#
# silero  Russian, a woman (baya), about 150 MB, the CPU, much faster than speech.
#         The model's licence is CC BY-NC 4.0 (non-commercial); it is downloaded for you, not bundled.
# moss    MOSS-TTS-Nano (Apache-2.0), 0.1B, ONNX on the CPU; no Russian built-in voice, slower than speech.
#
# Everything goes under the data folder: a Python 3.12 environment (venv-tts) and
# the engines' files. Nothing is installed system-wide. Needs `uv`; `moss` also `git`.
#
#   KVIST_VOICE_HOME   data folder; default $XDG_DATA_HOME/kvist/voice
set -euo pipefail

MOSS_COMMIT=8b7bcc9
SILERO_URL=https://models.silero.ai/models/tts/ru/v5_6_ru.pt
engine="${1:-silero}"
case "$engine" in silero | moss | all) ;; *) echo "usage: tts_setup.sh [silero|moss|all]" >&2; exit 2 ;; esac

home="${KVIST_VOICE_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/kvist/voice}"
command -v uv >/dev/null || { echo "tts_setup.sh: uv is required" >&2; exit 1; }
mkdir -p "$home"
cd "$home"

if [ ! -x venv-tts/bin/python ]; then
    uv venv --python 3.12 venv-tts
    # Both engines need torch; the CPU build is enough.
    uv pip install --python venv-tts/bin/python --index-url https://download.pytorch.org/whl/cpu torch==2.7.0 torchaudio==2.7.0
    uv pip install --python venv-tts/bin/python "numpy>=1.24" soundfile sounddevice
fi

if [ "$engine" = silero ] || [ "$engine" = all ]; then
    if [ ! -f silero/v5_6_ru.pt ]; then
        mkdir -p silero
        curl -fL --retry 3 -o silero/v5_6_ru.pt.part "$SILERO_URL"
        mv silero/v5_6_ru.pt.part silero/v5_6_ru.pt
    fi
fi

if [ "$engine" = moss ] || [ "$engine" = all ]; then
    command -v git >/dev/null || { echo "tts_setup.sh: git is required for moss" >&2; exit 1; }
    if [ ! -d moss-tts-nano-src ]; then
        git clone https://github.com/OpenMOSS/MOSS-TTS-Nano.git moss-tts-nano-src
        git -C moss-tts-nano-src checkout --quiet "$MOSS_COMMIT"
    fi
    uv pip install --python venv-tts/bin/python "sentencepiece>=0.1.99" "onnxruntime>=1.20.0" huggingface_hub
    if [ ! -f nano-models/MOSS-TTS-Nano-100M-ONNX/browser_poc_manifest.json ]; then
        # The engine's source is put on the path explicitly, not through the working folder.
        (cd "$home" && venv-tts/bin/python -I - "$home" <<'PY'
import sys
from pathlib import Path

home = Path(sys.argv[1])
sys.path.insert(0, str(home / "moss-tts-nano-src"))
import onnx_tts_runtime as engine

engine._download_default_browser_onnx_assets(home / "nano-models")
PY
        )
    fi
fi

echo "Voice engine ($engine) ready in $home"
