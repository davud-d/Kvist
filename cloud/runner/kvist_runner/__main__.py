# -*- coding: utf-8 -*-
"""kvist-runner --config runner.json"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from pathlib import Path

from . import release
from .config import RunnerConfig, RunnerConfigError, load_config
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
    waiting = [asyncio.create_task(stop.wait()), asyncio.create_task(runner.restart.wait())]
    await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
    switched = runner.restart.is_set()
    for waiter in waiting:
        waiter.cancel()
    if not switched:
        await runner.shutdown()
    connection.cancel()
    if switched:
        _start_again(config, config_path)
    return 0


def _start_again(config: RunnerConfig, config_path: Path) -> None:
    """The same process, same pid, on the release the pointer names: systemd sees nothing."""
    env = {**os.environ, **release.overlay_env(config)}
    os.execve(sys.executable, [sys.executable, "-m", "kvist_runner", "--config", str(config_path)], env)


def main() -> None:
    parser = argparse.ArgumentParser(prog="kvist-runner")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # After a reboot the image's code starts first; one step further and it is the release that was switched to.
    with contextlib.suppress(RunnerConfigError):
        config = load_config(args.config)
        if release.needs_restart(config):
            _start_again(config, args.config)
    sys.exit(asyncio.run(_main(args.config)))


if __name__ == "__main__":
    main()
