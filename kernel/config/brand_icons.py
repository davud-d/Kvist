# -*- coding: utf-8 -*-
"""Which brand a provider or a model belongs to, as a short stable slug.

A frontend draws a logo next to each provider and model in its lists. The core
knows the providers and the model ids, so it names the brand (``anthropic``,
``openai``, ...) and each frontend maps that slug to its own artwork; one with
no artwork for a slug draws a monogram instead. An empty string means "no
known brand" (a custom provider, a model nobody recognises).
"""
from __future__ import annotations

import re

# Provider id -> brand slug. A gateway (OpenRouter, OmniRoute, ...) is its own
# brand: the models behind it get theirs from ``model_icon``.
_PROVIDER_BRANDS: dict[str, str] = {
    "anymodel": "anymodel",
    "openai": "openai",
    "chatgpt": "openai",
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "openrouter": "openrouter",
    "omniroute": "omniroute",
    "freeflow": "freeflow",
    "qwen": "qwen",
    "moonshot": "moonshot",
    "kimi": "moonshot",
    "zhipu": "zhipu",
    "zai": "zai",
    "xiaomi": "xiaomi",
    "xiaomi-tokenplan": "xiaomi",
    "minimax": "minimax",
    "lingyiwanwu": "01ai",
    "stepfun": "stepfun",
    "siliconflow": "siliconflow",
    "baichuan": "baichuan",
    "qianfan": "baidu",
    "hunyuan": "tencent",
    "modelscope": "modelscope",
    "mistral": "mistral",
    "groq": "groq",
    "together": "together",
    "fireworks": "fireworks",
    "xai": "xai",
    "perplexity": "perplexity",
    "cerebras": "cerebras",
    "deepinfra": "deepinfra",
    "novita": "novita",
    "vercel": "vercel",
    "baseten": "baseten",
    "sambanova": "sambanova",
    "nebius": "nebius",
    "friendli": "friendli",
    "databricks": "databricks",
    "gemini": "google",
    "cohere": "cohere",
    "ai21": "ai21",
    "doubao": "bytedance",
    "github-models": "github",
    "iflytek-spark": "iflytek",
    "scaleway": "scaleway",
}

# First match wins, so the specific patterns come before the general ones.
_MODEL_BRANDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), brand)
    for pattern, brand in (
        (r"claude|fable|opus|sonnet|haiku", "anthropic"),
        (r"gpt|chatgpt|codex|(?<![a-z])o[134](?![a-z])", "openai"),
        (r"gemini|gemma", "google"),
        (r"deepseek", "deepseek"),
        (r"qwen|qwq", "qwen"),
        (r"llama", "meta"),
        (r"mistral|mixtral|codestral|ministral|magistral", "mistral"),
        (r"grok", "xai"),
        (r"(?:^|/)k3(?:[.-]|$)|kimi|moonshot", "moonshot"),
        (r"glm|zhipu", "zhipu"),
        (r"mimo", "xiaomi"),
        (r"minimax", "minimax"),
        (r"command-?r|cohere", "cohere"),
        (r"jamba", "ai21"),
        (r"doubao", "bytedance"),
        (r"ernie", "baidu"),
        (r"hunyuan", "tencent"),
        (r"step-", "stepfun"),
        (r"sonar|perplexity", "perplexity"),
        (r"nemotron", "nvidia"),
    )
)


def provider_icon(provider_id: str) -> str:
    """Brand slug of a provider; empty for a custom or unknown one."""
    return _PROVIDER_BRANDS.get(str(provider_id or "").strip().lower(), "")


def model_icon(model_id: str) -> str:
    """Brand slug of the model's maker, read from its id ("cc/claude-opus-5" is Anthropic's)."""
    text = str(model_id or "").strip().lower()
    for pattern, brand in _MODEL_BRANDS:
        if pattern.search(text):
            return brand
    return ""
