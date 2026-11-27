# -*- coding: utf-8 -*-
"""What a provider's own ``/models`` catalog says about each model.

Kvist used to keep only the ids from that catalog and guess everything else
from the model's name: whether it sees images, how large its window is. Many
providers state both outright, and a stated value outranks any guess. The
shapes differ per vendor, so each field is read from every name it is known
under:

* DeepSeek, Groq: ``context_window``, ``input_modalities``;
* OpenRouter (and gateways copying it): ``context_length``,
  ``architecture.input_modalities``;
* Anthropic: ``max_input_tokens``, ``capabilities.image_input.supported``;
* vLLM and servers built on it: ``max_model_len``.

A provider that returns bare ids (OpenAI itself) records nothing, and callers
fall back to their tables. Facts are kept per provider, because a gateway and
the vendor can state different limits for one id.

Kept free of heavy imports: ``context_budget`` and ``vision`` read it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelFacts:
    context_window: int | None = None
    sees_images: bool | None = None


_FACTS: dict[tuple[str, str], ModelFacts] = {}


def _as_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)  # openai / anthropic SDK objects keep unknown fields as extras
    if callable(dump):
        value = dump()
        if isinstance(value, dict):
            return value
    return {}


def _positive_int(value: Any) -> int | None:
    # bool is an int subclass, and a JSON true is not a window size.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 else None


def _window(entry: dict[str, Any]) -> int | None:
    top = entry.get("top_provider")
    nested = top.get("context_length") if isinstance(top, dict) else None
    for value in (entry.get("max_input_tokens"), entry.get("context_window"), entry.get("context_length"),
                  entry.get("max_model_len"), nested):
        window = _positive_int(value)
        if window is not None:
            return window
    return None


def _sees_images(entry: dict[str, Any]) -> bool | None:
    capabilities = entry.get("capabilities")
    if isinstance(capabilities, dict) and isinstance(capabilities.get("image_input"), dict):
        supported = capabilities["image_input"].get("supported")
        if isinstance(supported, bool):
            return supported
    architecture = entry.get("architecture")
    for modalities in (entry.get("input_modalities"),
                       architecture.get("input_modalities") if isinstance(architecture, dict) else None):
        if isinstance(modalities, list):
            return "image" in modalities
    return None


def parse_entry(item: Any) -> tuple[str, ModelFacts] | None:
    """(id, facts) for one catalog entry, or None when it has no id."""
    entry = _as_dict(item)
    model_id = str(entry.get("id") or getattr(item, "id", "") or "").strip()
    if not model_id:
        return None
    return model_id, ModelFacts(context_window=_window(entry), sees_images=_sees_images(entry))


def remember(provider: str, items: Any) -> None:
    """Record the facts of every entry in one catalog page that states any."""
    for item in items or ():
        parsed = parse_entry(item)
        if parsed is None:
            continue
        model_id, facts = parsed
        if facts != ModelFacts():
            _FACTS[(provider, model_id)] = facts


def facts_for(provider: str, model: str) -> ModelFacts | None:
    return _FACTS.get((provider, (model or "").strip()))


def forget_all() -> None:
    """Drop every recorded fact (tests, provider switch)."""
    _FACTS.clear()
