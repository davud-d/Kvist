# -*- coding: utf-8 -*-
"""kvist-runner --config runner.json"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from .config import RunnerConfigError, load_config
from .runner import Runner


async def _main(config_path: Path) -> int:
    try:
        config = load_config(config_path)
    except RunnerConfigError as error:
        logging.getLogger("kvist_runner").error("%s", error)
        return 2
    runner = Runner(config)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    connection = asyncio.create_task(runner.run_forever())
    await stop.wait()
    await runner.shutdown()
    connection.cancel()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="kvist-runner")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(asyncio.run(_main(args.config)))


if __name__ == "__main__":
    main()
