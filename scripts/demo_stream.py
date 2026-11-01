# -*- coding: utf-8 -*-
"""A model that writes code at a set speed, for watching how an interface shows a write arrive.

The provider is a local OpenAI-compatible endpoint that streams one response of three tool calls
(a 30-line file, a 160-line file, then an edit of the first) at TOKENS_PER_SECOND. The real core
reads it, so the `call_stream` events a frontend gets are the real ones.

    python scripts/demo_stream.py glass            # Kvist Glass (kitty), type any request
    python scripts/demo_stream.py record out.jsonl # no UI: run a turn and save the core's events
    python scripts/demo_stream.py serve            # only the provider; prints the environment to use

`--tps N` changes the speed (150 by default).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
GLASS = [ROOT / "frontends/tui/glass/target/release/kvist-glass", ROOT / "frontends/tui/glass/target/debug/kvist-glass"]

# What a model's tokens look like: short pieces of words, punctuation, and the escapes of JSON.
TOKEN = re.compile(r'\\n *|\\.|\s?\w{1,6}|\s+|[^\w\s]')

STORE_FIRST = "def store(path: Path) -> dict[str, int]:"
STORE_REPLACE = ("    totals: dict[str, int] = {}\n", "    totals: dict[str, int] = {}\n    seen: set[str] = set()\n")


def store_source() -> str:
    """A 30-line module."""
    return '''"""Counts words per file under a folder."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

__all__ = ["words", "store", "top"]

WORD = re.compile(r"[A-Za-z_]{3,}")


def words(text: str) -> list[str]:
    """Lower-cased words of at least three letters."""
    return [match.lower() for match in WORD.findall(text)]


def store(path: Path) -> dict[str, int]:
    """Total count of every word in the text files under `path`."""
    totals: dict[str, int] = {}
    for file in sorted(path.rglob("*.txt")):
        counts = Counter(words(file.read_text(encoding="utf-8")))
        for word, number in counts.items():
            totals[word] = totals.get(word, 0) + number
    return totals


def top(totals: dict[str, int], n: int = 5) -> list[tuple[str, int]]:
    """The n most common words, ties in alphabetical order."""
    return sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:n]
'''


FUNCTIONS = [
    ("parse_{n}", "line: str", "tuple[str, int]", ['    """Split `name=value` and read the value as an integer."""', '    name, _, raw = line.partition("=")', "    return name.strip(), int(raw.strip() or 0)"]),
    ("clamp_{n}", "value: float, low: float, high: float", "float", ['    """Keep `value` between `low` and `high`."""', "    if low > high:", '        raise ValueError("low is above high")', "    return max(low, min(high, value))"]),
    ("chunks_{n}", "items: list[int], size: int", "list[list[int]]", ['    """Cut `items` into pieces of at most `size`."""', "    if size < 1:", '        raise ValueError("size must be positive")', "    return [items[i : i + size] for i in range(0, len(items), size)]"]),
    ("merge_{n}", "left: dict[str, int], right: dict[str, int]", "dict[str, int]", ['    """Add the numbers of two dictionaries key by key."""', "    merged = dict(left)", "    for key, number in right.items():", "        merged[key] = merged.get(key, 0) + number", "    return merged"]),
    ("window_{n}", "values: list[float], width: int", "list[float]", ['    """Moving average over `width` values."""', "    if width < 1 or width > len(values):", "        return []", "    total = sum(values[:width])", "    averages = [total / width]", "    for index in range(width, len(values)):", "        total += values[index] - values[index - width]", "        averages.append(total / width)", "    return averages"]),
    ("label_{n}", "number: int", "str", ['    """Spell small numbers, the rest in digits."""', '    names = ["zero", "one", "two", "three", "four", "five"]', "    return names[number] if 0 <= number < len(names) else str(number)"]),
]


def pipeline_source() -> str:
    """A module of exactly 160 lines."""
    lines = ['"""A small pipeline: parse, clamp, group and report numbers."""', "from __future__ import annotations", "", "import logging", "from dataclasses import dataclass, field", "", "log = logging.getLogger(__name__)", "", ""]
    lines += ["@dataclass(slots=True)", "class Report:", '    """What a run found."""', "", "    rows: list[tuple[str, int]] = field(default_factory=list)", "    skipped: int = 0", "", "    def add(self, name: str, number: int) -> None:", "        self.rows.append((name, number))", "", "    def total(self) -> int:", "        return sum(number for _, number in self.rows)", "", ""]
    number = 1
    while len(lines) < 150:
        name, arguments, result, body = FUNCTIONS[(number - 1) % len(FUNCTIONS)]
        block = [f"def {name.format(n=number)}({arguments}) -> {result}:", *body, "", ""]
        if len(lines) + len(block) > 150:
            break
        lines += block
        number += 1
    lines += ["def run(source: list[str]) -> Report:", '    """Parse every line, count the ones that do not read."""', "    report = Report()", "    for line in source:", "        try:", "            report.add(*parse_1(line))", "        except ValueError:", "            report.skipped += 1", '            log.warning("skipped %r", line)', "    return report"]
    while len(lines) < 160:
        lines.insert(-1, "")
    return "\n".join(lines[:160]) + "\n"


def plan() -> list[dict]:
    store = store_source()
    old = STORE_REPLACE[0]
    assert old in store
    return [
        {"name": "write_file", "arguments": {"path": "demo/store.py", "content": store}},
        {"name": "write_file", "arguments": {"path": "demo/pipeline.py", "content": pipeline_source()}},
        {"name": "replace_in_file", "arguments": {"path": "demo/store.py", "old_text": old, "new_text": STORE_REPLACE[1]}},
    ]


class Provider:
    """OpenAI-compatible chat endpoint: the three calls above, then a read, then "Done."."""

    def __init__(self, tokens_per_second: float) -> None:
        self.tokens_per_second = tokens_per_second
        self.responses = 0
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                return

            def do_GET(self) -> None:
                body = json.dumps({"object": "list", "data": [{"id": "demo-fast", "object": "model"}, {"id": "demo-smart", "object": "model"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
                provider.answer(self, request)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def message(self, request: dict) -> dict:
        tools = {tool["function"]["name"] for tool in request.get("tools") or []}
        answered = sum(1 for item in request.get("messages") or [] if item.get("role") == "tool")
        if tools == {"ping"}:
            return {"calls": [("ping", {"ok": True})]}
        if not tools:
            return {"text": "Demo"}
        if answered == 0:
            return {"calls": [(call["name"], call["arguments"]) for call in plan()]}
        if answered == 3:
            return {"calls": [("read_file", {"path": "demo/store.py"})]}
        return {"text": "Done: demo/store.py and demo/pipeline.py are written."}

    def answer(self, handler: BaseHTTPRequestHandler, request: dict) -> None:
        message = self.message(request)
        if not request.get("stream"):
            handler.send_response(200)
            body = json.dumps(self.whole(message)).encode()
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        try:
            self.stream(handler, message)
        except (BrokenPipeError, ConnectionResetError):
            return

    @staticmethod
    def whole(message: dict) -> dict:
        calls = [{"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}} for i, (name, arguments) in enumerate(message.get("calls", []))]
        reply = {"role": "assistant", "content": message.get("text"), **({"tool_calls": calls} if calls else {})}
        return {"id": "demo", "object": "chat.completion", "model": "demo", "choices": [{"index": 0, "message": reply, "finish_reason": "tool_calls" if calls else "stop"}]}

    def stream(self, handler: BaseHTTPRequestHandler, message: dict) -> None:
        self.responses += 1
        prefix = f"call_{self.responses}"

        def send(delta: dict, finish: str | None = None) -> None:
            chunk = {"id": "demo", "object": "chat.completion.chunk", "created": 0, "model": "demo", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            handler.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            handler.wfile.flush()

        if "text" in message:
            send({"role": "assistant", "content": message["text"]})
            send({}, "stop")
        else:
            due = time.monotonic()
            for index, (name, arguments) in enumerate(message["calls"]):
                send({"tool_calls": [{"index": index, "id": f"{prefix}_{index}", "type": "function", "function": {"name": name, "arguments": ""}}]})
                for token in TOKEN.findall(json.dumps(arguments)):
                    due += 1 / self.tokens_per_second
                    time.sleep(max(0.0, due - time.monotonic()))
                    send({"tool_calls": [{"index": index, "function": {"arguments": token}}]})
            send({}, "tool_calls")
        handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()


def environment(provider: Provider, folder: Path) -> dict[str, str]:
    """The settings that make Kvist use `provider`, in a config folder of its own."""
    config = folder / "config"
    config.mkdir()
    os.environ["KVIST_CONFIG_DIR"] = str(config)
    sys.path.insert(0, str(ROOT))
    from kernel.config.user_config import save_custom_provider

    save_custom_provider("demo", {"label": "Demo", "base_url": provider.url, "api_key": "demo-key", "models": {"fast": "demo-fast", "smart": "demo-smart"}, "streaming": True})
    return {
        "KVIST_CONFIG_DIR": str(config), "AGENT_LLM_PROVIDER": "custom:demo", "AGENT_FAST_MODEL": "demo-fast", "AGENT_SMART_MODEL": "demo-smart",
        "AGENT_WRITE_APPROVAL": "0", "AGENT_API_INTERVAL": "0", "AGENT_REASONING_EFFORT": "false",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost", "KVIST_TRAY_HOST": "0",
        "KVIST_CORE_CMD": json.dumps([str(PYTHON), str(ROOT / "kernel/__main__.py")]),
    }


def record(env: dict[str, str], workspace: Path, target: Path) -> int:
    """Runs one turn with the real core and saves its events with their times."""
    process = subprocess.Popen(
        [str(PYTHON), str(ROOT / "kernel/__main__.py"), "--workspace", str(workspace), "--frontend", "stdio-multi", "--role", "coder"],
        cwd=ROOT, env=dict(os.environ, **env, PYTHONUNBUFFERED="1"), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )
    events: list[dict] = []
    started = 0.0

    def read() -> None:
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event["at_ms"] = round((time.monotonic() - started) * 1000) if started else 0
            events.append(event)

    threading.Thread(target=read, daemon=True).start()
    deadline = time.monotonic() + 60
    while not any(e.get("type") == "hello" for e in events) and time.monotonic() < deadline:
        time.sleep(0.05)
    started = time.monotonic()
    process.stdin.write("write the demo files\n")
    process.stdin.flush()
    while not any(e.get("type") == "turn_end" for e in events) and time.monotonic() < deadline + 120:
        time.sleep(0.1)
    process.stdin.close()
    process.terminate()
    target.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events if e.get("type") != "manifest"), encoding="utf-8")
    streamed = [e for e in events if e.get("type") == "call_stream"]
    print(f"{len(events)} events, {len(streamed)} call_stream, turn took {events[-1]['at_ms'] / 1000:.1f}s -> {target}")
    return 0 if streamed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["glass", "record", "serve"])
    parser.add_argument("output", nargs="?", help="record: where to save the events")
    parser.add_argument("--tps", type=float, default=150.0, help="tokens per second the model writes at")
    args = parser.parse_args()
    provider = Provider(args.tps)
    with tempfile.TemporaryDirectory(prefix="kvist-demo-") as scratch:
        folder = Path(scratch)
        workspace = folder / "workspace"
        workspace.mkdir()
        env = environment(provider, folder)
        if args.mode == "record":
            return record(env, workspace, Path(args.output or "demo-events.jsonl"))
        if args.mode == "serve":
            print(json.dumps(env, indent=2))
            print(f"workspace: {workspace}\nprovider: {provider.url}  (Ctrl+C to stop)")
            threading.Event().wait()
            return 0
        binary = next((path for path in GLASS if path.exists()), None)
        if binary is None:
            print("build Kvist Glass first: cd frontends/tui/glass && cargo build --release")
            return 1
        print("type any request; the model answers with a 30-line file, a 160-line file and an edit")
        return subprocess.call([str(binary), "--workspace", str(workspace)], env=dict(os.environ, **env))


if __name__ == "__main__":
    sys.exit(main())
