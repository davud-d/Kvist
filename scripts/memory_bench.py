# -*- coding: utf-8 -*-
"""Latency of long-term memory on this machine, against the targets of the memory design.

    .venv/bin/python scripts/memory_bench.py [--records 2000] [--rounds 200]

Seeds a scratch workspace with records, then measures p50/p95 of a record read,
a small skill-storage write, a search and the prompt's memory block. The targets
are measuring goals, not guarantees: disk type, database size and other processes
move them, so the report says which machine and size it measured.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kernel.memory.service import MemoryService  # noqa: E402
from kernel.memory.store import MemoryStore, close_connections  # noqa: E402
from kernel.plugins.storage import SkillStorage  # noqa: E402

TARGETS_MS = {"get": 10, "remember": 50, "storage set": 20, "search": 50, "prompt block": 100}
_TOPICS = ("config writes", "tests", "build", "release", "importer", "cache", "auth", "logging", "panel", "deploy")
_WORDS = ("queue", "lock", "postgres", "pytest", "PyInstaller", "lxml", "redis", "token source", "Textual", "cron")


def seed(workspace: Path, records: int) -> list[str]:
    store = MemoryStore.for_workspace(workspace)
    ids = []
    for index in range(records):
        topic, word = _TOPICS[index % len(_TOPICS)], _WORDS[(index * 7) % len(_WORDS)]
        content = (f"Finding {index}: the {topic} part relies on {word}; changing it needs a check of module "
                   f"{index % 37} and its tests (запись {index}, тесты модуля {index % 37}).")
        ids.append(store.remember(content, kind="project_fact", trust="observed", actor="agent", topic=topic)[1].id)
    return ids


def measure(operation: Callable[[int], object], rounds: int) -> dict[str, float]:
    timings = []
    for index in range(rounds):
        started = time.perf_counter()
        operation(index)
        timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    return {"p50": statistics.median(timings), "p95": timings[int(len(timings) * 0.95) - 1]}


def run(records: int, rounds: int, workspace: Path) -> dict[str, dict[str, float]]:
    ids = seed(workspace, records)
    store = MemoryStore.for_workspace(workspace)
    # Writes go to their own database: the seeded one may already be at the record quota.
    (workspace / "writes").mkdir()
    fresh = MemoryStore.for_workspace(workspace / "writes")
    storage = SkillStorage(workspace, "bench", "1.0.0")
    memory = MemoryService(workspace, with_user_scope=False)

    def tokens(text: str) -> int:
        return len(text) // 4

    queries = ["почему падают тесты модуля 12", "config writes queue lock", "release cron", "importer lxml threads"]
    return {
        "get": measure(lambda i: store.get(ids[(i * 31) % len(ids)]), rounds),
        "remember": measure(lambda i: fresh.remember(f"Measured finding {i} about module {i % 23}", kind="project_fact",
                                                     trust="observed", actor="agent"), rounds),
        "storage set": measure(lambda i: storage.set("workspace", "workspace", "cursor", {"i": i}, None), rounds),
        "search": measure(lambda i: store.search(queries[i % len(queries)], limit=10), rounds),
        "prompt block": measure(lambda i: memory.prompt_block(queries[i % len(queries)], tokens), rounds),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--records", type=int, default=2000)
    parser.add_argument("--rounds", type=int, default=200)
    parser.add_argument("--dir", type=Path, help="Where to make the scratch workspace (default: system temp).")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(dir=args.dir) as folder:
        try:
            results = run(args.records, args.rounds, Path(folder))
        finally:
            close_connections()
    print(f"{args.records} records, {args.rounds} rounds, in {args.dir or tempfile.gettempdir()}")
    print(f"{'operation':<14}{'p50 ms':>9}{'p95 ms':>9}{'target':>9}")
    for name, numbers in results.items():
        verdict = "ok" if numbers["p95"] <= TARGETS_MS[name] else "OVER"
        print(f"{name:<14}{numbers['p50']:>9.2f}{numbers['p95']:>9.2f}{TARGETS_MS[name]:>7} {verdict}")


if __name__ == "__main__":
    main()
