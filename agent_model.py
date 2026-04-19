# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from hashlib import sha256
from pathlib import Path

from openai.types.chat import ChatCompletionMessageParam

try:
    import tiktoken
except ImportError:
    tiktoken = None  # type: ignore[assignment]

_PROMPT_DIR = Path(__file__).resolve().parent
SYSTEM_PROMPT = (_PROMPT_DIR / "system_prompt.txt").read_text(encoding="utf-8").strip()


def call_action_model(self, prompt: str) -> str:
    preferred_model = (
        self.config.smart_model if self._prefer_smart_model() else self.config.fast_model
    )
    return self._call_model(
        model=preferred_model,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=prompt,
    )


def call_model(self, model: str, system_prompt: str, user_prompt: str) -> str:
    trimmed_prompt = self._trim_prompt_to_token_limit(system_prompt, user_prompt)
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": trimmed_prompt},
    ]

    models_to_try = [model]
    if model != self.config.fast_model:
        models_to_try.append(self.config.fast_model)

    last_error: Exception | None = None
    for current_model in models_to_try:
        cache_key = sha256(
            json.dumps(
                {
                    "model": current_model,
                    "messages": messages,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        if self.config.cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]

        for attempt in range(1, self.config.max_retries + 1):
            try:
                if self.config.stream:
                    content = self._call_model_streaming(current_model, messages)
                else:
                    response = self.client.chat.completions.create(
                        model=current_model,
                        messages=messages,
                        temperature=self.config.model_temperature,
                    )
                    content = response.choices[0].message.content
                if not content:
                    raise RuntimeError("Модель вернула пустой ответ")
                if self.config.cache_enabled:
                    self._cache[cache_key] = content
                return content
            except Exception as error:
                last_error = error
                if attempt < self.config.max_retries:
                    delay = self.config.retry_base_delay * (2 ** (attempt - 1))
                    self._ui.debug_print(
                        f"Ошибка API ({current_model}, попытка {attempt}/{self.config.max_retries}): "
                        f"{error}. Повтор через {delay:.1f}с"
                    )
                    time.sleep(delay)

    raise RuntimeError(f"Не удалось получить ответ модели: {last_error}")


def call_model_streaming(
    self,
    model: str,
    messages: list[ChatCompletionMessageParam],
) -> str:
    stream = self.client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=self.config.model_temperature,
        stream=True,
    )
    chunks: list[str] = []
    thought_shown = False
    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            chunks.append(delta.content)
            if self.config.verbose and not thought_shown:
                partial = "".join(chunks)
                if '"thought"' in partial and len(partial) > 80:
                    thought_shown = True
                    print(f"  [streaming] {partial[:120]}...", flush=True)
    return "".join(chunks)


def init_tiktoken_encoding():
    if tiktoken is None:
        return None
    try:
        return tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None


def count_tokens(self, text: str) -> int:
    if self._encoding is None:
        return len(text) // 4
    return len(self._encoding.encode(text, disallowed_special=()))


def trim_prompt_to_token_limit(self, system_prompt: str, user_prompt: str) -> str:
    system_tokens = self._count_tokens(system_prompt)
    available = self.config.max_context_tokens - system_tokens - 500
    if available <= 0:
        available = 4000

    prompt_tokens = self._count_tokens(user_prompt)
    if prompt_tokens <= available:
        return user_prompt

    ratio = available / prompt_tokens
    target_chars = int(len(user_prompt) * ratio * 0.95)
    trimmed = user_prompt[:target_chars]
    trimmed += "\n\n[КОНТЕКСТ ОБРЕЗАН: превышен лимит токенов. Действуй на основе доступных данных.]"
    self._ui.debug_print(
        f"Промпт обрезан: {prompt_tokens} токенов → ~{available} (лимит {self.config.max_context_tokens})"
    )
    return trimmed


def prefer_smart_model(self) -> bool:
    if not self._state.plan_info:
        return True
    if self._state.plan_info.complexity == "complex":
        return True
    if self._state.consecutive_errors > 0:
        return True
    if self._state.parse_errors > 0:
        return True
    if self._state.stagnation_count > 0:
        return True
    return False