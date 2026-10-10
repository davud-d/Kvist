# -*- coding: utf-8 -*-
"""Re-encode the Claude-style demo recording into the clip the setup screen plays.

    python3 demo/make_demo.py [--source ../../../docs/media/Claude-Code-demo-kvist.mp4] [--crf 25]

Writes ``demo/claude-style.mp4``: H.264 1280x720 at the recording's 60 fps, no audio, ``yuv420p``
so every ffmpeg build decodes it. The recording is an 18 MB, 1080p screen capture. Frames are
shown at most 960 pixels wide, so 720p keeps what can be seen and costs the player's ffmpeg half
the decoding; at CRF 25 the text stays sharp and the file is about 3.5 MB, which ships beside the bundle
(``build.mjs`` copies it into ``dist-ui/``). The setup screen decodes it with the user's own
ffmpeg and scales it to the rectangle it is shown in (``setup/video.ts``).

Needs ffmpeg (dev-time only; the generated file is what ships).
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

# Paths are relative to the Ink app (frontends/tui/ink), whatever the cwd.
REPO = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO.parent.parent.parent / "docs" / "media" / "Claude-Code-demo-kvist.mp4"
OUTPUT = REPO / "demo" / "claude-style.mp4"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--crf", type=int, default=25)
    args = parser.parse_args()

    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(args.source), "-an",
            "-vf", "scale=1280:720:flags=lanczos", "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUTPUT),
        ],
        check=True,
    )
    print(f"{OUTPUT.name}: {OUTPUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
