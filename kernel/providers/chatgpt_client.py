# -*- coding: utf-8 -*-
"""Adapt Kvist's completion interface to ChatGPT plan Responses requests."""
from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

import httpx
from openai import AuthenticationError, OpenAI

from kernel.providers import chatgpt_auth as auth


def _input(messages: list[dict]) -> tuple[list[dict], str]:
    items, instructions = [], []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content") or ""
        if role in {"system", "developer"}:
            instructions.append(str(content))
            continue
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": content})
            continue
        if content:
            if isinstance(content, list):
                content = [_content_part(part, role) for part in content]
            items.append({"role": role, "content": content})
        for call in message.get("tool_calls", []):
            items.append({"type": "function_call", "call_id": call["id"], "namespace": "kvist",
                          "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    return items, "\n\n".join(instructions)


def _content_part(part: dict, role: str) -> dict:
    if part.get("type") == "text":
        return {"type": "output_text" if role == "assistant" else "input_text", "text": part["text"]}
    if part.get("type") == "image_url":
        image = part["image_url"]
        return {"type": "input_image", "image_url": image["url"], "detail": image.get("detail", "auto")}
    raise ValueError("ChatGPT does not support this message content type")


def _request(kwargs: dict) -> dict:
    items, instructions = _input(kwargs["messages"])
    request = {"model": kwargs["model"], "input": items, "store": False, "stream": True}
    if instructions:
        request["instructions"] = instructions
    if kwargs.get("timeout") is not None:
        request["timeout"] = kwargs["timeout"]
    if kwargs.get("reasoning_effort"):
        request["reasoning"] = {"effort": kwargs["reasoning_effort"]}
    if kwargs.get("response_format"):
        request["text"] = {"format": kwargs["response_format"]}
    if kwargs.get("tools"):
        request["tools"] = [{"type": "namespace", "name": "kvist", "description": "Kvist workspace tools",
                             # Responses API defaults to strict, which makes the model fill every property of a flat schema.
                             "tools": [{"type": "function", "strict": False, **tool["function"]} for tool in kwargs["tools"]]}]
        choice = kwargs.get("tool_choice")
        if isinstance(choice, str):
            request["tool_choice"] = choice
        elif isinstance(choice, dict):
            request["tool_choice"] = {"type": "function", "namespace": "kvist", "name": choice["function"]["name"]}
    return request


def _usage(response: Any) -> NS | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return NS(prompt_tokens=usage.input_tokens, completion_tokens=usage.output_tokens,
              total_tokens=usage.total_tokens)


def _chunk(content: str = "", calls: list | None = None, usage: Any = None, reasoning: str = "") -> NS:
    return NS(choices=[NS(delta=NS(content=content, tool_calls=calls or [], reasoning_content=reasoning),
                          finish_reason=None)], usage=usage)


def _tool_call(item: Any, index: int) -> NS:
    if getattr(item, "namespace", None) not in (None, "kvist"):
        raise RuntimeError("ChatGPT returned an unexpected tool namespace")
    return NS(index=index, id=item.call_id, type="function",
              function=NS(name=item.name, arguments=item.arguments))


def _response_error(event: Any) -> RuntimeError:
    response = getattr(event, "response", None)
    error = getattr(response, "error", None) or getattr(event, "error", None)
    code = getattr(error, "code", None) or getattr(event, "code", None) or event.type
    if code in {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable"}:
        return RuntimeError("ChatGPT plan usage is unavailable or its limit was reached. "
                            "Check https://chatgpt.com/settings/usage / Проверьте лимиты подписки ChatGPT.")
    # Server error text can echo inputs; expose only the structured code.
    return RuntimeError(f"ChatGPT response failed ({code}) / Ответ ChatGPT не завершён.")


class ChatGPTClient:
    def __init__(self):
        self.account_id = auth.account_status()["active"]
        if not self.account_id or not auth.has_credentials():
            raise auth.ChatGPTAuthError("Continue with ChatGPT first / Сначала войдите через ChatGPT.")
        self.chat = NS(completions=self)

    def create(self, **kwargs):
        request = _request(kwargs)
        stream = self._stream(request)
        if kwargs.get("stream"):
            return stream
        text, reasoning, calls, usage = [], [], [], None
        for chunk in stream:
            delta = chunk.choices[0].delta
            text.append(delta.content)
            reasoning.append(delta.reasoning_content)
            calls.extend(delta.tool_calls)
            usage = chunk.usage or usage
        return NS(choices=[NS(message=NS(content="".join(text), reasoning_content="".join(reasoning),
                                         tool_calls=calls), finish_reason="tool_calls" if calls else "stop")], usage=usage)

    def _stream(self, request: dict):
        for attempt in range(2):
            token = auth.access_token(self.account_id, force_refresh=bool(attempt))
            try:
                yield from self._stream_request(request, token)
                return
            except AuthenticationError:
                if attempt:
                    raise auth.ChatGPTAuthError("ChatGPT access was revoked. Sign in again / Войдите в ChatGPT снова.") from None

    def _stream_request(self, request: dict, token: str):
        completed, call_index = False, 0
        with OpenAI(api_key=token, base_url=auth.RESOURCE, max_retries=0,
                    timeout=httpx.Timeout(60, connect=10)) as client:
            with client.responses.create(**request) as stream:
                for event in stream:
                    if event.type == "response.output_text.delta":
                        yield _chunk(content=event.delta)
                    elif event.type == "response.reasoning_summary_text.delta":
                        yield _chunk(reasoning=event.delta)
                    elif event.type == "response.output_item.done" and event.item.type == "function_call":
                        yield _chunk(calls=[_tool_call(event.item, call_index)])
                        call_index += 1
                    elif event.type in {"response.failed", "response.incomplete", "error"}:
                        raise _response_error(event)
                    elif event.type == "response.completed":
                        completed = True
                        yield _chunk(usage=_usage(event.response))
        if not completed:
            raise RuntimeError("ChatGPT stream interrupted before completion / Поток ChatGPT прерван до завершения.")


def fetch_models() -> list[str]:
    try:
        response = httpx.get(auth.RESOURCE + "/models", headers={"Authorization": "Bearer " + auth.access_token()}, timeout=20)
        response.raise_for_status()
        value = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("Could not load ChatGPT models / Не удалось загрузить модели ChatGPT.") from error
    if not isinstance(value, dict):
        raise RuntimeError("Invalid ChatGPT model catalog / Некорректный каталог моделей ChatGPT.")
    models = value.get("models", [])
    if not isinstance(models, list):
        raise RuntimeError("Invalid ChatGPT model catalog / Некорректный каталог моделей ChatGPT.")
    return [item["slug"] for item in models if isinstance(item, dict) and item.get("visibility") == "list"
            and isinstance(item.get("slug"), str) and item["slug"]]
