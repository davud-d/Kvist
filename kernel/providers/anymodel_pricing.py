# -*- coding: utf-8 -*-
"""Per-step cost calculation for the AnyModel provider.

AnyModel's ``/v1/models`` catalog carries a ``billing.coefficient`` field per
model (in addition to the plain ``id`` every OpenAI-compatible catalog has).
This is the only price source this codebase has today — there is no local
``/app/model-catalog`` with a coefficient already baked in — so the formula
here is not combined with any other pricing table.

The field comes in two shapes: a single number (one multiplier for every token)
and ``{"input": n, "output": m}`` (the catalog's current shape). Both parse to
:class:`ModelCoefficient`, so a caller never branches on the wire format.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
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

_COEFFICIENT_CACHE: dict[str, tuple[float, dict[str, ModelCoefficient]]] = {}


@dataclass(frozen=True, slots=True)
class ModelCoefficient:
    """What one input token and one output token of a model cost, in AnyModel's units."""

    input: float
    output: float


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


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_coefficient(raw: object) -> ModelCoefficient | None:
    if _is_number(raw):
        return ModelCoefficient(float(raw), float(raw))
    if isinstance(raw, dict) and _is_number(raw.get("input")) and _is_number(raw.get("output")):
        return ModelCoefficient(float(raw["input"]), float(raw["output"]))
    return None


def _parse_coefficients(payload: Any) -> dict[str, ModelCoefficient]:
    coefficients: dict[str, ModelCoefficient] = {}
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
        coefficient = _parse_coefficient(billing.get("coefficient"))
        if coefficient is not None:
            coefficients[model_id] = coefficient
    return coefficients


def fetch_model_coefficients(
    api_key: str,
    *,
    base_url: str = ANYMODEL_MODELS_URL,
    force_refresh: bool = False,
) -> dict[str, ModelCoefficient]:
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
) -> ModelCoefficient | None:
    """Return the billing coefficient for one model id, or ``None`` if unknown."""
    coefficients = fetch_model_coefficients(api_key, base_url=base_url, force_refresh=force_refresh)
    return coefficients.get(model_id)


def compute_step_cost(input_tokens: int, output_tokens: int, coefficient: ModelCoefficient) -> float:
    """Cost of one step: input and output tokens, each times its own coefficient.

    Pure arithmetic, no network — callers fetch the coefficient once (cached
    by :func:`fetch_model_coefficients`) and reuse it across a run's steps.
    """
    return input_tokens * coefficient.input + output_tokens * coefficient.output


def format_coefficient(coefficient: ModelCoefficient) -> str:
    """The multiplier as a model picker shows it next to the name: ``x0``, ``x1.5``, ``x1/3``.

    One number when input and output cost the same, ``input/output`` when they differ.
    """
    if coefficient.input == coefficient.output:
        return f"x{coefficient.input:g}"
    return f"x{coefficient.input:g}/{coefficient.output:g}"


def coefficient_labels(api_key: str) -> dict[str, str]:
    """``{model_id: "x1.5"}`` for a model picker; empty when the catalog cannot be read.

    A picker gets its model list from its own request, which reports a bad key or
    an outage itself. A pricing failure here must not blank that list, it only
    leaves the names without their multiplier.
    """
    try:
        coefficients = fetch_model_coefficients(api_key)
    except AnyModelPricingError:
        return {}
    return {model_id: format_coefficient(item) for model_id, item in coefficients.items()}
