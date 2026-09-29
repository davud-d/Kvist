#!/usr/bin/env python3
"""Write the throwaway config a guide recording starts from.

Each guide gets its own HOME so the recordings do not depend on the order they
were taken in, and so a guide that changes a setting cannot leak that change
into the next one's screen.

Two shapes:
  first-run  no provider chosen yet — what guide 01 needs to show the picker
  ready      OmniRoute already pointed at the local recording gateway

usage: seed-config.py <home> first-run|ready [port]
"""
from __future__ import annotations

import json
import pathlib
import sys

MODELS = {"fast": "local-fast", "smart": "local-smart", "planner": "local-planner"}


def main() -> int:
    if len(sys.argv) not in (3, 4):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    home = pathlib.Path(sys.argv[1])
    shape = sys.argv[2]
    port = sys.argv[3] if len(sys.argv) == 4 else "20129"

    payload: dict[str, object] = {
        "omniroute_base_url": f"http://127.0.0.1:{port}/v1",
        "llm_models": {"omniroute": dict(MODELS)},
    }
    if shape == "ready":
        payload["llm_provider"] = "omniroute"
        # The gateway ignores it; it exists so the guide is not asked for one.
        payload["omniroute_api_key"] = "local-gateway"
    elif shape != "first-run":
        print(f"unknown shape: {shape}", file=sys.stderr)
        return 2

    target = home / ".kvist"
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print(f"seeded {shape} config in {home}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
