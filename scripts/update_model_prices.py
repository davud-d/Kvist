# -*- coding: utf-8 -*-
"""Rebuild kernel/llm/model_prices.json from a LiteLLM price file you downloaded yourself.

Usage: python scripts/update_model_prices.py <litellm_prices.json>

The script never touches the network. Output prices are USD per 1M tokens; a price the
source does not state is written as null so the reader can tell "free" from "unknown".
``max_output_tokens`` is the model's output ceiling, copied as is (null when unstated).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "kernel" / "llm" / "model_prices.json"

# LiteLLM field -> our field. Source prices are USD per single token.
_FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cache_read": "cache_read_input_token_cost",
    "cache_write": "cache_creation_input_token_cost",
}
_PER_MILLION = 1_000_000


def _per_million(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    return round(value * _PER_MILLION, 9)


def convert(source: dict) -> dict:
    models: dict[str, dict] = {}
    for key, entry in sorted(source.items()):
        if not isinstance(entry, dict) or key == "sample_spec":
            continue
        prices = {name: _per_million(entry.get(field)) for name, field in _FIELDS.items()}
        # Models without a token price (image, audio-per-second, rerank, ...) cannot be costed per token.
        if prices["input"] is None or prices["output"] is None:
            continue
        cap = entry.get("max_output_tokens")
        models[key] = {
            "provider": entry.get("litellm_provider"),
            **prices,
            # A token count, not a price: null when the source does not state a usable one.
            "max_output_tokens": cap if type(cap) is int and cap > 0 else None,
        }
    return {"source": "litellm", "unit": "usd_per_1m_tokens", "models": models}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    source = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    table = convert(source)
    OUTPUT.write_text(json.dumps(table, indent=0, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(table['models'])} models -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
