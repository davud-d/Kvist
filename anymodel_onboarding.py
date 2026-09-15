# -*- coding: utf-8 -*-
"""First-run onboarding for the AnyModel default provider.

``try_oauth_register`` is a placeholder for a possible future "sign in with
AnyModel" exchange. AnyModel has not published a confirmed token-exchange
endpoint yet (no ``POST /v1/exchange`` or equivalent), so this never makes a
network call: it only decides, behind a feature flag, whether to attempt an
OAuth-like path before the caller falls through to the manual key-entry flow.
Flipping the flag on later needs no change to callers — the check lives here,
once.

``offer_anymodel_registration`` is the real, shipped screen: no key yet ->
offer to open the registration page -> paste the key -> validate it
immediately against AnyModel's own ``/v1/models`` (rejecting a bad key before
the agent ever starts) -> save it.
"""

from __future__ import annotations

import os
import sys
import webbrowser

from providers.anymodel_pricing import (
    AnyModelAuthError,
    AnyModelUnavailableError,
    fetch_model_coefficients,
)
from ui import AgentUI
from user_config import set_provider_secret

ANYMODEL_REGISTER_URL = "https://anymodel.org/register"
ANYMODEL_API_KEY_KEY = "anymodel_api_key"

_OAUTH_FLAG_ENV = "AGENT_ANYMODEL_OAUTH"


def _oauth_flag_enabled() -> bool:
    return os.getenv(_OAUTH_FLAG_ENV, "").strip() == "1"


def _gui_browser_available() -> bool:
    """Is there a graphical session ``webbrowser`` could actually pop a window in?

    Without one, ``webbrowser`` on Linux still finds a *console* browser
    (``www-browser``/``links``/``elinks``/``lynx``/``w3m``) if one happens to
    be on PATH, and opens it via a blocking subprocess call — the process
    takes over this same terminal until the user quits it by hand, which in
    a headless container (no browser installed, no DISPLAY) reads as the
    whole flow hanging on Enter. Skipping the open entirely when there is no
    GUI session to open a window in avoids that trap.
    """
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


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


def format_hyperlink(text: str, url: str) -> str:
    """OSC 8 terminal hyperlink wrapping ``text``.

    Support is inconsistent (kitty/iTerm2/foot/wezterm render it as a click
    target; gnome-terminal and Windows cmd print the raw escape as garbage),
    so callers always print the plain URL as a second, non-hyperlinked line
    right under it.
    """
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def _print_registration_screen() -> None:
    # Written directly to stdout, not through Rich/sanitize_display_text:
    # the sanitizer strips terminal control sequences on purpose (it exists
    # to neutralize *hostile* escapes coming from untrusted tool output), and
    # an OSC 8 hyperlink is exactly the kind of sequence it would strip. This
    # one is ours, not untrusted content, so it is written raw.
    lines = [
        "Нет ключа AnyModel. Регистрация займёт минуту.",
        "",
        "  Enter — открыть регистрацию в браузере (email + пароль)",
        "  Esc   — выбрать другого провайдера",
        "",
        format_hyperlink(ANYMODEL_REGISTER_URL, ANYMODEL_REGISTER_URL),
        ANYMODEL_REGISTER_URL,
        "",
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def _open_registration_browser() -> None:
    if not _gui_browser_available():
        print("Графический браузер не найден — открой ссылку выше вручную.")
        return
    try:
        webbrowser.open(ANYMODEL_REGISTER_URL)
    except webbrowser.Error:
        print("Не удалось открыть браузер автоматически — открой ссылку выше вручную.")


def offer_anymodel_registration(ui: AgentUI) -> str | None:
    """First-run "no AnyModel key yet" screen.

    Returns ``"anymodel"`` once a working key has been entered and saved, or
    ``None`` if the user backed out (Esc, or an empty key entry) — the caller
    is expected to fall back to the generic provider picker in that case.
    """
    if not sys.stdin.isatty():
        return None

    oauth_result = try_oauth_register()
    if oauth_result:
        return oauth_result

    _print_registration_screen()

    selected = ui.choose_option(
        title="AnyModel",
        options=[
            {
                "value": "register",
                "label": "Открыть регистрацию в браузере",
                "description": ANYMODEL_REGISTER_URL,
            }
        ],
        help_text="Enter — открыть регистрацию, Esc — выбрать другого провайдера",
    )
    if selected != "register":
        return None

    _open_registration_browser()

    while True:
        api_key = ui.prompt_secret(
            title="Вставь API-ключ после регистрации:",
            help_text="Paste or type the value, then press Enter.",
        )
        if not api_key:
            return None

        try:
            fetch_model_coefficients(api_key, force_refresh=True)
        except AnyModelAuthError:
            print("Ключ не сработал, проверь и вставь заново.")
            continue
        except AnyModelUnavailableError as error:
            raise RuntimeError(f"AnyModel is unreachable: {error}") from error

        set_provider_secret(ANYMODEL_API_KEY_KEY, api_key)
        return "anymodel"
