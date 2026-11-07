# -*- coding: utf-8 -*-
"""The customer's model key: one cheap check request, encrypted storage, and Kvist's environment.

Check results are codes the frontend turns into plain words next to the field:
``invalid_key``, ``no_funds``, ``rate_limited``, ``provider_down``, ``bad_endpoint``,
``model_not_found``.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, Request
from kernel.llm.provider_registry import ANYMODEL_PROVIDER, MANUAL_OPENAI_PROVIDERS
from pydantic import Field
from sqlalchemy.orm import Session

from .auth import current_user, http_client
from .config import Settings
from .db import get_db
from .errors import ApiError
from .funnel import record_step
from .models import User
from .outbound import PublicEndpointTransport
from .secrets import Vault, last4, remember
from .validation import InputModel


@dataclass(frozen=True, slots=True)
class ModelProvider:
    id: str
    label: str
    keys_page: str
    kvist_provider: str
    key_env: str
    # Where the customer's requests go when the provider has one fixed address.
    default_base_url: str = ""
    # Only for providers without a fixed address (the customer's own endpoint).
    needs_base_url: bool = False
    base_url_env: str = ""
    # Vendors that change model ids too often for Kvist to guess: the customer types one.
    needs_model: bool = False
    partner: bool = False


def _registry_provider(provider_id: str, config: dict[str, str]) -> ModelProvider:
    """A third-party OpenAI-compatible provider, as the core describes it."""
    return ModelProvider(
        provider_id,
        config["label"],
        "",
        provider_id,
        config["api_key_key"].upper(),
        default_base_url=config["default_base_url"],
        needs_base_url=not config["default_base_url"],
        base_url_env=config["base_url_key"].upper(),
        needs_model=True,
        partner=provider_id == ANYMODEL_PROVIDER,
    )


# The partner first, then the three vendors with their own balance check, then the rest of the core's
# list, then the customer's own endpoint.
PROVIDERS: dict[str, ModelProvider] = {
    ANYMODEL_PROVIDER: _registry_provider(ANYMODEL_PROVIDER, MANUAL_OPENAI_PROVIDERS[ANYMODEL_PROVIDER]),
    "deepseek": ModelProvider(
        "deepseek", "DeepSeek", "https://platform.deepseek.com/api_keys", "deepseek", "DEEPSEEK_API_KEY"
    ),
    "openai": ModelProvider("openai", "OpenAI", "https://platform.openai.com/api-keys", "openai", "OPENAI_API_KEY"),
    "anthropic": ModelProvider(
        "anthropic", "Anthropic", "https://console.anthropic.com/settings/keys", "anthropic", "ANTHROPIC_API_KEY"
    ),
    **{
        provider_id: _registry_provider(provider_id, config)
        for provider_id, config in MANUAL_OPENAI_PROVIDERS.items()
        if provider_id != ANYMODEL_PROVIDER
    },
    # Any OpenAI-compatible endpoint, run through Kvist's omniroute provider.
    "other": ModelProvider(
        "other", "Other", "", "omniroute", "OMNIROUTE_API_KEY",
        needs_base_url=True, base_url_env="OMNIROUTE_BASE_URL", needs_model=True,
    ),
}

router = APIRouter()


@dataclass(frozen=True, slots=True)
class KeyCheck:
    ok: bool
    code: str = ""


class KeyIn(InputModel):
    provider: str
    key: str = Field(min_length=8, max_length=500)
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)


def endpoint_problem(base_url: str, allow_local: bool) -> str:
    """Empty when the endpoint may be called; otherwise why not."""
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return "bad_endpoint"
    if parsed.username or parsed.password or parsed.fragment or port == 0:
        return "bad_endpoint"
    if not host:
        return "bad_endpoint"
    if allow_local:
        return "" if parsed.scheme in {"http", "https"} else "bad_endpoint"
    if parsed.scheme != "https" or host == "localhost" or host.endswith(".local"):
        return "bad_endpoint"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return ""
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        return "bad_endpoint"
    return ""


def _error_text(response: httpx.Response) -> str:
    try:
        return response.text.lower()
    except UnicodeDecodeError:
        return ""


def _classify(response: httpx.Response) -> KeyCheck:
    status = response.status_code
    body = _error_text(response)
    if status in (401, 403):
        return KeyCheck(False, "invalid_key")
    if status == 402 or "insufficient_quota" in body or "credit balance" in body or "insufficient balance" in body:
        return KeyCheck(False, "no_funds")
    if status == 429:
        return KeyCheck(False, "rate_limited")
    if status >= 500:
        return KeyCheck(False, "provider_down")
    if status != 200:
        return KeyCheck(False, "bad_endpoint")
    return KeyCheck(True)


async def check_key(
    client: httpx.AsyncClient, provider: str, key: str, base_url: str = "", model: str = ""
) -> KeyCheck:
    """One request that costs nothing: a balance or a model list, never a completion."""
    try:
        if provider == "deepseek":
            response = await client.get(
                "https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {key}"}
            )
            result = _classify(response)
            if result.ok and response.json().get("is_available") is False:
                return KeyCheck(False, "no_funds")
            return result
        if provider == "openai":
            return _classify(
                await client.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
            )
        if provider == "anthropic":
            return _classify(
                await client.get(
                    "https://api.anthropic.com/v1/models",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                )
            )
        if base_url:
            response = await client.get(f"{base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"})
            if response.status_code in (404, 405) and provider != "other":
                # A vendor with no model list: nothing cheap to ask, and a wrong key is caught by its first task.
                return KeyCheck(True)
            result = _classify(response)
            if not result.ok:
                return result
            # Some vendors list ids as "models/<id>".
            served = {str(item.get("id")).removeprefix("models/")
                      for item in response.json().get("data", []) if isinstance(item, dict)}
            if served and model.removeprefix("models/") not in served:
                return KeyCheck(False, "model_not_found")
            return result
    except httpx.HTTPError:
        return KeyCheck(False, "provider_down")
    except ValueError:
        # A 200 that is not JSON: whatever answered is not the API we expected.
        return KeyCheck(False, "bad_endpoint")
    return KeyCheck(False, "unknown_provider")


def kvist_environment(user: User, key: str) -> dict[str, str]:
    """What Kvist needs in its environment to talk to the customer's model."""
    provider = PROVIDERS[user.model_provider]
    env = {"AGENT_LLM_PROVIDER": provider.kvist_provider, provider.key_env: key}
    if provider.needs_base_url:
        env[provider.base_url_env] = user.model_base_url
    if provider.needs_model:
        env["AGENT_FAST_MODEL"] = user.model_name
        env["AGENT_SMART_MODEL"] = user.model_name
    return env


def key_summary(user: User) -> dict[str, object]:
    return {
        "provider": user.model_provider,
        "last4": user.model_key_last4,
        "base_url": user.model_base_url,
        "model": user.model_name,
        "set": bool(user.model_key_enc),
    }


@router.get("/api/model-providers")
def list_providers() -> list[dict[str, object]]:
    return [
        {"id": p.id, "label": p.label, "keys_page": p.keys_page, "needs_base_url": p.needs_base_url,
         "needs_model": p.needs_model, "partner": p.partner}
        for p in PROVIDERS.values()
    ]


@router.get("/api/model-key")
def get_key(user: User = Depends(current_user)) -> dict[str, object]:
    return key_summary(user)


@router.post("/api/model-key")
async def save_key(
    payload: KeyIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    key = payload.key.strip()
    remember(key)
    provider = PROVIDERS.get(payload.provider)
    if provider is None:
        raise ApiError(400, "unknown_provider", "Pick a provider. / Выбери провайдера.")
    # Only what this provider asks for is kept: a stale address or model from another choice is dropped.
    base_url = payload.base_url.strip() if provider.needs_base_url else ""
    model = payload.model.strip() if provider.needs_model else ""
    if provider.needs_base_url:
        problem = endpoint_problem(base_url, settings.allow_local_endpoints)
        if problem:
            return {"ok": False, "code": problem}
    if provider.needs_model and not model:
        return {"ok": False, "code": "model_missing"}
    client = http_client(request)
    if provider.needs_base_url and not settings.allow_local_endpoints and request.app.state.http_transport is None:
        await client.aclose()
        client = httpx.AsyncClient(transport=PublicEndpointTransport(), trust_env=False, timeout=10.0)
    async with client:
        result = await check_key(client, payload.provider, key, base_url or provider.default_base_url, model)
    if not result.ok:
        return {"ok": False, "code": result.code}
    vault: Vault = request.app.state.vault
    user.model_provider = payload.provider
    user.model_key_enc = vault.encrypt(key)
    user.model_key_last4 = last4(key)
    user.model_base_url = base_url
    user.model_name = model
    record_step(db, "key_valid", user_id=user.id)
    db.commit()
    return {"ok": True, "code": "", "key": key_summary(user)}


@router.delete("/api/model-key")
def delete_key(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    user.model_provider = ""
    user.model_key_enc = ""
    user.model_key_last4 = ""
    user.model_base_url = ""
    user.model_name = ""
    db.commit()
    return key_summary(user)
