# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import signal

import cuda_libs

cuda_libs.preload()

from app import VoiceToTextApp  # noqa: E402  (after the preload: it imports CTranslate2)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)


def main() -> None:
    app = VoiceToTextApp()

    def handle_signal(signum, frame) -> None:
        logging.info("Exiting...")
        app.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    app.run()


if __name__ == "__main__":
    main()
