# -*- coding: utf-8 -*-
"""Per-step cost calculation for the AnyModel provider.

AnyModel's ``/v1/models`` catalog carries a ``billing.coefficient`` field per
model (in addition to the plain ``id`` every OpenAI-compatible catalog has).
This is the only price source this codebase has today — there is no local
``/app/model-catalog`` with a coefficient already baked in — so the formula
here is not combined with any other pricing table.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from typing import Any

from kernel.sandbox.redaction import redact_text

ANYMODEL_MODELS_URL = "https://anymodel.org/v1/models"

_REQUEST_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # generous for a model catalog response

# Coefficients rarely change mid-run; re-fetching on every step would add a
# round-trip to a loop that can be tens of steps long, so the catalog is kept
# in memory for a while (10-15 minute window from the task spec).
_COEFFICIENT_CACHE_TTL_SECONDS = 720.0

_COEFFICIENT_CACHE: dict[str, tuple[float, dict[str, float]]] = {}


class AnyModelPricingError(RuntimeError):
    """Base class for AnyModel pricing-client failures."""


class AnyModelAuthError(AnyModelPricingError):
    """The API key was rejected (HTTP 401)."""


class AnyModelUnavailableError(AnyModelPricingError):
    """The endpoint could not be reached (timeout, DNS, non-auth HTTP error)."""


def clear_anymodel_pricing_cache() -> None:
    """Drop cached coefficient lookups (tests, key rotation)."""
    _COEFFICIENT_CACHE.clear()


def _cache_key(api_key: str, base_url: str) -> str:
    # Same fingerprint style as llm_clients' discovery cache: the key's
    # identity, never its raw value, ends up as a dict key.
    return hashlib.sha256(f"{base_url}\n{api_key}".encode("utf-8")).hexdigest()


class _NoCatalogRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Authorization must never be forwarded to a redirect destination.
        raise urllib.error.HTTPError(req.full_url, code, "Catalog redirect refused", headers, fp)


_catalog_opener = urllib.request.build_opener(_NoCatalogRedirect())


def _fetch_raw_catalog(api_key: str, base_url: str) -> Any:
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise AnyModelUnavailableError("AnyModel catalog requires an HTTPS URL without credentials")
    request = urllib.request.Request(
        url=base_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "kvist-local-agent/1.0",
        },
        method="GET",
    )
    try:
        with _catalog_opener.open(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 401:
            raise AnyModelAuthError("AnyModel rejected the API key (HTTP 401)") from None
        raise AnyModelUnavailableError(
            redact_text(f"AnyModel returned HTTP {error.code} for {base_url}")
        ) from None
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        raise AnyModelUnavailableError(redact_text(f"AnyModel request failed: {reason}")) from None
    except TimeoutError:
        raise AnyModelUnavailableError("AnyModel request timed out") from None

    if len(raw) > _MAX_RESPONSE_BYTES:
        raise AnyModelUnavailableError("AnyModel response exceeds the allowed size")

    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnyModelUnavailableError("AnyModel response was not valid JSON") from error


def _parse_coefficients(payload: Any) -> dict[str, float]:
    coefficients: dict[str, float] = {}
    if not isinstance(payload, dict):
        return coefficients
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        billing = item.get("billing")
        if not isinstance(billing, dict):
            continue
        coefficient = billing.get("coefficient")
        if isinstance(coefficient, (int, float)) and not isinstance(coefficient, bool):
            coefficients[model_id] = float(coefficient)
    return coefficients


def fetch_model_coefficients(
    api_key: str,
    *,
    base_url: str = ANYMODEL_MODELS_URL,
    force_refresh: bool = False,
) -> dict[str, float]:
    """Return ``{model_id: billing.coefficient}`` for AnyModel's catalog.

    Cached in memory for ``_COEFFICIENT_CACHE_TTL_SECONDS`` so a run with
    dozens of steps does not add a round-trip on every step. A failed fetch
    is never cached, so the next call retries immediately.
    """
    key = _cache_key(api_key, base_url)
    now = time.monotonic()
    if not force_refresh:
        cached = _COEFFICIENT_CACHE.get(key)
        if cached is not None and now - cached[0] < _COEFFICIENT_CACHE_TTL_SECONDS:
            return dict(cached[1])

    payload = _fetch_raw_catalog(api_key, base_url)
    coefficients = _parse_coefficients(payload)
    _COEFFICIENT_CACHE[key] = (now, coefficients)
    return dict(coefficients)


def get_model_coefficient(
    api_key: str,
    model_id: str,
    *,
    base_url: str = ANYMODEL_MODELS_URL,
    force_refresh: bool = False,
) -> float | None:
    """Return the billing coefficient for one model id, or ``None`` if unknown."""
    coefficients = fetch_model_coefficients(api_key, base_url=base_url, force_refresh=force_refresh)
    return coefficients.get(model_id)


def compute_step_cost(input_tokens: int, output_tokens: int, coefficient: float) -> float:
    """Cost of one step: ``(input_tokens + output_tokens) * coefficient``.

    Pure arithmetic, no network — callers fetch the coefficient once (cached
    by :func:`fetch_model_coefficients`) and reuse it across a run's steps.
    """
    return (input_tokens + output_tokens) * coefficient
