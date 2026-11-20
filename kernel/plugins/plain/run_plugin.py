# -*- coding: utf-8 -*-
"""Runs one plain plugin's ``run(request)`` inside its sandbox.

Started by the core as ``python3 -I -B run_plugin.py <plugin.py>``: the request
arrives on stdin, the answer leaves on stdout as one JSON object, ``{"text": ...}``
or ``{"error": ...}``. Whatever the plugin prints goes to stderr, so a ``print``
left in a plugin cannot break the answer.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path


def _call(path: Path, request: str) -> dict[str, str]:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A plugin's mistake is reported to the person in full; nothing here is hidden.
    try:
        spec.loader.exec_module(module)
        text = module.run(request)
    except Exception:  # noqa: BLE001 - any error of a plugin is its author's to read
        return {"error": traceback.format_exc(limit=-3)}
    if not isinstance(text, str):
        return {"error": f"run() must return text (str), it returned {type(text).__name__}"}
    return {"text": text}


def main() -> None:
    path = Path(sys.argv[1])
    sys.path[:0] = [str(Path(__file__).resolve().parent), str(path.parent)]
    request = sys.stdin.read()
    answer_stream = sys.stdout
    sys.stdout = sys.stderr
    answer_stream.write(json.dumps(_call(path, request), ensure_ascii=False))
    answer_stream.flush()


if __name__ == "__main__":
    main()
