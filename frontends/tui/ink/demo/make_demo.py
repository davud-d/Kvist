# -*- coding: utf-8 -*-
"""Re-encode the demo recordings into the clips the setup screen plays on the interface step.

    python3 demo/make_demo.py [--crf 25]

Writes ``demo/claude-style.mp4`` (from ``docs/media/Claude-Code-demo-kvist.mp4``) and
``demo/classic-style.mp4`` (from ``docs/media/Classic-demo-kvist.mp4``): H.264 1280x720 at the
recording's frame rate, no audio, ``yuv420p`` so every ffmpeg build decodes it. A recording that is
not 16:9 is cropped to it from the middle (Classic's window is a little wider than 16:9, and the crop
only takes dark border and the edge of the photograph). Frames are shown at most 960 pixels wide, so
720p keeps what can be seen and costs the player's ffmpeg half the decoding; at CRF 25 the text stays
sharp and each file is a few megabytes, which ship beside the bundle (``build.mjs`` copies them into
``dist-ui/``). The setup screen decodes them with the user's own ffmpeg and scales them to the
rectangle they are shown in (``setup/video.ts``).

Needs ffmpeg (dev-time only; the generated files are what ships).
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

# Paths are relative to the Ink app (frontends/tui/ink), whatever the cwd.
APP = Path(__file__).resolve().parent.parent
MEDIA = APP.parent.parent.parent / "docs" / "media"
CLIPS = (
    (MEDIA / "Claude-Code-demo-kvist.mp4", APP / "demo" / "claude-style.mp4"),
    (MEDIA / "Classic-demo-kvist.mp4", APP / "demo" / "classic-style.mp4"),
)
FRAME_FILTER = "crop='min(iw,ih*16/9)':ih,scale=1280:720:flags=lanczos"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--crf", type=int, default=25)
    args = parser.parse_args()

    for source, output in CLIPS:
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y", "-i", str(source), "-an",
                "-vf", FRAME_FILTER, "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
            ],
            check=True,
        )
        print(f"{output.name}: {output.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
