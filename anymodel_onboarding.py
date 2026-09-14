# -*- coding: utf-8 -*-
"""First-run onboarding for the AnyModel default provider.

``try_oauth_register`` is a placeholder for a possible future "sign in with
AnyModel" exchange. AnyModel has not published a confirmed token-exchange
endpoint yet (no ``POST /v1/exchange`` or equivalent), so this never makes a
network call: it only decides, behind a feature flag, whether to attempt an
OAuth-like path before the caller falls through to the manual key-entry flow.
Flipping the flag on later needs no change to callers — the check lives here,
once.
"""

from __future__ import annotations

import os

_OAUTH_FLAG_ENV = "AGENT_ANYMODEL_OAUTH"


def _oauth_flag_enabled() -> bool:
    return os.getenv(_OAUTH_FLAG_ENV, "").strip() == "1"


def try_oauth_register() -> str | None:
    """Attempt an OAuth-like AnyModel registration; a no-op today either way.

    Returns the resolved provider slug on success, or ``None`` to tell the
    caller to fall back to the manual registration screen. With the flag off
    (the default) this returns ``None`` immediately. With the flag on, it
    still returns ``None`` and makes no network request — the real exchange
    is intentionally not implemented until AnyModel confirms an endpoint.
    """
    if not _oauth_flag_enabled():
        return None
    return None
